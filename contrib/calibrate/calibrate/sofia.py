"""Sofia / DVRIP control for the XiongMai cam (CAM3, TCP 34567).

CAM3 runs XiongMai's "Sofia" firmware (the main binary on the device is
literally named ``Sofia``). Its ONVIF service on :8899 is a compatibility
shim: media profiles work, but ImagingService is partial — white balance
mode and gains both read back ``null``, so there is nothing to set. The
real imaging config only exists on the native protocol, **DVRIP on TCP
34567**, which is what this module speaks.

Wire format — a 20-byte header then a JSON body:

    offset 0   0xFF          head flag
    offset 1   0x00          version
    offset 4   uint32 LE     session id (0 until login)
    offset 8   uint32 LE     sequence number
    offset 14  uint16 LE     message id
    offset 16  uint32 LE     payload length
    offset 20  <payload>     JSON + b"\\x0a\\x00"

Auth is XiongMai's own truncated-MD5 hash (:func:`sofia_hash`), 8 chars.
Sanity check on the fleet: ``sofia_hash("")`` equals the 8-character token
embedded in CAM3's RTSP URL — that URL is hashing an *empty* password,
which is why the stream needs no credentials. (Compute it yourself and
compare against your own camera's URL.)

Config is a tree addressed by dotted name. ``get("Camera")`` returns the
whole camera subtree; ``get("Camera.Param")`` returns one node. Imaging
lives at:

* ``Camera.Param[ch]`` — ``WhiteBalance``, ``DayNightColor``, ``GainParam``
  (``AutoGain``/``Gain``), ``ExposureParam`` (``Level``/``LeastTime``/
  ``MostTime``), ``BLCMode``, ``AeSensitivity``, ``ElecLevel``.
* ``Camera.ParamEx[ch]`` — extended knobs (``Style``, ``ExposureTime``,
  ``BroadTrends``); manual WB gains live here on some builds.
* ``AVEnc.VideoColor.[ch]`` — brightness/contrast/saturation/hue trims,
  per time-section.

Field names drift between firmware builds, so **read the tree before
writing** rather than trusting the list above. Values are frequently
hex-in-a-string (``"0x00000000"``) even where an int would do; helpers
below normalise that.

Note the mask semantics: writes are shallow-merged by the camera, so a
set only needs the keys being changed — but a malformed key is accepted
silently and does nothing, exactly like the Dahua inactive-profile trap.
Always read back and compare.

**Measured on CAM3, 2026-07-24 (firmware V3.00.R02.000659SQ, build
2024-11-05) — what actually has authority over the picture:**

* ``AVEnc.VideoColor[0][sec].VideoColorParam`` is LIVE and strong.
  Brightness 50→80 moved frame mean luma 148.7→209.7; Saturation 43→95
  moved median saturation 0.097→0.210. Writes land in ~3 s. This is the
  usable correction surface on this camera.
* ``VideoColorParam.Whitebalance`` (0-255, sits in that same live node)
  is INERT. 40 / 128 / 200 all read back correctly and all measured
  identical to within the repeat-measurement noise (~0.6% on R/G).
* ``Camera.Param[0].WhiteBalance`` accepts 0-5, reads back correctly,
  and is inert too — the whole 0-5 sweep spanned 0.978-0.984 R/G, i.e.
  noise. A real preset change would move R/G by 10-20%.
* ``Camera.ParamEx[0].Style`` (typedefault / type1 / type2) shifts R/G
  about 2.7% — an ISP look profile with a slightly different colour
  matrix, not a white-balance control.
* ``Camera.WhiteBalance`` as a config name does not exist (Ret 607).

**Conclusion: CAM3 has no working white-balance control on any plane.**
ONVIF Imaging returns null for mode and gains; DVRIP stores WB values in
three separate places and the ISP honours none of them. Its auto-WB
cannot be pinned. Correct CAM3 on saturation/luma here and treat its
colour temperature as uncontrolled.
"""
from __future__ import annotations

import hashlib
import json
import socket
import struct
from typing import Any

DVRIP_PORT = 34567

