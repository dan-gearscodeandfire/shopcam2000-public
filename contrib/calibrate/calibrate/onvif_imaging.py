"""Minimal ONVIF Imaging client (WS-Security UsernameToken digest).

The driver of last resort for cameras whose CGI imaging writes are stubs —
CAM4's 2017 Amcrest firmware accepts configManager WB gain writes and silently
drops them; its ONVIF service is the path that actually reaches the ISP.
Also serves CAM3 (the XiongMai), which was pinned via ONVIF on :8899.

    python -m calibrate.onvif_imaging HOST PORT get
    python -m calibrate.onvif_imaging HOST PORT set-wb-manual CRGAIN CBGAIN
    python -m calibrate.onvif_imaging HOST PORT set-wb-auto

Credentials come from ``config.toml`` (gitignored), matched to the host, or
from ``SHOPCAM_ONVIF_USER`` / ``SHOPCAM_ONVIF_PASSWORD`` which override it.
"""
from __future__ import annotations

import base64
import functools
import hashlib
import os
import pathlib
import re
import sys
import tomllib
from datetime import datetime, timezone

import httpx

ROOT = pathlib.Path(__file__).resolve().parents[1]


@functools.cache
def credentials(host: str) -> tuple[str, str]:
    """The user/password for ``host``, from config.toml or the environment.

    Every camera this module drives is already described in config.toml under
    ``[cameras.*]`` — the Amcrests by their ``hosts`` table, the XiongMai by
    its ``host`` — so the address is enough to pick the right pair. Hosts that
    appear in neither fall back to the Amcrest credentials, which is what the
    fleet's non-OBSBOT cameras share.
    """
    env_user, env_password = os.getenv("SHOPCAM_ONVIF_USER"), os.getenv(
        "SHOPCAM_ONVIF_PASSWORD")
    if env_user is not None and env_password is not None:
        return env_user, env_password

    from .site_paths import config_path
    path = config_path()
    if not path.exists():
        raise RuntimeError(
            f"no credentials: {path} is missing and SHOPCAM_ONVIF_USER/"
            f"SHOPCAM_ONVIF_PASSWORD are not set. Copy config.example.toml.")
    cams = tomllib.loads(path.read_text(encoding="utf-8-sig")).get("cameras", {})

    xiongmai = cams.get("xiongmai", {})
    if host == xiongmai.get("host"):
        return xiongmai.get("user", "admin"), xiongmai.get("password", "")
    ash21 = cams.get("ash21", {})
    if host == ash21.get("host"):
        return ash21.get("user", "admin"), ash21["password"]
    amcrest = cams.get("amcrest", {})
    return amcrest.get("user", "admin"), amcrest["password"]


def security_header(user: str, password: str) -> str:
    nonce = os.urandom(16)
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    digest = base64.b64encode(
        hashlib.sha1(nonce + created.encode() + password.encode()).digest()).decode()
    return f"""<s:Header><Security s:mustUnderstand="1"
 xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">
<UsernameToken><Username>{user}</Username>
<Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">{digest}</Password>
<Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">{base64.b64encode(nonce).decode()}</Nonce>
<Created xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">{created}</Created>
</UsernameToken></Security></s:Header>"""


def soap(host: str, port: int, path: str, body: str) -> str:
    user, password = credentials(host)
    envelope = (f'<?xml version="1.0" encoding="UTF-8"?>'
                f'<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">'
                f'{security_header(user, password)}'
                f'<s:Body>{body}</s:Body></s:Envelope>')
    r = httpx.post(f"http://{host}:{port}{path}", content=envelope,
                   headers={"Content-Type": "application/soap+xml"}, timeout=10)
    r.raise_for_status()
    return r.text


def video_source_token(host: str, port: int) -> str:
    text = soap(host, port, "/onvif/media_service",
                '<GetVideoSources xmlns="http://www.onvif.org/ver10/media/wsdl"/>')
    m = re.search(r'VideoSources token="([^"]+)"', text)
    if not m:
        raise RuntimeError(f"no video source token in: {text[:500]}")
    return m.group(1)


def get_imaging(host: str, port: int, token: str) -> str:
    return soap(host, port, "/onvif/imaging_service",
                f'<GetImagingSettings xmlns="http://www.onvif.org/ver20/imaging/wsdl">'
                f'<VideoSourceToken>{token}</VideoSourceToken></GetImagingSettings>')


def set_wb(host: str, port: int, token: str, mode: str,
           cr: float | None = None, cb: float | None = None) -> str:
    gains = ""
    if mode == "MANUAL":
        gains = f"<tt:CrGain>{cr}</tt:CrGain><tt:CbGain>{cb}</tt:CbGain>"
    body = (f'<SetImagingSettings xmlns="http://www.onvif.org/ver20/imaging/wsdl" '
            f'xmlns:tt="http://www.onvif.org/ver10/schema">'
            f'<VideoSourceToken>{token}</VideoSourceToken>'
            f'<ImagingSettings><tt:WhiteBalance><tt:Mode>{mode}</tt:Mode>{gains}'
            f'</tt:WhiteBalance></ImagingSettings>'
            f'<ForcePersistence>true</ForcePersistence></SetImagingSettings>')
    return soap(host, port, "/onvif/imaging_service", body)


