"""Is this camera's coded signal FULL range or LIMITED range?

The tag can be absent or wrong; the coded samples cannot. A limited-range
(studio-swing) encoder confines Y to 16..235 and never emits values outside it.
A full-range encoder uses 0..255. So read the decoded Y plane raw -- before any
RGB conversion -- and look at where the population actually lives.

Reads several frames because one frame of a dark shop may simply not contain
any highlights; the LOW tail is the discriminating one here.
"""
from __future__ import annotations
import datetime as dt
import pathlib, re, subprocess, sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from calibrate.site_paths import FFMPEG, FFPROBE, CLIP_DIR as CLIPS  # noqa: E402


def dimensions(path: pathlib.Path) -> tuple[int, int]:
    """The clip's OWN coded size. The fleet is no longer all 1080p -- CAM7
    records 2560x1440 -- and a wrong frame size silently shreds the Y-plane
    arithmetic while still yielding a confident verdict. Never scale to a
    common size: interpolation invents sample values, and which values exist
    is the entire question here.
    """
    r = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=width,height",
                        "-of", "csv=p=0:s=x", str(path)],
                       capture_output=True, text=True, timeout=60)
    w, h = r.stdout.strip().split("x")[:2]
    return int(w), int(h)


def y_planes(path: pathlib.Path, t: float, n: int) -> np.ndarray:
    """n consecutive frames' Y planes, straight out of the decoder."""
    w, h = dimensions(path)
    frame_bytes = w * h * 3 // 2
    r = subprocess.run([FFMPEG, "-v", "error", "-ss", str(t), "-i", str(path),
                        "-frames:v", str(n), "-f", "rawvideo",
                        "-pix_fmt", "yuv420p", "-"],
                       capture_output=True, timeout=180)
    buf = r.stdout
    got = len(buf) // frame_bytes
    if got == 0:
        raise RuntimeError(f"no frames ({len(buf)} bytes; {r.stderr[:120]!r})")
    ys = [np.frombuffer(buf[i * frame_bytes:i * frame_bytes + w * h], dtype=np.uint8)
          for i in range(got)]
    return np.concatenate(ys)


def newest_stamp() -> str:
    """The most recent clip token actually on disk.

    The old default was a hardcoded 2026-07-29 stamp, so a bare run printed
    "no clip" nine times and read as a dead rig rather than a stale argument.
    """
    # By MTIME, not by name: sorted() is alphabetical, so CAM9 always won and the
    # probe adopted CAM9's second -- which is routinely one off the other eight.
    paths = sorted(CLIPS.glob("CAM*_*Z.mp4"), key=lambda p: p.stat().st_mtime)
    if not paths:
        return "00000000_000000"
    m = re.search(r"\.(\d{8}_\d{6})Z", paths[-1].name)
    return m.group(1) if m else "00000000_000000"


def find_clip(cam: str, stamp: str, tolerance_s: int = 4):
    """The camera's clip for this press, allowing for a straddled second.

    Cameras do not all open their file in the same second, so demanding an exact
    token match drops most of the fleet. Take the nearest clip within a few
    seconds and treat that as the same event -- the same tolerance grouping
    calibrate/bi_clips.py uses.
    """
    target = dt.datetime.strptime(stamp, "%Y%m%d_%H%M%S")
    best, best_gap = None, None
    for p in CLIPS.glob(f"{cam}.{stamp[:8]}_*Z.mp4"):
        m = re.search(r"\.(\d{8}_\d{6})Z", p.name)
        if not m:
            continue
        gap = abs((dt.datetime.strptime(m.group(1), "%Y%m%d_%H%M%S") - target)
                  .total_seconds())
        if gap <= tolerance_s and (best_gap is None or gap < best_gap):
            best, best_gap = p, gap
    return best


def main() -> None:
    stamp = sys.argv[1] if len(sys.argv) > 1 else newest_stamp()
    t = float(sys.argv[2]) if len(sys.argv) > 2 else 30.0
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 8
    print(f"coded Y-plane population, {n} frames from t={t}s\n")
    print(f"{'cam':5s} {'min':>4s} {'max':>4s} {'<16':>8s} {'>235':>8s} "
          f"{'=16':>7s} {'=235':>7s}  verdict")
    for cam in [f"CAM{i}" for i in range(1, 10)]:
        clip = find_clip(cam, stamp)
        if clip is None:
            print(f"{cam:5s} no clip"); continue
        try:
            y = y_planes(clip, t, n)
        except Exception as e:
            print(f"{cam:5s} ERROR {e}"); continue
        below = float((y < 16).mean() * 100)
        above = float((y > 235).mean() * 100)
        at_lo = float((y == 16).mean() * 100)
        at_hi = float((y == 235).mean() * 100)
        # A limited-range encoder cannot emit outside 16..235. Anything more than
        # a rounding-noise fraction below/above proves a full-range signal.
        if below > 0.5 or above > 0.5:
            verdict = "FULL range (samples outside 16..235)"
        elif below == 0.0 and above == 0.0:
            verdict = "LIMITED range (clean 16..235 clamp)"
        else:
            verdict = f"ambiguous (tiny tails {below:.3f}/{above:.3f})"
        print(f"{cam:5s} {y.min():4d} {y.max():4d} {below:7.3f}% {above:7.3f}% "
              f"{at_lo:6.3f}% {at_hi:6.3f}%  {verdict}")


if __name__ == "__main__":
    main()
