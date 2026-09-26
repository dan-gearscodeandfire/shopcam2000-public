"""Colour/exposure metrics off a grabbed frame.

Two measurement modes:

* chart_neutrals — the SpyderCheckr grey patches inside a hand-drawn box.
  Glare-robust per-channel medians over low-saturation pixels in a luma band.
  This is the cross-camera matching signal (the chart sits at the subject
  plane, which is what must match on cuts).

* scene_neutrals — auto-detected low-saturation surfaces over the whole frame.
  Scene content confounds absolute comparisons between cameras, but for ONE
  camera whose framing hasn't moved it is a stable before/after signal: apply
  a setting, re-grab, and the shift in scene R/G, B/G and luma tracks the
  shift a chart would show. Used to servo corrections when nobody is holding
  the chart.
"""
from __future__ import annotations

import dataclasses
import io
import pathlib

import numpy as np
from PIL import Image


@dataclasses.dataclass
class Neutrals:
    n: int              # pixels that passed the mask
    r_g: float          # median R / median G  (1.0 = neutral)
    b_g: float          # median B / median G
    luma: float         # Rec.601 luma of the channel medians
    clip_hi: float      # % of masked pixels at/above 250

    def ok(self, min_n: int = 50) -> bool:
        return self.n >= min_n


def load_rgb(src: bytes | str | pathlib.Path) -> np.ndarray:
    """JPEG bytes or a path -> float64 RGB array."""
    if isinstance(src, bytes):
        im = Image.open(io.BytesIO(src))
    else:
        im = Image.open(src)
    return np.asarray(im.convert("RGB")).astype(np.float64)


def _neutrals(im: np.ndarray, sat_max: float, luma_lo: float,
              luma_hi: float) -> Neutrals:
    R, G, B = im[..., 0], im[..., 1], im[..., 2]
    Y = 0.299 * R + 0.587 * G + 0.114 * B
    mx = np.maximum(np.maximum(R, G), B)
    mn = np.minimum(np.minimum(R, G), B)
    sat = (mx - mn) / (mx + 1e-6)
    mask = (sat < sat_max) & (Y >= luma_lo) & (Y <= luma_hi)
    n = int(mask.sum())
    if n < 50:
        nan = float("nan")
        return Neutrals(n, nan, nan, nan, nan)
    r, g, b = np.median(R[mask]), np.median(G[mask]), np.median(B[mask])
    return Neutrals(
        n=n, r_g=r / g, b_g=b / g,
        luma=0.299 * r + 0.587 * g + 0.114 * b,
        clip_hi=float((Y[mask] >= 250).mean() * 100),
    )


def chart_neutrals(im: np.ndarray, box: tuple[int, int, int, int],
                   luma_lo: float = 70, luma_hi: float = 190) -> Neutrals:
    """Grey patches inside box=(x0,y0,x1,y1) of the full frame."""
    x0, y0, x1, y1 = box
    return _neutrals(im[y0:y1, x0:x1], sat_max=0.15,
                     luma_lo=luma_lo, luma_hi=luma_hi)


def chart_white(im: np.ndarray, box: tuple[int, int, int, int]) -> Neutrals:
    """The white patch: exposure headroom / clipping check."""
    x0, y0, x1, y1 = box
    return _neutrals(im[y0:y1, x0:x1], sat_max=0.15, luma_lo=150, luma_hi=255)


def scene_neutrals(im: np.ndarray) -> Neutrals:
    """Whole-frame auto-neutral: low-sat mid/bright surfaces, median-based.

    ⚠ The masked population shifts when a camera's colour shifts (pixels enter/
    leave the low-sat mask), so this is NOT stable enough to servo against.
    Use global_means for before/after tracking on a static scene."""
    return _neutrals(im, sat_max=0.12, luma_lo=90, luma_hi=245)


def global_means(im: np.ndarray) -> Neutrals:
    """Whole-frame channel MEANS. The population never changes, so ratio
    SHIFTS on a static scene track the cast shift with sub-count resolution
    (absolute values are scene-coloured — only compare same-camera frames)."""
    R, G, B = im[..., 0], im[..., 1], im[..., 2]
    Y = 0.299 * R + 0.587 * G + 0.114 * B
    r, g, b = float(R.mean()), float(G.mean()), float(B.mean())
    return Neutrals(n=int(Y.size), r_g=r / g, b_g=b / g,
                    luma=0.299 * r + 0.587 * g + 0.114 * b,
                    clip_hi=float((Y >= 250).mean() * 100))
