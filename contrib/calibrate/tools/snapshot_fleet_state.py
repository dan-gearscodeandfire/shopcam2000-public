"""Read the LIVE image settings of every camera and write one consolidated
last-known-good snapshot.

    python tools/snapshot_fleet_state.py [OUT.json]

Reads from the device in every case -- never from a note -- because the whole
point of a last-known-good file is that it describes what the rig is actually
running. Cameras that cannot be reached are recorded as errors rather than
silently omitted, so a gap in the snapshot is visible instead of invisible.
"""
from __future__ import annotations
import datetime
import json, os, pathlib, subprocess, sys, tomllib, re

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from calibrate.site_paths import PROCAMP_DIR, PROCAMP_PY, load_config  # noqa: E402

# `--help` must never need a config (or touch a camera) - see main().
_HELP = len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help", "/?")
CFG = {} if _HELP else load_config()


def _on_blue_iris_host() -> bool:
    """Are we running ON the machine that holds the USB cameras?

    Since the 2026-07-28 move the answer is yes: the code, Blue Iris and both
    OBSBOTs all live on the Blue Iris PC. Before the move this ran from another box and
    had to reach procamp over ssh.
    """
    return str(CFG["blue_iris"]["host"]) in ("127.0.0.1", "localhost", "::1")


def obsbot(device: str) -> dict:
    """CAM1/CAM5/CAM8 -- DirectShow ProcAmp, locally or over ssh as appropriate.

    This used to ALWAYS shell out `ssh <blue-iris-pc>`. After the 2026-07-28 code move
    that became a loop back to this same machine, which hangs until the 90 s
    timeout -- so CAM1, CAM5 and CAM8 silently dropped out of every fleet
    snapshot, including the rollback baseline, and CAM1 is the calibration
    reference. It was only noticed because this module records an unreachable
    camera as an ERROR instead of omitting it; a snapshot quietly holding five
    cameras would have looked complete.

    NOTE for the ssh path: plain double quotes inside the bash single-quoted
    command are what work here; escaped and doubled quotes both break argparse
    on the far side.
    """
    if _on_blue_iris_host():
        r = subprocess.run([str(PROCAMP_PY), "procamp.py", "--device", device,
                            "report"],
                           cwd=str(PROCAMP_DIR), capture_output=True,
                           text=True, timeout=90)
    else:
        # The USB cameras are on another machine: run procamp.py there.
        host = os.environ.get("SHOPCAM_PROCAMP_SSH", str(CFG["blue_iris"]["host"]))
        remote = os.environ.get("SHOPCAM_PROCAMP_REMOTE_DIR",
                                r"C:\shopcam2000\contrib\calibrate\calibrate\remote")
        cmd = (f'cd {remote} && python procamp.py '
               f'--device "{device}" report')
        r = subprocess.run(["ssh", host, cmd], capture_output=True,
                           text=True, timeout=90)
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError(f"procamp failed (rc={r.returncode}): "
                           f"{(r.stderr or r.stdout).strip()[:160]}")
    d = json.loads(r.stdout)
    # Keep mode and range, not just the value. CAM8's white balance is AUTO by
    # design (manual was tried and reverted as worse), so an auto/manual flip has
    # to be visible here; and ranges are per-camera -- CAM1's
    # BacklightCompensation is 0..18, not 0..100.
    keep = lambda v: {"value": v["value"], "mode": v.get("mode"),
                      "range": v.get("range")}
    return {"procamp": {k: keep(v) for k, v in d["procamp"].items()},
            "camctrl": {k: keep(v) for k, v in d.get("camctrl", {}).items()}}


def amcrest(name: str) -> dict:
    from calibrate.dahua import DahuaCam
    am = CFG["cameras"]["amcrest"]
    c = DahuaCam(am["hosts"][name], am["user"], am["password"])
    try:
        p = c.active_profile()
        short = lambda d: {k.split(".")[-1]: v for k, v in d.items() if f"[0][{p}]" in k}
        dn = c.get_config("VideoInDayNight")
        # Every imaging section on ALL THREE profiles. These cameras self-switch by
        # light level and active_profile() is blind to it, so capturing only the
        # active profile cannot reveal that the others disagree.
        all_profiles = {}
        # 2026-08-06: was only the three colour/exposure/WB sections, so a profile
        # divergence in Lighting (the IR illuminator: Auto/Off/Auto on CAM7),
        # VideoInBacklight (WideDynamicRange 19/50/7) or VideoInSharpness was
        # INVISIBLE to last-known-good. A baseline that cannot see a setting can
        # neither detect its loss nor restore it - the same lesson gamma taught on
        # 2026-08-01. All seven imaging sections, all three profiles.
        for sect in ("VideoInColor", "VideoInExposure", "VideoInWhiteBalance",
                     "VideoInDayNight", "VideoInBacklight", "VideoInSharpness",
                     "Lighting"):
            sd = c.get_config(f"{sect}[0]")
            for i in (0, 1, 2):
                got = {k.split(".")[-1]: v for k, v in sd.items()
                       if f"[0][{i}]" in k}
                # 2026-08-07: NOT every camera exposes every section on every
                # profile -- CAM4 has Lighting on profile 0 ONLY, and asking for
                # Lighting[0][1] directly answers "Error -1 getting param".
                # The bulk read above does not error, it just comes back without
                # those keys, so this filter used to yield {} and the baseline
                # recorded a NON-EXISTENT profile as an EMPTY one. Nothing was
                # raised and nothing looked wrong: a read that found nothing is
                # indistinguishable from a thing that has nothing, and a report
                # built on it invented five phantom CAM4 divergences.
                # Record absence AS ABSENCE so it can never be read as a value.
                all_profiles[f"{sect}[{i}]"] = got or {
                    "_absent": f"{sect}[0][{i}] not exposed by this camera"}
        # VideoInMode carries Mode (0 = SELF-ADAPTING by light level) and Config[0].
        # It is the mechanism behind the profile hazard, so a baseline that omits
        # it cannot show that the camera is free to switch out from under us.
        vim = c.get_config("VideoInMode[0]")
        return {"config_profile_field": p,
                "video_in_mode": {k.split(".")[-1]: v for k, v in vim.items()
                                  if "TimeSection" not in k},
                "color": short(c.get_config("VideoInColor")),
                "exposure": short(c.exposure()), "white_balance": short(c.white_balance()),
                "daynight_all_profiles": {f"profile{i}": dn.get(f"VideoInDayNight[0][{i}].Mode")
                                          for i in (0, 1, 2)},
                "backlight": short(c.get_config("VideoInBacklight")),
                "sharpness": short(c.get_config("VideoInSharpness")),
                "all_profiles": all_profiles}
    finally:
        c.close()


