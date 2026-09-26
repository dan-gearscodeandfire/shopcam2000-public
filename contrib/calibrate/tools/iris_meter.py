"""Live exposure meter for setting CAM3's manual iris ring by hand.

CAM3's gain is pinned at 0 and its white balance is uncontrollable, so
auto-shutter (256-65536 us) is the only automatic lever it has left. Closing
the iris does nothing visible until the shutter runs out of range — at which
point the frame starts to darken. That knee is the setting you want to find,
then back a quarter turn open from it.

Run this at the console, turn the ring slowly, and watch the numbers:

    python tools/iris_meter.py              # CAM3, every 1.5s
    python tools/iris_meter.py CAM2 -i 1.0  # any camera, any interval

`plateau` is the brightest stable reading seen so far — while the shutter is
still compensating, `mean` tracks it. When `drop%` starts climbing, the
shutter has hit its long end and you have found the knee. Ctrl-C to stop.
"""

from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from shopcam2000 import config as cfg  # noqa: E402
from shopcam2000.blueiris import BlueIrisClient  # noqa: E402
from calibrate import metrics  # noqa: E402

# More than this far under the plateau means the shutter has run out and the
# iris is stealing real exposure — the knee.
KNEE_DROP_PCT = 4.0


def bar(value: float, lo: float = 0.0, hi: float = 255.0, width: int = 32) -> str:
    n = int(round(width * (value - lo) / (hi - lo)))
    return "#" * max(0, min(width, n)) + "." * (width - max(0, min(width, n)))


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("camera", nargs="?", default="CAM3")
    ap.add_argument("-i", "--interval", type=float, default=1.5)
    a = ap.parse_args()

    c = cfg.load()
    bi = BlueIrisClient(c.blue_iris.host, c.blue_iris.port,
                        c.blue_iris.user, c.blue_iris.password)
    await bi.login()

    plateau = 0.0
    print(f"metering {a.camera} every {a.interval}s — turn the iris slowly, Ctrl-C to stop")
    print(f"{'mean':>6} {'p5':>4} {'p95':>4} {'clip%':>6} {'plateau':>8} {'drop%':>6}  frame")
    try:
        while True:
            im = metrics.load_rgb(await bi.snapshot(a.camera, scale_pct=100))
            luma = 0.299 * im[..., 0] + 0.587 * im[..., 1] + 0.114 * im[..., 2]
            mean = float(luma.mean())
            p5, p95 = (float(x) for x in np.percentile(luma, (5, 95)))
            clip = float((luma >= 250).mean() * 100)

            plateau = max(plateau, mean)
            drop = (plateau - mean) / plateau * 100 if plateau else 0.0

            note = ""
            if clip > 1.0:
                note = "  <- clipping, close down"
            elif drop >= KNEE_DROP_PCT:
                note = "  <- KNEE: shutter maxed, back off 1/4 turn"

            print(f"{mean:6.1f} {p5:4.0f} {p95:4.0f} {clip:6.2f} {plateau:8.1f} "
                  f"{drop:6.1f}  {bar(mean)}{note}")
            await asyncio.sleep(a.interval)
    # Ctrl-C during the snapshot fetch surfaces as CancelledError from inside
    # the await, not as KeyboardInterrupt — catch both or the console eats a
    # screenful of traceback at exactly the wrong moment.
    except (KeyboardInterrupt, asyncio.CancelledError):
        print(f"\nplateau was {plateau:.1f}; knee at {KNEE_DROP_PCT}% below = "
              f"{plateau * (1 - KNEE_DROP_PCT / 100):.1f}")
    finally:
        await bi.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