# msg ids. The reply to a request comes back as id+1.
LOGIN = 1000
LOGOUT = 1002
KEEPALIVE = 1006
CONFIG_SET = 1040
CONFIG_GET = 1042
ABILITY_GET = 1360
SYSTEM_INFO = 1020

OK_CODES = {100, 515}

RET_MEANING = {
    100: "OK",
    101: "unknown error",
    102: "version not supported",
    103: "illegal request",
    104: "user already logged in",
    105: "user not logged in",
    106: "username or password wrong",
    107: "no permission",
    203: "password wrong",
    204: "user disabled",
    205: "user locked out",
    511: "upgrade started",
    512: "upgrade failed",
    514: "config failed",
    515: "OK (config applied)",
    607: "config name not supported on this firmware",
}

# Subtrees worth capturing for rollback. Anything the camera doesn't
# implement just returns a non-OK Ret and is recorded as such.
DUMP_NAMES = (
    "Camera",
    "AVEnc.VideoColor",
    "Simplify.Encode",
    "fVideo.OSDInfo",
    "General.Location",
    "General.General",
)


class SofiaError(RuntimeError):
    pass


def sofia_hash(password: str = "") -> str:
    """XiongMai's password hash: MD5, then fold byte pairs into 8 chars.

    Not a security measure — it is a lossy 8-character digest over a
    62-character alphabet. It is simply what the protocol expects on the
    wire in place of the plaintext.
    """
    digest = hashlib.md5(password.encode("utf-8")).digest()
    chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    return "".join(chars[(digest[i] + digest[i + 1]) % 62] for i in range(0, 16, 2))


def as_int(value: Any) -> int | None:
    """Sofia writes ints as ``"0x0000000A"`` about half the time."""
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value, 16) if value.lower().startswith("0x") else int(value)
        except ValueError:
            return None
    return None


