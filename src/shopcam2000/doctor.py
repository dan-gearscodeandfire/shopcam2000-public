"""``shopcam doctor`` - check the things that fail silently.

Every check in here is something the reference rig learned by losing footage or
by chasing a ghost for an evening. None of them are checks a healthy system
*needs*; they are all checks that something which looked healthy was not.

The organising principle is stated once and applied throughout:

    🔑 THE CAMERA LIES - CHECK THE WIRE.
    A status field is a CLAIM. A frame pulled off the wire is EVIDENCE.
    Where the two disagree, the wire is right and the claim is a bug report.

Exit code is 0 if nothing failed, 1 otherwise, so this can gate a deploy.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import sys
from pathlib import Path

from .blueiris import BlueIrisClient, BlueIrisError
from .config import CONFIG_PATH, ROOT, STATE_DIR, Config, ConfigError
from .config import load as load_config
from .paths import find_ffmpeg, find_ffprobe, install_hint

OK, WARN, FAIL, INFO = "ok", "warn", "fail", "info"

_GLYPH = {OK: "  ok  ", WARN: " warn ", FAIL: " FAIL ", INFO: " info "}
_COLOUR = {OK: "\033[32m", WARN: "\033[33m", FAIL: "\033[31m", INFO: "\033[36m"}


class Report:
    def __init__(self, colour: bool) -> None:
        self.colour = colour
        self.failed = 0
        self.warned = 0

    def line(self, level: str, title: str, detail: str = "") -> None:
        if level == FAIL:
            self.failed += 1
        elif level == WARN:
            self.warned += 1
        tag = _GLYPH[level]
        if self.colour:
            tag = f"{_COLOUR[level]}{tag}\033[0m"
        print(f"[{tag}] {title}")
        for chunk in (detail or "").splitlines():
            if chunk.strip():
                print(f"         {chunk}")

    def section(self, name: str) -> None:
        print(f"\n\033[1m{name}\033[0m" if self.colour else f"\n{name}")


# --------------------------------------------------------------------- checks

def check_binaries(rep: Report, cfg: Config | None) -> None:
    rep.section("External binaries")
    configured = cfg.recording.ffprobe_path if cfg else ""

    probe = find_ffprobe(configured)
    if probe:
        rep.line(OK, "ffprobe", probe)
    else:
        rep.line(FAIL, "ffprobe not found",
                 "Clip verification is the only layer that OPENS a saved file. Without\n"
                 "it, 'the recorder said it saved' is the last word - and on the\n"
                 "reference rig 1 file in 99 was 5.56 MB with no H.264 start code\n"
                 "inside a press that reported success with no failures.\n"
                 f"Install: {install_hint()}")

    if find_ffmpeg():
        rep.line(OK, "ffmpeg", find_ffmpeg() or "")
    else:
        rep.line(WARN, "ffmpeg not found",
                 "Only needed for `shopcam encode` (USB/desktop -> RTSP).\n"
                 f"Install: {install_hint()}")


def check_config(rep: Report) -> Config | None:
    rep.section("Configuration")
    rep.line(INFO, "SHOPCAM_HOME", str(ROOT) +
             ("  (from $SHOPCAM_HOME)" if os.environ.get("SHOPCAM_HOME") else "  (current directory)"))
    if not CONFIG_PATH.exists():
        rep.line(FAIL, "config.toml not found", f"Expected at {CONFIG_PATH}\n"
                 "Run `shopcam init` to write a commented starter file here.")
        return None
    try:
        cfg = load_config()
    except ConfigError as exc:
        rep.line(FAIL, "config.toml is not usable", str(exc))
        return None
    rep.line(OK, "config.toml", str(CONFIG_PATH))

    # A password in a world-readable file is not this tool's business to fix,
    # but it IS its business to mention once.
    if os.name != "nt":
        mode = CONFIG_PATH.stat().st_mode & 0o077
        if mode:
            rep.line(WARN, "config.toml is readable by other users",
                     "It holds your recorder password.  chmod 600 config.toml")
    return cfg


async def check_recorder(rep: Report, cfg: Config) -> list[dict]:
    rep.section("Recorder (Blue Iris)")
    client = BlueIrisClient(
        host=cfg.blue_iris.host, port=cfg.blue_iris.port,
        user=cfg.blue_iris.user, password=cfg.blue_iris.password,
        timeout=cfg.blue_iris.timeout,
    )
    try:
        await client.login()
    except BlueIrisError as exc:
        rep.line(FAIL, f"cannot reach Blue Iris at {cfg.blue_iris.host}:{cfg.blue_iris.port}",
                 f"{exc}\n"
                 "Check: Blue Iris > Settings > Web server is enabled, the port matches,\n"
                 "and this account exists. The account needs 'clip creation' permission\n"
                 "or start/stop recording will fail with a success-looking response.")
        with contextlib.suppress(Exception):
            await client.close()
        return []
    rep.line(OK, f"Blue Iris {client.version or '?'}",
             f"{cfg.blue_iris.host}:{cfg.blue_iris.port} as {cfg.blue_iris.user}")

    try:
        cams = await client.camlist()
    except BlueIrisError as exc:
        rep.line(FAIL, "camlist failed", str(exc))
        cams = []
    finally:
        with contextlib.suppress(Exception):
            await client.close()

    real = [c for c in cams if c.get("optionValue") and not c.get("group")]
    rep.line(OK if real else FAIL, f"{len(real)} cameras")
    return real


def check_cameras(rep: Report, cfg: Config, cams: list[dict]) -> None:
    if not cams:
        return
    rep.section("Cameras - claim vs wire")
    audio_only = set(cfg.recording.audio_only or ())
    for cam in cams:
        name = cam["optionValue"]
        online = bool(cam.get("isOnline"))
        enabled = bool(cam.get("isEnabled"))
        nosig = bool(cam.get("isNoSignal"))
        fps = float(cam.get("FPS") or 0)

        if not enabled:
            rep.line(INFO, f"{name}: disabled in Blue Iris")
            continue
        if not online:
            rep.line(FAIL, f"{name}: offline")
            continue
        if nosig:
            rep.line(FAIL, f"{name}: online but NO SIGNAL")
            continue

        # 🔴 The check that matters. Blue Iris keeps reporting a dead camera as
        # online+signal for ~20 s after its source dies - measured. So "online"
        # alone has a window in which it is simply false.
        if fps <= 0.1:
            rep.line(FAIL, f"{name}: Blue Iris says online, but FPS is {fps:g}",
                     "This is the shape of the lie: the status field is stale.\n"
                     "First-hand knowledge overrides the recorder's availability.")
            continue

        note = "audio-only (waveform video)" if name in audio_only else ""
        rep.line(OK, f"{name}: {fps:g} fps", note)


def check_storage(rep: Report, cfg: Config, cams: list[dict]) -> None:
    rep.section("Storage")
    clip_dir = Path(cfg.recording.clip_dir)
    if not clip_dir.exists():
        rep.line(WARN, "clip_dir does not exist here", f"{clip_dir}\n"
                 "Fine if the Controller runs on a different host from Blue Iris -\n"
                 "clip verification and auto-sorting are skipped, everything else works.")
        return

    usage = shutil.disk_usage(clip_dir)
    free_gb = usage.free / 1e9
    total_gb = usage.total / 1e9
    used_pct = round(100 * (1 - usage.free / usage.total))

    # ⚠️ BPS from Blue Iris is BYTES per second, not bits. Verified against an
    # independently measured 21.7 GB/hr for nine cameras: bytes gives 21.56,
    # bits gives 2.69. Do not add a /8.
    rate = sum(float(c.get("BPS") or 0) for c in cams if c.get("isEnabled"))
    gb_per_hour = rate * 3600 / 1e9

    level = FAIL if used_pct >= 95 else WARN if used_pct >= 85 else OK
    detail = f"{free_gb:.0f} GB free of {total_gb:.0f} GB ({used_pct}% used) at {clip_dir}"
    if gb_per_hour > 0.05:
        hours = free_gb / gb_per_hour
        detail += (f"\nAbout {hours:.0f} hours of headroom at {gb_per_hour:.1f} GB/hr "
                   f"with all {len(cams)} cameras recording.")
        if hours < 4:
            level = FAIL
    else:
        detail += "\nNo bitrate reported, so no headroom estimate."
    rep.line(level, "clip volume", detail)


def check_secure_context(rep: Report, cfg: Config) -> None:
    """🔴 The single most-missed thing, because it fails by ABSENCE."""
    rep.section("Browser capabilities")
    host, port = cfg.server.host, cfg.server.port
    shown = "localhost" if host in ("0.0.0.0", "127.0.0.1", "::") else host
    rep.line(WARN, "served over plain HTTP",
             f"On the machine running this, http://localhost:{port} IS a secure\n"
             "context and everything works. From any OTHER device - your phone -\n"
             f"http://{shown}:{port} is NOT, and these do not merely fail, they\n"
             "are ABSENT (the APIs are undefined, nothing throws, nothing logs):\n"
             "  * service worker  -> so the browser never offers to install the app\n"
             "  * wake lock       -> so the screen blanks mid-shoot\n"
             "Two fixes, neither of them code:\n"
             "  1. per device: chrome://flags/#unsafely-treat-insecure-origin-as-secure\n"
             f"     add http://{shown}:{port}, Enabled, relaunch. Two minutes.\n"
             "  2. properly: serve TLS. See docs/https.md.\n"
             "Settings > About in the UI reports which case that device is in.")


# ----------------------------------------------------------------------- main

async def _run(rep: Report) -> None:
    cfg = check_config(rep)
    check_binaries(rep, cfg)
    if cfg is None:
        return
    cams = await check_recorder(rep, cfg)
    check_cameras(rep, cfg, cams)
    check_storage(rep, cfg, cams)
    check_secure_context(rep, cfg)


def main(colour: bool = True) -> int:
    rep = Report(colour=colour and sys.stdout.isatty())
    print("shopcam doctor")
    try:
        asyncio.run(_run(rep))
    except KeyboardInterrupt:
        return 130
    print()
    if rep.failed:
        print(f"{rep.failed} failed, {rep.warned} warnings.")
        return 1
    print(f"Nothing failed. {rep.warned} warnings." if rep.warned else "All clear.")
    return 0
