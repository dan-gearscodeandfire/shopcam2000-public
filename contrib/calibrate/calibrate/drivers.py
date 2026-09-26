"""One interface over five vendor control planes, with the traps built in.

Eight cameras, five transports: DirectShow ProcAmp over a subprocess (CAM1/5/8),
Dahua CGI on an *active profile* (CAM4/7), ONVIF Imaging (CAM2), Sofia dvrip on
*section 0* (CAM3), and Foscam CGI (CAM6). Every one of them lies in a different
way, so a caller that has to remember which is which will eventually forget.

WHAT THIS LAYER GUARANTEES, AND WHY EACH GUARANTEE EXISTS
----------------------------------------------------------
1. **Every write is verified by a live read-back**, and `set()` returns what the
   device actually holds — never what was asked for. A read-back is the only
   evidence; a setter's return value is a claim. The camera has lied four
   separate times on this rig, and `procamp set` specifically prints the value it
   *replaced*, which looks exactly like a confirmation.

2. **One key per call.** CAM2 has silently dropped keys from a batched 8-key
   write, applying some and ignoring others. There is no batch API here.

3. **Writes to a LOCKED camera raise.** CAM1 is the calibration reference and is
   locked by user decision; changing it invalidates every other camera's target.
   Framing (pan/tilt/zoom) is explicitly *not* locked — the user moves it freely
   and a gimbal move is not a regression.

4. **Known-dead knobs raise instead of failing silently.** CAM2 has no white
   balance control at all; CAM3's `Whitebalance` reads back perfectly and reaches
   nothing (a 136-unit swing moved the picture 0.02%). Both were discovered by
   servoing into them and believing the result.

5. **Known ceilings clamp loudly.** Amcrest `GainRed` saturates at ~45-49; past
   that it is noise for nothing.

6. **AUTO is never silently pinned to manual.** On CAM8 that is strictly worse and
   has been done twice: a manual CCT slider traces one line through (R/G, B/G)
   while auto computes independent R and B gains and reaches points the slider
   cannot. Pinning requires an explicit opt-in.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
No servo logic, no measurement, no opinion about what a value *should* be. This
layer moves knobs and tells the truth about what happened. Deciding what to write
belongs to `procamp_wb_servo` and the skill, where the measurement lives.
"""
from __future__ import annotations

import abc
import dataclasses
import json
import pathlib
import re
import subprocess
import sys
import time
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from calibrate.site_paths import PROCAMP_DIR, PROCAMP_PY, load_config  # noqa: E402

#: Loaded on first use rather than at import, so `--help` works before a
#: config.toml exists.
CFG: dict | None = None

#: Seconds to let a camera settle before the verifying read. The first reading
#: after a write is not the settled one -- CAM8 once read luma 176 immediately
#: after a write and settled to 150-155 over the following 20 s.
SETTLE_S = 1.5


class DriverError(RuntimeError):
    """A write did not land, or was refused before it could do harm."""


@dataclasses.dataclass
class Knob:
    """One controllable parameter, and what is known about it."""
    name: str
    lo: float | None = None
    hi: float | None = None
    #: Highest value worth writing, where the device saturates before its own
    #: limit. Beyond this the knob adds noise and nothing else.
    useful_hi: float | None = None
    dead: str = ""          # non-empty => the knob does not reach the ISP
    note: str = ""