def ash21() -> dict:
    from calibrate import onvif_imaging as oi
    h = CFG["cameras"]["ash21"]["host"]
    tok = oi.video_source_token(h, 80)
    return {"levels": oi.read_levels(h, 80, tok)}


def xiongmai() -> dict:
    from calibrate.sofia import SofiaCam
    x = CFG["cameras"]["xiongmai"]
    with SofiaCam(x["host"], x.get("user", "admin"), x.get("password", "")) as c:
        c.login()
        tree = c.get("AVEnc.VideoColor")[0]
        cp = c.camera_param()
        return {"LIVE_section0": tree[0]["VideoColorParam"],
                "inactive_section1": tree[1]["VideoColorParam"],
                "camera_param": {k: cp.get(k) for k in
                                 ("WhiteBalance", "DayNightColor", "GainParam", "ExposureParam")}}


def foscam() -> dict:
    import httpx
    f = CFG["cameras"]["foscam"]
    base = f"http://{f['host']}:{f['port']}/cgi-bin/CGIProxy.fcgi"
    out = {}
    for cmd in ("getImageSetting", "getInfraLedConfig", "getVideoStreamParam"):
        t = httpx.get(base, params={"cmd": cmd, "usr": f["user"], "pwd": f["password"]},
                      timeout=10).text
        out[cmd] = {k: v for k, v in re.findall(r"<(\w+)>([^<]*)</\1>", t) if k != "result"}
    return out


SOURCES = {
    "CAM1": ("OBSBOT Tiny 4K Camera (USB/ProcAmp)", lambda: obsbot("OBSBOT Tiny 4K Camera")),
    "CAM2": ("Amcrest ASH21-B-V2 (ONVIF only)",     ash21),
    "CAM3": ("XiongMai / Sofia dvrip :34567",        xiongmai),
    "CAM4": ("Amcrest IP-cam (Dahua CGI)",           lambda: amcrest("CAM4")),
    "CAM5": ("OBSBOT Tiny Camera (USB/ProcAmp)",     lambda: obsbot("OBSBOT Tiny Camera")),
    "CAM6": ("Foscam R2C (CGI :88)",                 foscam),
    "CAM7": ("Amcrest IP4M-1041W (Dahua CGI)",       lambda: amcrest("CAM7")),
    "CAM8": ("USB Camera (USB/ProcAmp)",             lambda: obsbot("USB")),
}


def _now_iso() -> str:
    """When this snapshot was ACTUALLY taken.

    Was the string literal "2026-07-26", so every baseline written after that
    date misreported its own age. This file is the ROLLBACK: a stale-looking
    date gets a good baseline discarded, and a fresh-looking one gets old values
    restored as though current. A timestamp written by hand is not a timestamp.
    """
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def main() -> None:
    # `--help` used to READ ALL EIGHT CAMERAS and then try to write a file called
    # "--help", failing with PermissionError after doing the work (and on one
    # occasion leaving a directory of that name in the repo root). Asking a tool
    # what it does must never be the same as running it.
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help", "/?"):
        print(__doc__)
        raise SystemExit(0)
    out = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else \
        ROOT / "var/calibrate/FLEET-LAST-KNOWN-GOOD.json"
    snap = {"captured": _now_iso(), "note": "Live read from every device. CAM9 is a desktop "
            "capture and has no image settings.", "cameras": {}}
    for cam, (desc, fn) in SOURCES.items():
        try:
            snap["cameras"][cam] = {"device": desc, **fn()}
            print(f"  {cam}: ok")
        except Exception as e:
            snap["cameras"][cam] = {"device": desc, "ERROR": f"{type(e).__name__}: {e}"[:200]}
            print(f"  {cam}: ERROR {type(e).__name__}: {str(e)[:80]}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snap, indent=2))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
