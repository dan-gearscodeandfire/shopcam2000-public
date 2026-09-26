"""Rectify a SpyderCheckr from its four corners, then sample patches by index.

Why this exists (2026-07-26, CAM8). The overhead bench camera sees the card lying
at ~55 degrees. Axis-aligned boxes straddle patch borders (within-box sd 22-28
against a true patch sd of ~2). Masking the whole card as a quad and letting
`chartmath` find the neutral population instead gave a **7.1% p90-p10 noise floor
on R/G with luma stable to 3.5%** -- the iterative estimator re-picks its
population every frame, so the ratios jump while exposure plainly does not. A 7%
noise floor cannot servo a 14% cast.

Rectifying makes the patch grid axis-aligned and known, so WB can be measured on
the grey patches ALONE with `grey_only=True` -- no saturation filter, which is
the only safe mode on a cast camera.

Card layout (SpyderCheckr 24), 6 columns x 4 row-bands:
  col 0 spans bands 0-1 = WHITE          col 5 spans bands 0-1 = BLACK
  band 0, cols 1-4 = the fine grey ramp
  band 1, cols 1-4 = the four big GREY patches   <- what WB is measured on
  bands 2-3, cols 0-5 = colour patches

    python tools/chart_rectify.py IMG x0 y0 x1 y1 x2 y2 x3 y3 \
        [--optimise [--radius PX]] [--out FILE]

Corners must be given in card order: TL, TR, BR, BL, where the card's TOP edge
is the one the fine grey ramp runs along and the WHITE patch is at the LEFT.

`--optimise` refines those four corners against patch flatness before measuring
(and before drawing `--out`). Eyeballed corners are the dominant error left in
the chart path, so use it every time; it costs ~6 s. It only ever REFINES, so the
corners you give still have to be roughly right. **Still look at `--out`.**

Validated 2026-07-26 on the CAM4 hold, 6 starts perturbed by +/-8 px:

    corner-placement noise    R/G  2.53pp -> 0.39pp   (6.5x better)
                              B/G  1.49pp -> 1.49pp   (NO improvement)

⚠️ **It fixes R/G, not B/G.** Aligning the grid does not remove whatever makes
B/G move with the sample position (a blue gradient across the greys is the
likely cause), so **treat B/G differences under ~1.5pp as unmeasurable by this
method however carefully the corners are placed.** R/G is trustworthy to ~0.4pp.
Sanity check: this reproduces the session's hand-searched CAM4 result
(R/G +7.0% here vs +7.1% recorded) at a better patch_sd, 1.73 vs 2.73.
"""
from __future__ import annotations

import pathlib
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from calibrate.chartmath import grey_ratios  # noqa: E402

W, H = 720, 480          # rectified canvas
COLS, BANDS = 6, 4


def homography(dst: np.ndarray, src: np.ndarray) -> tuple[float, ...]:
    """PIL PERSPECTIVE coefficients mapping output (dst) -> input (src)."""
    A, b = [], []
    for (x, y), (u, v) in zip(dst, src):
        A.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        b.append(u)
        A.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        b.append(v)
    return tuple(np.linalg.solve(np.array(A, dtype=float), np.array(b, dtype=float)))


def rectify(im: Image.Image, corners: np.ndarray) -> Image.Image:
    dst = np.array([[0, 0], [W, 0], [W, H], [0, H]], dtype=float)
    return im.transform((W, H), Image.PERSPECTIVE,
                        homography(dst, corners), Image.BICUBIC)


def cell(col: int, band: int, inset: float = 0.28) -> tuple[int, int, int, int]:
    """Box for one grid cell, pulled in by `inset` so borders never contribute."""
    cw, bh = W / COLS, H / BANDS
    x0, y0 = col * cw, band * bh
    dx, dy = cw * inset, bh * inset
    return int(x0 + dx), int(y0 + dy), int(x0 + cw - dx), int(y0 + bh - dy)


def grey_patches(arr: np.ndarray) -> np.ndarray:
    """Pixels of the four big grey patches (band 1, cols 1-4), concatenated."""
    parts = []
    for c in range(1, 5):
        x0, y0, x1, y1 = cell(c, 1)
        parts.append(arr[y0:y1, x0:x1].reshape(-1, 3))
    return np.concatenate(parts).reshape(1, -1, 3)


def colour_patches(arr: np.ndarray) -> np.ndarray:
    parts = []
    for band in (2, 3):
        for c in range(COLS):
            x0, y0, x1, y1 = cell(c, band)
            parts.append(arr[y0:y1, x0:x1].reshape(-1, 3))
    return np.concatenate(parts).reshape(1, -1, 3)


def measure(im: Image.Image, corners: np.ndarray) -> dict | None:
    arr = np.asarray(rectify(im, corners).convert("RGB")).astype(np.float64)
    g = grey_ratios(grey_patches(arr), grey_only=True)
    if g is None:
        return None
    col = colour_patches(arr)
    R, G, B = col[..., 0] / g["rg"], col[..., 1], col[..., 2] / g["bg"]
    mx = np.maximum(np.maximum(R, G), B)
    mn = np.minimum(np.minimum(R, G), B)
    g["colour_sat"] = float(np.median(((mx - mn) / (mx + 1e-6))))
    # per-patch flatness: a real chart gives near-flat cells; a bad quad does not
    sds = []
    for c in range(1, 5):
        x0, y0, x1, y1 = cell(c, 1)
        w = arr[y0:y1, x0:x1]
        sds.append(float((0.299 * w[..., 0] + 0.587 * w[..., 1] + 0.114 * w[..., 2]).std()))
    g["patch_sd"] = float(np.mean(sds))
    return g