class SofiaCam:
    def __init__(self, host: str, user: str = "admin", password: str = "",
                 port: int = DVRIP_PORT, timeout: float = 10.0) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.timeout = timeout
        self.session = 0
        self.alive_interval = 20
        self._seq = 0
        self._sock: socket.socket | None = None

    # ------------------------------------------------------------ plumbing

    def connect(self) -> None:
        sock = socket.create_connection((self.host, self.port), self.timeout)
        sock.settimeout(self.timeout)
        self._sock = sock

    def close(self) -> None:
        if self._sock is not None:
            try:
                if self.session:
                    self._send(LOGOUT, {"Name": "", "SessionID": self._sid()})
            except OSError:
                pass
            self._sock.close()
            self._sock = None

    def __enter__(self) -> "SofiaCam":
        self.connect()
        self.login()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _sid(self) -> str:
        return "0x%08X" % self.session

    def _recv_exactly(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))  # type: ignore[union-attr]
            if not chunk:
                raise SofiaError(f"{self.host}: connection closed mid-packet")
            buf += chunk
        return buf

    def _send(self, msg_id: int, payload: dict) -> dict:
        if self._sock is None:
            raise SofiaError(f"{self.host}: not connected")
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        header = struct.pack(
            "<BB2xII2xHI", 0xFF, 0x00, self.session, self._seq, msg_id, len(body) + 2
        )
        self._sock.sendall(header + body + b"\x0a\x00")
        self._seq += 1
        return self._recv_reply()

    def _recv_reply(self) -> dict:
        # Big configs can arrive as several DVRIP packets sharing one msg id
        # (header bytes 12/13 are total/current). Rather than trust those
        # counters, keep appending packets until the JSON parses.
        text = ""
        for _ in range(16):
            header = self._recv_exactly(20)
            if header[0] != 0xFF:
                raise SofiaError(f"{self.host}: bad header {header[:4]!r}")
            (length,) = struct.unpack("<I", header[16:20])
            text += self._recv_exactly(length).decode("utf-8", "replace")
            try:
                return json.loads(text.strip("\x00 \n\r\t"))
            except json.JSONDecodeError:
                continue
        raise SofiaError(f"{self.host}: reply never parsed as JSON ({len(text)} bytes)")

    @staticmethod
    def _check(reply: dict, what: str) -> dict:
        ret = reply.get("Ret")
        if ret not in OK_CODES:
            meaning = RET_MEANING.get(ret, "?")
            raise SofiaError(f"{what}: Ret={ret} ({meaning})")
        return reply

    # ------------------------------------------------------------- session

    def login(self) -> dict:
        reply = self._send(LOGIN, {
            "EncryptType": "MD5",
            "LoginType": "DVRIP-Web",
            "UserName": self.user,
            "PassWord": sofia_hash(self.password),
        })
        self._check(reply, f"{self.host}: login as {self.user!r}")
        self.session = int(reply["SessionID"], 16)
        self.alive_interval = reply.get("AliveInterval", 20)
        return reply

    def keepalive(self) -> None:
        """Call at least once per ``alive_interval`` seconds during a long
        servo loop or the camera drops the session."""
        self._send(KEEPALIVE, {"Name": "KeepAlive", "SessionID": self._sid()})

    def system_info(self) -> dict:
        reply = self._send(SYSTEM_INFO, {"Name": "SystemInfo", "SessionID": self._sid()})
        return reply.get("SystemInfo", reply)

    # -------------------------------------------------------------- config

    def get(self, name: str) -> Any:
        """Read a config node or subtree, e.g. ``Camera`` / ``Camera.Param``."""
        reply = self._send(CONFIG_GET, {"Name": name, "SessionID": self._sid()})
        self._check(reply, f"{self.host}: get {name}")
        return reply.get(name, reply)

    def set(self, name: str, value: Any) -> dict:
        """Write a config node. Shallow-merged by the camera: send only the
        keys you are changing. Unknown keys are accepted and ignored, so
        always read back."""
        reply = self._send(CONFIG_SET, {
            "Name": name, "SessionID": self._sid(), name: value,
        })
        return self._check(reply, f"{self.host}: set {name}")

    # --------------------------------------------------------------- knobs

    def camera_param(self, channel: int = 0) -> dict:
        param = self.get("Camera.Param")
        return param[channel] if isinstance(param, list) else param

    def set_camera_param(self, changes: dict, channel: int = 0) -> dict:
        """Patch ``Camera.Param[channel]``. The camera wants the list shape
        back, so send a list with only the changed keys in the slot."""
        slots: list[dict] = [{} for _ in range(channel + 1)]
        slots[channel] = changes
        return self.set("Camera.Param", slots)

    def video_color(self, channel: int = 0, section: int = 0) -> dict:
        """The live correction surface: Brightness / Contrast / Saturation /
        Hue / Acutance / Gain for one time-section (50 = neutral, 0-100).
        ``Whitebalance`` also lives here but has no effect — see module docs."""
        return self.get("AVEnc.VideoColor")[channel][section]["VideoColorParam"]

    def set_video_color(self, changes: dict, channel: int = 0,
                        section: int = 0) -> dict:
        """Patch the live trims, preserving every other field and section.

        Read-modify-write rather than a sparse patch: this node is nested
        two levels deep in lists and the camera does not merge reliably
        below the top level."""
        tree = self.get("AVEnc.VideoColor")
        tree[channel][section]["VideoColorParam"].update(changes)
        return self.set("AVEnc.VideoColor", tree)

    def white_balance(self, channel: int = 0) -> dict:
        p = self.camera_param(channel)
        keys = ("WhiteBalance", "DayNightColor", "IRCUTMode", "IrcutSwap",
                "GainParam", "ExposureParam", "ElecLevel", "AeSensitivity",
                "BLCMode", "EsShutter", "RejectFlicker")
        return {k: p[k] for k in keys if k in p}


def probe_login(host: str, user: str, candidates: list[str],
                port: int = DVRIP_PORT) -> tuple[SofiaCam, str]:
    """Try each candidate password, return the first that authenticates.

    CAM3's RTSP token is the hash of an empty password while ONVIF is
    documented with the fleet password, so which one DVRIP wants is an
    open question until asked.
    """
    errors = []
    for password in candidates:
        cam = SofiaCam(host, user, password, port=port)
        try:
            cam.connect()
            cam.login()
            return cam, password
        except (SofiaError, OSError) as exc:
            errors.append(f"{password!r}: {exc}")
            cam.close()
    raise SofiaError(f"{host}: no candidate password worked\n  " + "\n  ".join(errors))
