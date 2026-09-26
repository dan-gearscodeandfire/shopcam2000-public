"""Measure how sharp a camera's picture actually is, and whether haze is stealing
its contrast — the two things a dirty dome or a mis-set focus ruin.

    python tools/edge_acuity.py DIR CAM2 CAM1

Reads every ``<cam>_NN.jpg`` (or ``<cam>.jpg``) in DIR and reports the MEDIAN
across frames, because a single grab is noisy. Take the burst with
``tools/grab_fleet.py`` (2026-08-09: this line used to point at
``tools/chart_burst.py``, which has never existed in this repo — a dangling
pointer in a docstring sends the next reader hunting for a tool instead of
using the one that is here).

Columns:

* ``meanGrad``  mean gradient magnitude. Overall edge energy.
* ``p99Grad``   99th-percentile gradient — the *hardest* edges in the frame.
  This is the number that exposes softness: blur destroys the sharpest edges
  long before it touches the average.
* ``acuity``    ``p99Grad`` divided by the frame's luma spread. Gradient scales
  with contrast, so a hazy lens and a blurred lens both drag ``p99Grad`` down.
  Dividing it out asks the narrower question — *given* this much contrast, how
  crisply is it resolved? A clean-but-defocused lens loses acuity; a sharp-but-
  filthy one keeps it.
* ``p1``/``p99``/``range`` luma floor, ceiling and spread. **A dirty or hazy dome
  scatters light into the shadows, so its tell is a black floor that will not
  reach zero** while the ceiling also falls short.

⚠️ These numbers compare a camera **to itself** — before vs after a clean, a
focus tweak, a lighting change. Do NOT rank one camera against another with
them: they see different scenes at different resolutions, and both drive
gradient statistics directly. Same camera, same scene, same lights, or the
comparison means nothing.
"""
from __future__ import annotations

import pathlib
import sys

import numpy as np
from PIL import Image


def frame_stats(path: pathlib.Path) -> dict:
    im = np.asarray(Image.open(path).convert("RGB")).astype(np.float64)
    Y = 0.299 * im[..., 0] + 0.587 * im[..., 1] + 0.114 * im[..., 2]

    gy, gx = np.gradient(Y)
    grad = np.hypot(gx, gy)

    p1, p99y = (float(v) for v in np.percentile(Y, (1, 99)))
    p99g = float(np.percentile(grad, 99))
    spread = p99y - p1
    return dict(
        mean_grad=float(grad.mean()),
        p99_grad=p99g,
        # Guard the degenerate flat-frame case (a lens cap, a dead stream).
        acuity=p99g / spread * 100 if spread > 1 else float("nan"),
        p1=p1, p99=p99y, span=spread, luma=float(Y.mean()),
        px=Y.size,
    )


def camera_stats(d: pathlib.Path, cam: str) -> tuple[dict, int]:
    """Median of each metric over every frame found for ``cam``.

    Median rather than mean: one frame catching a passing shadow or a glare
    flash should not move the answer.
    """
    paths = sorted(d.glob(f"{cam.lower()}_*.jpg")) or sorted(d.glob(f"{cam.lower()}.jpg"))
    if not paths:
        raise SystemExit(f"no frames for {cam} in {d} (looked for {cam.lower()}_*.jpg)")
    rows = [frame_stats(p) for p in paths]
    return {k: float(np.median([r[k] for r in rows])) for k in rows[0]}, len(rows)


def main() -> None:
    d = pathlib.Path(sys.argv[1])
    cams = sys.argv[2:]
    if not cams:
        raise SystemExit(__doc__)

    hdr = (f"{'cam':5} {'n':>3} {'MP':>5} {'luma':>6} {'meanGrad':>9} {'p99Grad':>8} "
           f"{'acuity':>7} {'p1':>5} {'p99':>5} {'range':>6}")
    print(hdr)
    print("-" * len(hdr))
    for cam in cams:
        s, n = camera_stats(d, cam)
        print(f"{cam:5} {n:3d} {s['px']/1e6:5.2f} {s['luma']:6.1f} "
              f"{s['mean_grad']:9.2f} {s['p99_grad']:8.1f} {s['acuity']:7.2f} "
              f"{s['p1']:5.0f} {s['p99']:5.0f} {s['span']:6.0f}")


if __name__ == "__main__":
    main()