class CameraDriver(abc.ABC):
    """Read and write one camera's image parameters. No servo, no opinions."""

    name: str = "?"
    transport: str = "?"
    locked: bool = False
    knobs: dict[str, Knob] = {}

    @abc.abstractmethod
    def read(self) -> dict[str, object]:
        """Live values, straight from the device."""

    @abc.abstractmethod
    def _write(self, knob: str, value: int) -> None:
        ...

    # ---------------------------------------------------------------- public

    def capabilities(self) -> dict[str, Knob]:
        return dict(self.knobs)

    def set(self, knob: str, value: int, *, allow_locked: bool = False) -> int:
        """Write ONE knob and return the value the device actually holds.

        Raises rather than writing when the knob is known dead or the camera is
        locked, because both of those have previously produced confident,
        wrong results rather than errors.
        """
        k = self.knobs.get(knob)
        if k is None:
            raise DriverError(f"{self.name}: no knob named {knob!r}. "
                              f"Known: {sorted(self.knobs)}")
        if k.dead:
            raise DriverError(f"{self.name}.{knob} is a DEAD knob: {k.dead} "
                              f"Writing it will read back correctly and change "
                              f"nothing. Do not servo it.")
        if self.locked and not allow_locked:
            raise DriverError(
                f"{self.name} is LOCKED (it is the calibration reference; every "
                f"other camera's target is derived from it). Pass "
                f"allow_locked=True only with a decision behind it. Framing "
                f"(pan/tilt/zoom) is NOT locked and is not written here.")
        if k.useful_hi is not None and value > k.useful_hi:
            raise DriverError(
                f"{self.name}.{knob}={value} is past its useful ceiling "
                f"({k.useful_hi:g}) -- it saturates there and buys only noise. "
                f"{k.note}")
        if k.lo is not None and not (k.lo <= value <= (k.hi if k.hi is not None else value)):
            raise DriverError(f"{self.name}.{knob}={value} outside range "
                              f"[{k.lo:g}, {k.hi:g}]")

        self._write(knob, int(value))
        time.sleep(SETTLE_S)
        got = self.read().get(knob)
        if got is None:
            raise DriverError(f"{self.name}: wrote {knob} but it is absent from "
                              f"the read-back -- cannot verify")
        return int(got)

    def verified_set(self, knob: str, value: int, **kw) -> tuple[int, bool]:
        """``(actual, landed_exactly)``. Devices quantise -- the OBSBOT accepted
        a request for Pan 24 and read back 23 -- so 'not exact' is not always
        'failed'. The caller decides which it is."""
        got = self.set(knob, value, **kw)
        return got, got == int(value)

    def snapshot(self) -> dict:
        return {"camera": self.name, "transport": self.transport,
                "locked": self.locked, "values": self.read()}

    def restore(self, snap: dict, *, allow_locked: bool = False) -> dict:
        """Put every knob back and RE-READ. A rollback can fail silently too.

        CAM3's restore once threw mid-connection and did not land, leaving the
        camera parked on a probe value. The abort path needs the same
        verification discipline as the forward path -- it runs exactly when
        nobody is watching.
        """
        want = snap.get("values", snap)
        results, skipped = {}, {}
        for knob, value in want.items():
            k = self.knobs.get(knob)
            if k is None:
                skipped[knob] = "not a knob on this driver"
                continue
            if k.dead:
                skipped[knob] = "dead knob -- nothing to restore"
                continue
            # Coerce: several transports report numbers as STRINGS (the Foscam
            # CGI parses out of XML, ONVIF returns floats). An earlier version
            # required int|float and silently dropped every Foscam knob, then
            # reported a clean restore having restored nothing.
            try:
                target = int(float(value))
            except (TypeError, ValueError):
                skipped[knob] = f"non-numeric value {value!r}"
                continue
            try:
                got = self.set(knob, target, allow_locked=allow_locked)
                results[knob] = {"wanted": target, "got": got, "ok": got == target}
            except DriverError as e:
                results[knob] = {"wanted": target, "error": str(e)[:160], "ok": False}

        bad = [k for k, v in results.items() if not v.get("ok")]
        # A restore that restored NOTHING is not clean, it is a no-op wearing a
        # success message. Only an empty snapshot may legitimately restore zero
        # knobs.
        restorable = [k for k in want if k in self.knobs and not self.knobs[k].dead]
        nothing_done = bool(restorable) and not results
        return {"camera": self.name, "restored": results, "skipped": skipped,
                "failed": bad, "nothing_restored": nothing_done,
                "clean": (not bad) and not nothing_done}


# --------------------------------------------------------------- ProcAmp/UVC

