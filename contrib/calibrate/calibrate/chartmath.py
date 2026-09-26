"""Grey-patch measurement that survives a strongly cast camera.

THE BUG THIS EXISTS TO KILL (found 2026-07-24 on CAM7)
------------------------------------------------------
Every neutral-sampling routine here used to start with `saturation < 0.15`.
That rule is circular: it defines a neutral as "a pixel this camera already
renders near-neutral", so on a camera with a real cast it **throws away that
camera's own grey patches** and keeps only the least-cast survivors. The
measured cast is then biased toward zero — CAM7's true grey ratio was
R/G 0.780, and the sat<0.15 mask reported 0.92. A servo fed that number
under-corrects by 3x and then declares convergence.

The same circularity broke the glare gate. It tested the white patch's RAW
saturation against 0.1 to detect a specular veil, but a cast camera renders
EVERY neutral as non-neutral — CAM7's cast alone contributes ~0.14, so a
spotless hold failed the gate on all 45 frames.

THE FIX
-------
Estimate the cast first, then judge neutrality in cast-corrected space:

  1. Seed the grey ratios with a deliberately GENEROUS saturation ceiling
     (0.45) — wide enough to admit a badly cast grey, tight enough to exclude
     the chart's vivid patches, which clear 0.5.
  2. Divide R and B by those ratios. A true grey now sits near zero saturation
     whatever the camera's cast.
  3. Re-select neutrals in that corrected space with a tight threshold, and
     re-estimate. Iterate — it converges in 2-3 passes.
  4. Judge glare on CORRECTED saturation, so it measures specular
     contamination only, not the cast.

Pass `grey_only=True` when the box was drawn on grey patches alone; then no
saturation filter is applied at all and only a luma band is used. That is the
most reliable mode and the one to prefer for a suspected-cast camera.
"""
from __future__ import annotations

import numpy as np

SEED_SAT = 0.45      # generous: admits a cast grey, excludes printed colour
TIGHT_SAT = 0.15     # applied only AFTER cast correction
LUMA_BAND = (60.0, 235.0)   # drop the black patch and anything clipping
ITERS = 3


def _sat(R: np.ndarray, G: np.ndarray, B: np.ndarray) -> np.ndarray:
    mx = np.maximum(np.maximum(R, G), B)
    mn = np.minimum(np.minimum(R, G), B)
    return (mx - mn) / (mx + 1e-6)


def grey_ratios(win: np.ndarray, grey_only: bool = False) -> dict | None:
    """Measure R/G, B/G, luma on the neutral patches inside `win` (HxWx3 RGB).

    Returns None when there is not enough evidence to call it a measurement.
    `glare` is CAST-CORRECTED saturation on the brightest neutrals — compare it
    against a threshold to detect a specular veil; it does NOT rise merely
    because the camera has a cast.
    """
    R, G, B = win[..., 0], win[..., 1], win[..., 2]
    Y = 0.299 * R + 0.587 * G + 0.114 * B
    band = (Y >= LUMA_BAND[0]) & (Y <= LUMA_BAND[1])
    if band.sum() < 50:
        return None

    mask = band if grey_only else band & (_sat(R, G, B) < SEED_SAT)
    if mask.sum() < 50:
        return None

    rg = bg = 1.0
    for _ in range(ITERS):
        r, g, b = np.median(R[mask]), np.median(G[mask]), np.median(B[mask])
        if g <= 0:
            return None
        rg, bg = float(r / g), float(b / g)
        if grey_only:
            break
        # Re-select in cast-corrected space: a genuine grey is now ~neutral
        # regardless of how far this camera's white balance is off.
        corrected = _sat(R / rg, G, B / bg)
        new = band & (corrected < TIGHT_SAT)
        if new.sum() < 50:
            break
        mask = new

    r, g, b = np.median(R[mask]), np.median(G[mask]), np.median(B[mask])
    rg, bg = float(r / g), float(b / g)
    corrected = _sat(R / rg, G, B / bg)
    hi = mask & (Y >= np.percentile(Y[mask], 90))
    return dict(
        rg=rg, bg=bg,
        luma=float(0.299 * r + 0.587 * g + 0.114 * b),
        n=int(mask.sum()),
        raw_sat=float(np.median(_sat(R, G, B)[mask])),
        glare=float(np.median(corrected[hi])) if hi.sum() else float("nan"),
        clip=float((Y >= 250).mean() * 100),
    )