# --- corner refinement -----------------------------------------------------
# Eyeballed corners are the dominant error in every chart reading: on the CAM4
# hold of 2026-07-26 they scored mean patch sd 16.8 against 6.5 for a searched
# quad. Borders bleeding into the sample boxes is exactly what a flatness score
# sees, so flatness is a usable objective -- but only with a leash. Left free it
# has a trivial global minimum: shrink the quad inside one uniform patch and
# every cell reads sd 0. Hence `radius` (no corner may move far from the one you
# placed) and the area check below.

# The 20 grid cells that hold ONE flat colour. Band 0 cols 1-4 are excluded:
# that is the fine grey ramp, many small steps that do not align to the 6-column
# grid, so those cells carry an irreducible sd that varies with quad position and
# biases the optimum. Everything else is a single patch (col 0 = white and col 5
# = black each span bands 0-1).
FLAT_CELLS = ([(0, 0), (5, 0)]
              + [(c, 1) for c in range(COLS)]
              + [(c, b) for b in (2, 3) for c in range(COLS)])


def flatness(im: Image.Image, corners: np.ndarray) -> float:
    """Mean per-cell luma sd over the 20 flat cells. Lower = better aligned.

    Not just the greys: the colour patches have the highest-contrast borders on
    the card, so they constrain the quad hardest.
    """
    arr = np.asarray(rectify(im, corners).convert("RGB")).astype(np.float64)
    Y = 0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]
    sds = []
    for c, band in FLAT_CELLS:
        x0, y0, x1, y1 = cell(c, band)
        sds.append(float(Y[y0:y1, x0:x1].std()))
    return float(np.mean(sds))


def _area(c: np.ndarray) -> float:
    """Shoelace area of the quad."""
    x, y = c[:, 0], c[:, 1]
    return float(abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) / 2)


def optimise(im: Image.Image, corners: np.ndarray, radius: float = 30.0,
             verbose: bool = True) -> tuple[np.ndarray, float, float]:
    """Coordinate descent on the 8 corner coordinates against `flatness`.

    Each coordinate is tried +/- step, best move kept, step halved when a full
    sweep finds nothing. Bounded to `radius` px from the supplied corners, and
    the quad's area may not change by more than 25% -- both guards against the
    collapse-into-one-patch minimum.
    """
    best = corners.astype(float).copy()
    start_score = score = flatness(im, best)
    area0 = _area(corners)
    step = max(4.0, radius / 4)
    while step >= 0.5:
        improved = False
        for i in range(4):
            for j in range(2):
                for delta in (step, -step):
                    trial = best.copy()
                    trial[i, j] += delta
                    if abs(trial[i, j] - corners[i, j]) > radius:
                        continue
                    a = _area(trial)
                    if not 0.75 * area0 <= a <= 1.25 * area0:
                        continue
                    s = flatness(im, trial)
                    if s < score - 1e-6:
                        best, score, improved = trial, s, True
        if not improved:
            step /= 2
    if verbose:
        moved = np.linalg.norm(best - corners, axis=1)
        print(f"  optimise: flatness {start_score:.2f} -> {score:.2f}  "
              f"(corners moved {moved.min():.1f}-{moved.max():.1f} px, "
              f"area {_area(best) / area0 * 100:.0f}% of start)")
        print("  corners: " + " ".join(f"{v:.0f}" for v in best.reshape(-1)))
        if moved.max() > radius - 1:
            print("  !! a corner hit the radius limit -- re-place it by eye "
                  "or raise --radius; the result may not be a true minimum")
    return best, start_score, score


def main() -> None:
    img = pathlib.Path(sys.argv[1])
    corners = np.array([float(v) for v in sys.argv[2:10]], dtype=float).reshape(4, 2)
    im = Image.open(img).convert("RGB")
    if "--optimise" in sys.argv or "--optimize" in sys.argv:
        radius = 30.0
        if "--radius" in sys.argv:
            radius = float(sys.argv[sys.argv.index("--radius") + 1])
        corners, _, _ = optimise(im, corners, radius=radius)
    if "--out" in sys.argv:
        out = pathlib.Path(sys.argv[sys.argv.index("--out") + 1])
        r = rectify(im, corners).copy()
        from PIL import ImageDraw
        d = ImageDraw.Draw(r)
        for c in range(1, 5):
            d.rectangle(cell(c, 1), outline=(0, 255, 0), width=3)
        for band in (2, 3):
            for c in range(COLS):
                d.rectangle(cell(c, band), outline=(255, 128, 0), width=2)
        d.rectangle(cell(0, 0), outline=(0, 128, 255), width=3)
        d.rectangle(cell(5, 0), outline=(255, 0, 255), width=3)
        r.save(out)
        print(f"rectified -> {out}  (green=greys, orange=colours, blue=white, magenta=black)")
    g = measure(im, corners)
    if not g:
        sys.exit("no measurement")
    print(f"  R/G {g['rg']:.4f} ({(g['rg']-1)*100:+.1f}%)   "
          f"B/G {g['bg']:.4f} ({(g['bg']-1)*100:+.1f}%)")
    print(f"  grey luma {g['luma']:.1f}   glare {g['glare']:.4f}   "
          f"colour_sat {g['colour_sat']:.4f}   patch_sd {g['patch_sd']:.2f}")


if __name__ == "__main__":
    main()
