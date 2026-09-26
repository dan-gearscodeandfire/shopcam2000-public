"""Measure ONE clip the way fleet_sample measures the fleet.

Same decode path, same whole_frame maths (crush = Y<16), so the numbers are
comparable with var/calibrate/samples/*.json. Reads only.
"""
import pathlib, sys
import numpy as np
from calibrate import decode, metrics

HERE = pathlib.Path(__file__).resolve().parent

def luma(im):
    a = im.astype(np.float64)
    return 0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2]

def measure(path, times):
    work = HERE / "var" / "calibrate" / "bisect" / "frames"
    work.mkdir(parents=True, exist_ok=True)
    rows = []
    for i, t in enumerate(times):
        out = work / ("f%02d.png" % i)
        im = decode.extract_frame(pathlib.Path(path), t, out)
        m = metrics.global_means(im)
        Y = luma(im)
        rows.append((m.r_g, m.b_g, float(Y.mean()), float((Y < 16).mean() * 100),
                     float((Y >= 240).mean() * 100),
                     float((im[..., 0] >= 250).mean() * 100)))
    a = np.array(rows)
    med = np.median(a, axis=0)
    print("%-40s R/G %.4f  B/G %.4f  luma %6.2f  crush %5.2f%%  hot %5.2f%%  Rpeg %5.2f%%"
          % (pathlib.Path(path).name, med[0], med[1], med[2], med[3], med[4], med[5]))

if __name__ == "__main__":
    for p in sys.argv[1:]:
        measure(p, [10.0, 20.0, 30.0, 40.0, 50.0])