class ProcAmpDriver(CameraDriver):
    """CAM1 / CAM5 / CAM8 -- DirectShow ProcAmp over a subprocess.

    Runs `procamp.py` locally: since the 2026-07-28 move the code, Blue Iris and
    the USB cameras all live on the same host. It used to shell out over ssh,
    which after the move became a loop back to the same machine that hung until
    timeout and silently dropped three cameras out of every fleet snapshot.
    """
    transport = "DirectShow ProcAmp (UVC)"

    def __init__(self, name: str, device: str, locked: bool = False,
                 knobs: dict[str, Knob] | None = None,
                 extra_knobs: dict[str, Knob] | None = None):
        self.name, self.device, self.locked = name, device, locked
        self.knobs = knobs or {
            "Brightness": Knob("Brightness"),
            "Contrast": Knob("Contrast"),
            "Hue": Knob("Hue", note="rotates colour; INVISIBLE to grey ratios "
                                    "-- never validate it on neutrals"),
            "Saturation": Knob("Saturation"),
            "Sharpness": Knob("Sharpness"),
            "WhiteBalance": Knob("WhiteBalance", lo=2800, hi=6500,
                                 note="single CCT slider: traces ONE line "
                                      "through (R/G,B/G), only trades red "
                                      "against blue"),
            "BacklightCompensation": Knob("BacklightCompensation"),
            "Gain": Knob("Gain"),
        }
        # This default map is a HARDCODED SUBSET of what the device reports, so
        # a real knob can be invisible to set() while read() happily returns it
        # -- CAM8 reported Gamma=100 for months and its setter had never heard
        # of it. Per-device additions go here; ranges come from the device's own
        # `procamp report`, never from guesswork.
        if extra_knobs:
            self.knobs = {**self.knobs, **extra_knobs}

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([str(PROCAMP_PY), "procamp.py", "--device",
                               self.device, *args],
                              cwd=str(PROCAMP_DIR), capture_output=True,
                              text=True, timeout=90)

    def _report(self) -> dict:
        r = self._run("report")
        if r.returncode != 0 or not r.stdout.strip():
            raise DriverError(f"{self.name}: procamp report failed: "
                              f"{(r.stderr or r.stdout)[:200]}")
        return json.loads(r.stdout)

    def read(self) -> dict[str, object]:
        d = self._report()
        out: dict[str, object] = {}
        for section in ("procamp", "camctrl"):
            for k, v in d.get(section, {}).items():
                out[k] = v["value"]
        return out

    def modes(self) -> dict[str, str]:
        """Per-knob auto/manual. **Check this before servoing anything** -- an
        AUTO knob ignores writes AND drifts between takes."""
        d = self._report()
        return {k: str(v.get("mode", "?"))
                for section in ("procamp", "camctrl")
                for k, v in d.get(section, {}).items()}

    def _write(self, knob: str, value: int) -> None:
        self._run("set", "--prop", knob, "--value", str(value))

    def set_auto(self, knob: str) -> str:
        """Hand a knob back to the camera. Sometimes the right answer: on CAM8,
        auto-WB reaches R/G 0.893 / B/G 1.056, which no manual value can occupy
        (best manual: 0.845 / 0.974 -- worse on BOTH axes)."""
        self._run("set", "--prop", knob, "--auto")
        time.sleep(SETTLE_S)
        return self.modes().get(knob, "?")


# ---------------------------------------------------------------- Dahua CGI

