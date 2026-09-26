"""Measure exposure + colour of grabbed frames so 'match to CAM1' is a number,
not an eyeball. Global stats first (whole frame), then an optional neutral ROI.

    python tools/frame_metrics.py DIR CAM1 CAM2 CAM4 CAM5 CAM7

Prints, per image, relative to the FIRST camera listed (the reference):
  luma mean / p5 / p95 / %clip, per-channel R,G,B means, and the grey-world
  white-balance ratios R/G and B/G (1.00 = neutral under grey-world).
"""
from __future__ import annotations
import pathlib, sys
import numpy as np
from PIL import Image

def stats(path: pathlib.Path) -> dict:
    im = np.asarray(Image.open(path).convert("RGB")).astype(np.float64)
    R, G, B = im[..., 0], im[..., 1], im[..., 2]
    # Rec.601 luma
    Y = 0.299 * R + 0.587 * G + 0.114 * B
    # Neutral-surface WB: among low-saturation, mid-high-luma pixels (white
    # cabinets / walls / grey tools common to every frame), take the mean
    # channel ratios. Content-robust — strips out each scene's coloured objects.
    mx = np.maximum(np.maximum(R, G), B)
    mn = np.minimum(np.minimum(R, G), B)
    sat = (mx - mn) / (mx + 1e-6)
    neutral = (sat < 0.12) & (Y > 90) & (Y < 245)
    frac = float(neutral.mean() * 100)
    if neutral.sum() > 500:
        nr, ng, nb = R[neutral].mean(), G[neutral].mean(), B[neutral].mean()
        n_rg, n_bg, n_luma = nr / ng, nb / ng, (0.299*nr+0.587*ng+0.114*nb)
    else:
        n_rg = n_bg = n_luma = float("nan")
    return dict(
        luma=Y.mean(), p5=np.percentile(Y, 5), p50=np.percentile(Y, 50),
        p95=np.percentile(Y, 95),
        clip_lo=float((Y < 4).mean() * 100), clip_hi=float((Y > 251).mean() * 100),
        r=R.mean(), g=G.mean(), b=B.mean(),
        rg=R.mean() / G.mean(), bg=B.mean() / G.mean(),
        n_rg=n_rg, n_bg=n_bg, n_luma=n_luma, n_frac=frac,
    )

def main() -> None:
    d = pathlib.Path(sys.argv[1]); cams = sys.argv[2:]
    ref = None
    print("== GLOBAL (whole frame — confounded by scene content) ==")
    hdr = f"{'cam':5} {'luma':>6} {'p5':>5} {'p95':>5} {'clipLo%':>7} {'clipHi%':>7} " \
          f"{'R/G':>5} {'B/G':>5}  vs-ref"
    print(hdr); print("-" * len(hdr))
    rows = {cam: stats(d / f"{cam.lower()}.jpg") for cam in cams}
    for cam in cams:
        s = rows[cam]
        if ref is None:
            ref = s; rel = "(reference)"
        else:
            rel = (f"dLuma {s['luma']-ref['luma']:+5.1f}  "
                   f"R/G {(s['rg']/ref['rg']-1)*100:+4.1f}%  "
                   f"B/G {(s['bg']/ref['bg']-1)*100:+4.1f}%")
        print(f"{cam:5} {s['luma']:6.1f} {s['p5']:5.0f} {s['p95']:5.0f} "
              f"{s['clip_lo']:7.2f} {s['clip_hi']:7.2f} "
              f"{s['rg']:5.3f} {s['bg']:5.3f}  {rel}")

    print("\n== NEUTRAL-SURFACE (low-sat bright pixels — content-robust) ==")
    hdr2 = f"{'cam':5} {'neutral%':>8} {'n_luma':>7} {'n_R/G':>6} {'n_B/G':>6}  vs-ref"
    print(hdr2); print("-" * len(hdr2))
    refn = rows[cams[0]]
    for cam in cams:
        s = rows[cam]
        if cam == cams[0]:
            rel = "(reference)"
        else:
            rel = (f"nLuma {s['n_luma']-refn['n_luma']:+5.1f}  "
                   f"R/G {(s['n_rg']/refn['n_rg']-1)*100:+5.1f}%  "
                   f"B/G {(s['n_bg']/refn['n_bg']-1)*100:+5.1f}%")
        print(f"{cam:5} {s['n_frac']:8.1f} {s['n_luma']:7.1f} "
              f"{s['n_rg']:6.3f} {s['n_bg']:6.3f}  {rel}")

if __name__ == "__main__":
    main()
