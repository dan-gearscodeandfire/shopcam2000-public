"""Measure the whole CAM7 verification clip, head to tail.

The press came >60 s after the write, so the pre-roll ALREADY carries the new
state and a FLAT head-to-tail profile is the proof it settled
(`measure-the-tail-not-the-preroll`, learned on CAM7 the hard way).
"""
import glob, os, pathlib, sys
import numpy as np
from calibrate import decode, metrics
from calibrate.site_paths import CLIP_DIR

path = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(
    sorted(glob.glob(str(CLIP_DIR / "CAM7*.mp4")), key=os.path.getmtime)[-1])
work = pathlib.Path(__file__).resolve().parent / "var" / "calibrate" / "cam7-verify-2026-08-22"
work.mkdir(parents=True, exist_ok=True)
print(path.name, "dur %.1f s" % decode.probe(path).duration
      if hasattr(decode, "probe") else path.name)

rows = []
for t in (5.0, 20.0, 35.0, 50.0, 62.0):
    out = work / ("t%02d.png" % int(t))
    if out.exists():
        out.unlink()          # boxes-go-stale-silently: never read a leftover
    try:
        im = decode.extract_frame(path, t, out)
    except Exception as exc:
        print("  t=%-5.1f EXTRACT FAILED %s" % (t, exc))
        continue
    if not out.exists():
        print("  t=%-5.1f no frame" % t)
        continue
    m = metrics.global_means(im)
    a = im.astype(np.float64)
    Y = 0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2]
    w = ((a[..., 0] >= 250) & (a[..., 1] >= 250) & (a[..., 2] >= 250)).mean() * 100
    rows.append((Y.mean(), (Y < 16).mean() * 100, (Y >= 240).mean() * 100, w,
                 m.r_g, m.b_g))
    print("  t=%-5.1f luma %6.2f  crush %5.2f%%  hot %5.2f%%  blown-white %5.2f%%  "
          "R/G %.4f  B/G %.4f" % (t, rows[-1][0], rows[-1][1], rows[-1][2],
                                  rows[-1][3], rows[-1][4], rows[-1][5]))
if rows:
    med = np.median(np.array(rows), axis=0)
    print("MEDIAN  luma %6.2f  crush %5.2f%%  hot %5.2f%%  blown-white %5.2f%%  "
          "R/G %.4f  B/G %.4f" % (med[0], med[1], med[2], med[3], med[4], med[5]))
    print("head-to-tail luma spread: %.2f" % (max(r[0] for r in rows) -
                                              min(r[0] for r in rows)))