class DahuaDriver(CameraDriver):
    """CAM4 / CAM7 -- Amcrest/Dahua CGI, on the ACTIVE profile.

    The profile trap has bitten three times: getters read `[0][0]` while the
    cameras run profile 2, so every recorded value described a store the camera
    was not using and `set_compensation` was a silent no-op for weeks. The
    profile is resolved on every call here.

    Further, `active_profile()` reads a STATIC config field. With
    `VideoInMode.Mode = 0` the camera picks its own profile by light level, so
    the reported number is an assumption, not an observation -- and the
    Amcrests self-switch to black-and-white when the shop dims.
    """
    transport = "Dahua CGI :80 (active profile)"

    def __init__(self, name: str, host: str, user: str, password: str):
        self.name, self.host = name, host
        self._user, self._password = user, password
        self.knobs = {
            "GainRed": Knob("GainRed", lo=0, hi=100, useful_hi=49,
                            note="saturates ~45-49 ON CAM7 (CAM4 is linear through 49; the cap is kept "
                                 "fleet-wide as a safe default); the gains renormalise, so "
                                 "past that it is pure noise. The residual "
                                 "-2.5% red is a firmware ceiling -> edit tint."),
            "GainBlue": Knob("GainBlue", lo=0, hi=100),
            "Compensation": Knob("Compensation", lo=0, hi=100,
                                 note="the AE TARGET and the only real exposure "
                                      "lever. On CAM7 50->65 halved crush in one "
                                      "write; 65->80 added nothing. Lowering it "
                                      "makes crush WORSE."),
            "WideDynamicRange": Knob("WideDynamicRange", lo=0, hi=100,
                                     note="LEAVE AT 0. 0->16 RAISED crush "
                                          "19.7%->25.3%."),
        }

    def _cam(self):
        from calibrate.dahua import DahuaCam
        return DahuaCam(self.host, self._user, self._password)

    def read(self) -> dict[str, object]:
        c = self._cam()
        try:
            p = c.active_profile()
            wb, ex = c.white_balance(), c.exposure()
            short = lambda d: {k.split(".")[-1]: v for k, v in d.items()
                               if f"[0][{p}]" in k}
            out: dict[str, object] = {}
            out.update(short(wb))
            out.update(short(ex))
            out["_active_profile"] = p
            out["_profile_is_assumed"] = True
            return out
        finally:
            c.close()

    def _write(self, knob: str, value: int) -> None:
        c = self._cam()
        try:
            p = c.active_profile()
            table = {"GainRed": "VideoInWhiteBalance", "GainBlue": "VideoInWhiteBalance",
                     "Compensation": "VideoInExposure",
                     "WideDynamicRange": "VideoInBacklight"}[knob]
            c.set_config({f"{table}[0][{p}].{knob}": value})
        finally:
            c.close()


# ------------------------------------------------------------ ONVIF Imaging

class OnvifLevelsDriver(CameraDriver):
    """CAM2 -- Amcrest ASH21 over ONVIF Imaging. Levels only.

    This camera has **no white balance control at all**, and its gains
    renormalise, so its -15.7% R/G is a firmware ceiling that goes to the edit.
    Its worst defect is optical anyway: 3.2x softer than CAM1 on edge strength,
    which no setting reaches.
    """
    transport = "ONVIF Imaging :80"

    def __init__(self, name: str, host: str, port: int = 80):
        self.name, self.host, self.port = name, host, port
        self._token: str | None = None
        self.knobs = {
            "Brightness": Knob("Brightness", lo=0, hi=100,
                               note="response is NOT linear: 1.97 luma/unit at "
                                    "B33->20 but 4.26 at B20->8. Interpolate "
                                    "between measured points, never extrapolate."),
            "ColorSaturation": Knob("ColorSaturation", lo=0, hi=100),
            "Contrast": Knob("Contrast", lo=0, hi=100),
            "Sharpness": Knob("Sharpness", lo=0, hi=100),
            "WhiteBalance": Knob("WhiteBalance",
                                 dead="the ASH21 exposes no WB control."),
        }

    def _tok(self) -> str:
        from calibrate import onvif_imaging as oi
        if self._token is None:
            self._token = oi.video_source_token(self.host, self.port)
        return self._token

    def read(self) -> dict[str, object]:
        from calibrate import onvif_imaging as oi
        return dict(oi.read_levels(self.host, self.port, self._tok()))

    def _write(self, knob: str, value: int) -> None:
        from calibrate import onvif_imaging as oi
        # One key per call, deliberately: a batched multi-key write to this
        # camera has silently applied some keys and ignored others.
        oi.set_levels(self.host, self.port, self._tok(), **{knob: float(value)})


