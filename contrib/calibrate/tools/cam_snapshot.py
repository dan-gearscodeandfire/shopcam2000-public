"""Pull a live JPEG from a camera so you can check it with your eyes.

Written for CAM9, the desktop-capture camera, where the status flags lie by
omission: `isNoSignal=false` only means Blue Iris received *a* frame. The manual
offers a "Blackness" capture mode, and a locked or non-rendering Windows session
yields a black or frozen frame that still reports signal. Only the pixels settle
whether the capture is real.

    python tools/cam_snapshot.py                 # CAM9 -> cam9.jpg
    python tools/cam_snapshot.py CAM2 out.jpg    # any camera, any path

Reads the same gitignored config.toml as the app, so no credential is ever typed
on a command line.

🔴 --scale defaults to 100 (full resolution) BECAUSE IT ONCE DEFAULTED TO 50.
On 2026-07-28 a CAM8 focus investigation compared these 960x536 snapshots against
a 1920x1080 reference frame and measured a 50x collapse in Laplacian variance
that was mostly the downscale. Channel ratios and luma survive a resize; anything
that depends on DETAIL -- sharpness, focus, noise, edge acuity -- does not.
The printed line always states the real pixel dimensions, so a scaled frame can
never again be mistaken for a full one.
"""

from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import calibrate  # noqa: F401,E402  (safe console encoding: the help text has emoji)

from shopcam2000 import config as cfg  # noqa: E402
from shopcam2000.blueiris import BlueIrisClient  # noqa: E402


def jpeg_size(data: bytes) -> tuple[int, int] | None:
    """(width, height) from the JPEG SOF marker — no Pillow dependency.

    Deliberately dependency-free: this runs in every snapshot, including on
    hosts where the measurement stack is not installed.
    """
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        # SOF0..SOF15, excluding the non-frame markers DHT/JPG/DAC
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            return int.from_bytes(data[i + 7:i + 9], "big"), int.from_bytes(data[i + 5:i + 7], "big")
        i += 2 + int.from_bytes(data[i + 2:i + 4], "big")
    return None


async def grab(camera: str, out: pathlib.Path, scale: int) -> None:
    c = cfg.load()
    bi = BlueIrisClient(c.blue_iris.host, c.blue_iris.port,
                        c.blue_iris.user, c.blue_iris.password)
    try:
        await bi.login()
        data = await bi.snapshot(camera, scale_pct=scale)
        out.write_bytes(data)
        # A black frame compresses to a couple of KB; anything with real desktop
        # content lands far above that. Worth printing as a first-glance signal.
        wh = jpeg_size(data)
        dims = f"{wh[0]}x{wh[1]}" if wh else "size unknown"
        print(f"{camera} -> {out} ({len(data):,} bytes, {dims}, scale={scale}%)")
        # ASCII only in these prints: the Windows console runs cp1252 and an
        # emoji or em-dash here raises UnicodeEncodeError *after* the file is
        # written, so the grab looks like it failed when it did not.
        if len(data) < 8_000:
            print("  WARNING: suspiciously small - likely a black or blank frame")
        if scale != 100:
            print(f"  WARNING: SCALED to {scale}% - do not use this frame for sharpness, "
                  f"focus or noise metrics; re-grab with --scale 100")
    finally:
        await bi.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("camera", nargs="?", default="CAM9")
    ap.add_argument("out", nargs="?", default=None,
                    help="output path (default: <camera>.jpg, lowercased)")
    ap.add_argument("--scale", type=int, default=100,
                    help="percent, default 100 (full res). Anything less is unsafe "
                         "for detail-based measurement — see the module docstring.")
    args = ap.parse_args()

    out = pathlib.Path(args.out) if args.out else pathlib.Path(f"{args.camera.lower()}.jpg")
    asyncio.run(grab(args.camera, out, args.scale))


if __name__ == "__main__":
    main()
