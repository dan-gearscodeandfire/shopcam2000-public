"""Dahua/Amcrest CGI control (CAM2, CAM4, CAM7).

Everything goes over ``configManager.cgi`` on :80 with digest auth. Reads
return ``table.Name[..].Key=value`` lines; writes are ``action=setConfig``
with the same dotted keys as query params. Settings persist on the camera
(they survive reboot — this is the camera's own config store).

Knob map (verified against the fleet 2026-07-24; older 2017 firmware on CAM4
exposes the same tables):

* ``VideoInWhiteBalance[0][0]`` — ``Mode`` (Auto/Custom/...), ``GainRed``,
  ``GainBlue`` (0-100).
* ``VideoInExposure[0][0]`` — ``Mode`` (0=auto ranges), ``Value1``/``Value2``
  (shutter ms range), ``Gain``/``GainMin``/``GainMax``, ``Compensation``
  (0-100, AE target bias, 50 = neutral).
* ``VideoColor[0][0]`` — Brightness/Contrast/Saturation/Gamma post-processing
  trims (50 = neutral).
"""
from __future__ import annotations

import pathlib
import time

import httpx


class DahuaError(RuntimeError):
    pass


class DahuaCam:
    def __init__(self, host: str, user: str, password: str, port: int = 80,
                 timeout: float = 10.0) -> None:
        self.host = host
        self._client = httpx.Client(
            base_url=f"http://{host}:{port}",
            auth=httpx.DigestAuth(user, password),
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    def _cgi(self, params: dict[str, str]) -> str:
        r = self._client.get("/cgi-bin/configManager.cgi", params=params)
        if r.status_code != 200:
            raise DahuaError(f"{self.host}: HTTP {r.status_code} for {params}")
        return r.text

    def get_config(self, name: str) -> dict[str, str]:
        """``table.X=y`` lines -> {'X': 'y'} (the ``table.`` prefix stripped)."""
        text = self._cgi({"action": "getConfig", "name": name})
        if text.startswith("Error"):
            raise DahuaError(f"{self.host}: {name}: {text.strip()}")
        out: dict[str, str] = {}
        for line in text.splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                out[k.removeprefix("table.")] = v.strip()
        return out

    def set_config(self, kv: dict[str, str | int | float]) -> None:
        params = {"action": "setConfig"}
        params.update({k: str(v) for k, v in kv.items()})
        text = self._cgi(params)
        if "OK" not in text:
            raise DahuaError(f"{self.host}: setConfig rejected: {text.strip()!r}")

    # ------------------------------------------------------------- knobs

    def white_balance(self) -> dict[str, str]:
        """WB config for the ACTIVE profile.

        This used to hardcode `[0][0]`, the day profile. All three Amcrests run
        profile 2, so every value it returned was from an inactive store that
        the camera was not using — reads looked plausible and described nothing.
        `set_white_balance` has always written the active profile, so the getter
        and the setter were addressing different registers.
        """
        p = self.active_profile()
        return {k: v for k, v in self.get_config("VideoInWhiteBalance").items()
                if f"[0][{p}]" in k}

    def active_profile(self) -> int:
        """Which config profile the camera is running: 0=day, 1=night,
        2=normal. Writes to any other profile store fine and do nothing —
        the null-response trap that burned the first CAM4 attempt
        (2026-07-24: CAM4 runs profile 2)."""
        mode = self.get_config("VideoInMode")
        return int(mode.get("VideoInMode[0].Config[0]", "0"))

    def set_white_balance(self, gain_red: int, gain_blue: int,
                          profile: int | None = None) -> None:
        """Pin manual WB on the ACTIVE profile. Gains are 0-100 and HOT
        (~3%/unit B/G on CAM4). Mode must be "Manual" — "Custom" is the
        colour-temperature-slider mode and ignores the gains."""
        p = self.active_profile() if profile is None else profile
        self.set_config({
            f"VideoInWhiteBalance[0][{p}].Mode": "Manual",
            f"VideoInWhiteBalance[0][{p}].GainRed": int(gain_red),
            f"VideoInWhiteBalance[0][{p}].GainBlue": int(gain_blue),
        })

    def exposure(self) -> dict[str, str]:
        """Exposure config for the ACTIVE profile.

        Same trap as `white_balance` had (fixed 2026-07-24) and it survived here
        until 2026-07-25: this hardcoded `[0][0]`, so on a camera running
        profile 2 it reported a store the camera does not use. CAM7 read
        "16.67 ms" from `[0][0]` while actually running 8.33 ms on `[0][2]` —
        and any luma maths built on that number is wrong by 2x.
        """
        p = self.active_profile()
        return {k: v for k, v in self.get_config("VideoInExposure").items()
                if f"[0][{p}]" in k}

    def set_compensation(self, value: int, profile: int | None = None) -> None:
        """AE target bias, 0-100 (50 = neutral). Keeps auto shutter/gain but
        moves the brightness the AE aims for — the gentle exposure knob.

        Writes the ACTIVE profile: this used to write `[0][0]`, which stores
        fine and does nothing on a profile-2 camera — a silent no-op that looks
        like "the knob is inert".
        """
        p = self.active_profile() if profile is None else profile
        self.set_config({f"VideoInExposure[0][{p}].Compensation": int(value)})

    def dump(self, out_dir: pathlib.Path, cam_label: str) -> pathlib.Path:
        """Rollback snapshot of every imaging-relevant table."""
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d-%H%M%S")
        path = out_dir / f"rollback-{cam_label}-{stamp}.txt"
        chunks = []
        for name in ("VideoInWhiteBalance", "VideoInExposure", "VideoColor",
                     "VideoInOptions", "VideoInDayNight"):
            try:
                text = self._cgi({"action": "getConfig", "name": name})
            except DahuaError as exc:
                text = f"<{exc}>"
            chunks.append(f"### {name}\n{text.strip()}\n")
        path.write_text("\n".join(chunks), encoding="utf-8")
        return path