# ---------------------------------------------------------------- Sofia dvrip

class SofiaDriver(CameraDriver):
    """CAM3 -- XiongMai over Sofia dvrip :34567, SECTION 0 ONLY.

    `AVEnc.VideoColor[0]` has two sections and **only section 0 is live** --
    proven by writing Hue to section 0 alone and watching the picture rotate
    while section 1 kept its old values. Section 1 still holds stale numbers.
    """
    transport = "Sofia dvrip :34567 (section 0)"

    def __init__(self, name: str, host: str, user: str = "admin", password: str = ""):
        self.name, self.host = name, host
        self._user, self._password = user, password
        self.knobs = {
            "Brightness": Knob("Brightness", lo=0, hi=100),
            "Contrast": Knob("Contrast", lo=0, hi=100),
            "Saturation": Knob("Saturation", lo=0, hi=100,
                               note="~0.013 chroma/unit, monotonic, measured on "
                                    "the red patch. Cannot move a neutral."),
            "Hue": Knob("Hue", lo=0, hi=100,
                        note="pure rotation: 8 units ~ 4 deg, chroma moves <=2.2%. "
                             "Score it on colour-patch ANGLE, never on greys."),
            "Whitebalance": Knob(
                "Whitebalance",
                dead="PROVEN NULL 2026-07-30: 128 -> 60 -> 196 (a 136-unit "
                     "swing) moved R/G 0.02% and B/G 0.06% while reading back "
                     "the written value each time. Its ONVIF WB is dead too."),
        }

    def _cam(self):
        from calibrate.sofia import SofiaCam
        return SofiaCam(self.host, self._user, self._password)

    def read(self) -> dict[str, object]:
        with self._cam() as c:
            c.login()
            return dict(c.video_color(section=0))

    def _write(self, knob: str, value: int) -> None:
        with self._cam() as c:
            c.login()
            c.set_video_color({knob: int(value)}, section=0)


# --------------------------------------------------------------- Foscam CGI

class FoscamDriver(CameraDriver):
    """CAM6 -- Foscam R2C CGI :88. The roamer.

    Excluded from calibration by design: it has no fixed position, sees neither
    station, and its WB stays AUTO. Included here so the fleet can be *read*
    completely -- a snapshot with a hole in it looks like a snapshot.
    """
    transport = "Foscam CGI :88"

    #: Parameter names the firmware actually accepts, where they differ from the
    #: property name. Getting one wrong does NOT error -- see `_write`.
    PARAM_TYPOS = {"contrast": "constrast"}

    def __init__(self, name: str, host: str, port: int, user: str, password: str):
        self.name, self.host, self.port = name, host, port
        self._user, self._password = user, password
        self.knobs = {k: Knob(k, lo=0, hi=100) for k in
                      ("brightness", "contrast", "hue", "saturation", "sharpness")}
        self.knobs["contrast"].note = ("firmware expects the param spelled "
                                       "`constrast`; the correct spelling is "
                                       "accepted and ignored")

    def _cgi(self, cmd: str, **params) -> str:
        import httpx
        base = f"http://{self.host}:{self.port}/cgi-bin/CGIProxy.fcgi"
        return httpx.get(base, params={"cmd": cmd, "usr": self._user,
                                       "pwd": self._password, **params},
                         timeout=10).text

    def read(self) -> dict[str, object]:
        t = self._cgi("getImageSetting")
        return {k: v for k, v in re.findall(r"<(\w+)>([^<]*)</\1>", t)
                if k != "result"}

    def _write(self, knob: str, value: int) -> None:
        # 🔴 There is NO `setImageSetting` on this firmware, despite
        # `getImageSetting` existing. It returns result=-3 -- and so does a
        # deliberately bogus command name, which is how -3 was identified as
        # "unknown command" rather than "bad arguments". Writes go through
        # per-property setters: `setBrightness&brightness=N`.
        #
        # 🔴 And the parameter name is load-bearing in the worst way:
        # `setBrightness&value=58` returns **result=0** and sets brightness to
        # **0**, because the expected `brightness` param was absent and defaulted.
        # A wrong param name here does not error -- it zeroes the knob and
        # reports success. That is how CAM6 ended up parked on contrast 0 while
        # every response said ok. `set()` re-reads after every write, which is
        # the only reason it was caught.
        cmd = "set" + knob[:1].upper() + knob[1:]
        # 🔴 `setContrast` expects the parameter spelled **`constrast`** -- a
        # typo in the camera's own firmware. Spelled correctly it returns
        # result=0 and does nothing; misspelled it works. Verified 2026-07-30
        # after the correct spelling left CAM6 parked on contrast 0.
        param = self.PARAM_TYPOS.get(knob, knob)
        resp = self._cgi(cmd, **{param: int(value)})
        m = re.search(r"<result>(-?\d+)</result>", resp)
        if m and m.group(1) != "0":
            raise DriverError(f"{self.name}: {cmd} returned result={m.group(1)} "
                              f"(0=ok, -3=unknown command). Nothing was written.")


