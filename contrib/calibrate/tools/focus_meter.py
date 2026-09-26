"""Live sharpness meter for setting a camera's focus ring by hand.

The companion to tools/iris_meter.py, and used the same way: run it at the
console, turn the ring slowly, watch the number. Unlike exposure, focus has a
true peak — you are hunting a maximum, not a knee.

    python tools/focus_meter.py CAM2
    python tools/focus_meter.py CAM2 -i 1.0

`sharp` is the 99th-percentile gradient — the hardest edges in the frame, which
is what blur destroys first. `best` is the sharpest reading so far and `off%`
how far under it you currently are. **Turn past the peak deliberately**: the
only way to know you are at the top is to see the number fall on both sides.
When `off%` reads 0.0 you are back at the best position seen.

⚠️ Two rules or the readings lie:
  * Nothing may move in frame, including you. A person walking through adds
    edges and inflates `sharp`.
  * Do not change the lights mid-sweep. Gradient scales with contrast.

Ctrl-C prints the peak.
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

# Within this much of the best reading counts as "at the peak" — focus is
# flat near optimum and the snapshot-to-snapshot noise is around 1%.
PEAK_TOL_PCT = 2.0


def bar(value: float, best: float, width: int = 34) -> str:
    n = 0 if best <= 0 else int(round(width * value / best))
    n = max(0, min(width, n))
    return "#" * n + "." * (width - n)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("camera", nargs="?", default="CAM2")
    ap.add_argument("-i", "--interval", type=float, default=1.5)
    a = ap.parse_args()

    c = cfg.load()
    bi = BlueIrisClient(c.blue_iris.host, c.blue_iris.port,
                        c.blue_iris.user, c.blue_iris.password)
    await bi.login()

    best = 0.0
    print(f"focusing {a.camera} every {a.interval}s — turn slowly, go PAST the peak, Ctrl-C to stop")
    print(f"{'sharp':>7} {'mean':>6} {'acuity':>7} {'best':>7} {'off%':>6}  frame")
    try:
        while True:
            im = metrics.load_rgb(await bi.snapshot(a.camera, scale_pct=100))
            Y = 0.299 * im[..., 0] + 0.587 * im[..., 1] + 0.114 * im[..., 2]
            gy, gx = np.gradient(Y)
            grad = np.hypot(gx, gy)

            sharp = float(np.percentile(grad, 99))
            p1, p99 = (float(v) for v in np.percentile(Y, (1, 99)))
            # Same contrast-normalised figure tools/edge_acuity.py reports, so a
            # ring position found here can be confirmed later with a burst.
            acuity = sharp / (p99 - p1) * 100 if p99 - p1 > 1 else float("nan")

            best = max(best, sharp)
            off = (best - sharp) / best * 100 if best else 0.0
            note = "  <- PEAK" if off <= PEAK_TOL_PCT else ""

            print(f"{sharp:7.1f} {float(grad.mean()):6.2f} {acuity:7.2f} {best:7.1f} "
                  f"{off:6.1f}  {bar(sharp, best)}{note}", flush=True)
            await asyncio.sleep(a.interval)
    except (KeyboardInterrupt, asyncio.CancelledError):
        print(f"\npeak sharpness {best:.1f} — leave the ring where it read within "
              f"{PEAK_TOL_PCT}% of that ({best * (1 - PEAK_TOL_PCT / 100):.1f} or better)")
    finally:
        await bi.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