def get_settings_xml(host: str, port: int, token: str) -> str:
    """The device's own <timg:ImagingSettings> block, verbatim.

    Used both as the rollback dump and as the base for a full-object write.
    """
    text = get_imaging(host, port, token)
    m = re.search(r"<timg:ImagingSettings>(.*?)</timg:ImagingSettings>", text, re.S)
    if not m:
        raise RuntimeError(f"no ImagingSettings in response: {text[:500]}")
    return m.group(1)


def set_wb_full(host: str, port: int, token: str, mode: str,
                cr: float | None = None, cb: float | None = None) -> str:
    """Set white balance by echoing back the FULL settings object.

    ``set_wb`` sends an ImagingSettings containing only <WhiteBalance>. Plenty
    of ONVIF stacks treat SetImagingSettings as a whole-object write and reset
    everything the caller omitted — which on CAM3 would silently drop the
    manual exposure pin (Mode/ExposureTime/Gain) applied 2026-07-24. Read the
    current object, swap just the WhiteBalance element, write it back.
    """
    body_inner = get_settings_xml(host, port, token)
    gains = (f"<tt:CrGain>{cr}</tt:CrGain><tt:CbGain>{cb}</tt:CbGain>"
             if mode == "MANUAL" else "")
    new_wb = f"<tt:WhiteBalance><tt:Mode>{mode}</tt:Mode>{gains}</tt:WhiteBalance>"
    if "<tt:WhiteBalance>" in body_inner:
        body_inner = re.sub(r"<tt:WhiteBalance>.*?</tt:WhiteBalance>", new_wb,
                            body_inner, flags=re.S)
    else:
        body_inner += new_wb
    body = (f'<SetImagingSettings xmlns="http://www.onvif.org/ver20/imaging/wsdl" '
            f'xmlns:tt="http://www.onvif.org/ver10/schema">'
            f'<VideoSourceToken>{token}</VideoSourceToken>'
            f'<ImagingSettings>{body_inner}</ImagingSettings>'
            f'<ForcePersistence>true</ForcePersistence></SetImagingSettings>')
    return soap(host, port, "/onvif/imaging_service", body)


LEVELS = ("Brightness", "ColorSaturation", "Contrast", "Sharpness")


def set_levels(host: str, port: int, token: str, **levels: float) -> str:
    """Set Brightness / ColorSaturation / Contrast / Sharpness, full-object.

    These four are the ONLY imaging levers the Amcrest ASH21 (CAM2) exposes —
    it has no WB, exposure, gain or WDR control at all. Written the same way as
    ``set_wb_full``: read the device's own settings object back, substitute the
    named elements, write the whole thing. A partial ImagingSettings risks the
    stack resetting everything omitted.

    ⚠️ The ASH21 is in the "returns OK and silently drops the write" family
    (its ONVIF Rotate does exactly that). ALWAYS read back — this function's
    return value proves nothing.
    """
    unknown = set(levels) - set(LEVELS)
    if unknown:
        raise ValueError(f"not imaging levels: {sorted(unknown)}")
    inner = get_settings_xml(host, port, token)
    for key, val in levels.items():
        el = f"<tt:{key}>{float(val):.6f}</tt:{key}>"
        if f"<tt:{key}>" in inner:
            inner = re.sub(rf"<tt:{key}>.*?</tt:{key}>", el, inner, flags=re.S)
        else:
            inner = el + inner
    body = (f'<SetImagingSettings xmlns="http://www.onvif.org/ver20/imaging/wsdl" '
            f'xmlns:tt="http://www.onvif.org/ver10/schema">'
            f'<VideoSourceToken>{token}</VideoSourceToken>'
            f'<ImagingSettings>{inner}</ImagingSettings>'
            f'<ForcePersistence>true</ForcePersistence></SetImagingSettings>')
    return soap(host, port, "/onvif/imaging_service", body)


def read_levels(host: str, port: int, token: str) -> dict[str, float]:
    xml = get_settings_xml(host, port, token)
    out = {}
    for key in LEVELS:
        m = re.search(rf"<tt:{key}>([\d.]+)</tt:{key}>", xml)
        if m:
            out[key] = float(m.group(1))
    return out


def main() -> None:
    host, port, cmd = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    token = video_source_token(host, port)
    print(f"video source token: {token}")
    if cmd == "get":
        text = get_imaging(host, port, token)
        wb = re.search(r"<tt:WhiteBalance>.*?</tt:WhiteBalance>", text, re.S)
        print(wb.group(0) if wb else text)
    elif cmd == "set-wb-manual":
        print(set_wb(host, port, token, "MANUAL",
                     float(sys.argv[4]), float(sys.argv[5]))[:400])
    elif cmd == "set-wb-auto":
        print(set_wb(host, port, token, "AUTO")[:400])
    elif cmd == "dump":
        print(get_settings_xml(host, port, token))
    elif cmd == "set-wb-manual-full":
        print(set_wb_full(host, port, token, "MANUAL",
                          float(sys.argv[4]), float(sys.argv[5]))[:400])
    elif cmd == "set-wb-auto-full":
        print(set_wb_full(host, port, token, "AUTO")[:400])
    elif cmd == "levels":
        print(read_levels(host, port, token))
    elif cmd == "set-levels":
        # set-levels Brightness=10 ColorSaturation=41 ...
        kv = dict(a.split("=", 1) for a in sys.argv[4:])
        set_levels(host, port, token, **{k: float(v) for k, v in kv.items()})
        print("read-back:", read_levels(host, port, token))


if __name__ == "__main__":
    main()