# ------------------------------------------------------------------ registry

def fleet(cfg: dict | None = None) -> dict[str, CameraDriver]:
    """Every scene camera, keyed by name. CAM9 is a desktop capture: no image
    settings, deliberately absent."""
    c = (cfg or CFG or load_config())["cameras"]
    am, x, f = c["amcrest"], c["xiongmai"], c["foscam"]
    return {
        "CAM1": ProcAmpDriver("CAM1", "OBSBOT Tiny 4K Camera", locked=True),
        "CAM2": OnvifLevelsDriver("CAM2", c["ash21"]["host"]),
        "CAM3": SofiaDriver("CAM3", x["host"], x.get("user", "admin"),
                            x.get("password", "")),
        "CAM4": DahuaDriver("CAM4", am["hosts"]["CAM4"], am["user"], am["password"]),
        "CAM5": ProcAmpDriver("CAM5", "OBSBOT Tiny Camera"),
        "CAM6": FoscamDriver("CAM6", f["host"], f["port"], f["user"], f["password"]),
        "CAM7": DahuaDriver("CAM7", am["hosts"]["CAM7"], am["user"], am["password"]),
        # CAM8 reports Gamma and Exposure as manual knobs with real ranges, but
        # neither was in the default map, so the driver could not write either
        # -- including the exposure pin that is part of this camera's own
        # last-known-good (Exp -7). Ranges read off `procamp report` 2026-08-31.
        # `Roll` is deliberately still excluded: its range is [0, 0], a dead
        # knob, not a gap.
        "CAM8": ProcAmpDriver("CAM8", "USB", extra_knobs={
            "Gamma": Knob("Gamma", lo=72, hi=500,
                          note="TONE knob, and the one that matters when this "
                               "camera crushes; 100 = default, NOT the middle "
                               "of its range"),
            "Exposure": Knob("Exposure", lo=-13, hi=-1,
                             note="log2 seconds; LESS NEGATIVE = brighter. "
                                  "Pinned manual at -7 (2026-08-07) -- it is "
                                  "part of the LKG, do not hand it back to "
                                  "auto without a measurement"),
        }),
    }


def snapshot_fleet(drivers: dict[str, CameraDriver] | None = None) -> dict:
    """Read every camera. **Unreachable cameras are recorded as errors, never
    omitted** -- an invisible gap reads as a clean run, which is how CAM1, CAM5
    and CAM8 quietly vanished from every baseline for a week."""
    drivers = drivers or fleet()
    out: dict[str, object] = {}
    for name, d in drivers.items():
        try:
            out[name] = d.snapshot()
        except Exception as e:                       # noqa: BLE001
            out[name] = {"camera": name, "transport": d.transport,
                         "ERROR": f"{type(e).__name__}: {e}"[:200]}
    return out
