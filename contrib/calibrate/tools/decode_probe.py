"""How much of a cross-camera difference is the DECODER, not the camera?

Extracts the same frame from the same clip under different decode conventions
and measures what changed. Nothing about the camera moves between the two
readings, so any difference is manufactured by the decode path alone.
"""
from __future__ import annotations
import json, pathlib, subprocess, sys, tempfile

import numpy as np
from PIL import Image

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from calibrate.site_paths import FFMPEG, FFPROBE, CLIP_DIR as CLIPS  # noqa: E402


def tags(path: pathlib.Path) -> dict:
    r = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream", "-of", "json", str(path)],
                       capture_output=True, text=True, timeout=60)
    s = json.loads(r.stdout)["streams"][0]
    return {k: s.get(k, "-") for k in ("codec_name", "pix_fmt", "color_range",
                                       "color_space", "color_transfer", "color_primaries")}


def frame(path: pathlib.Path, t: float, vf: str, out: pathlib.Path) -> np.ndarray:
    cmd = [FFMPEG, "-v", "error", "-y", "-ss", str(t), "-i", str(path),
           "-frames:v", "1"]
    if vf:
        cmd += ["-vf", vf]
    cmd += [str(out)]
    subprocess.run(cmd, check=True, capture_output=True, timeout=120)
    return np.asarray(Image.open(out).convert("RGB")).astype(np.float64)


def stats(im: np.ndarray) -> dict:
    R, G, B = im[..., 0], im[..., 1], im[..., 2]
    Y = 0.299 * R + 0.587 * G + 0.114 * B
    return dict(luma=float(Y.mean()), rg=float(R.mean() / G.mean()),
                bg=float(B.mean() / G.mean()),
                p1=float(np.percentile(Y, 1)), p99=float(np.percentile(Y, 99)),
                crush=float((Y < 16).mean() * 100), clip=float((Y >= 250).mean() * 100))


def main() -> None:
    stamp = sys.argv[1] if len(sys.argv) > 1 else "20260729_175745"
    t = float(sys.argv[2]) if len(sys.argv) > 2 else 30.0
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="decodeprobe"))
    print(f"frame at t={t}s, clip set {stamp}\n")

    modes = {
        "as-tagged": "",
        "force-full": "scale=in_range=pc:out_range=pc,format=rgb24",
        "force-limited": "scale=in_range=tv:out_range=pc,format=rgb24",
        "force-709-full": "scale=in_color_matrix=bt709:in_range=pc:out_range=pc,format=rgb24",
        "force-601-full": "scale=in_color_matrix=bt470bg:in_range=pc:out_range=pc,format=rgb24",
    }

    for cam in [f"CAM{i}" for i in range(1, 10)]:
        hits = sorted(CLIPS.glob(f"{cam}.{stamp[:8]}_*Z.mp4"))
        hits = [h for h in hits if stamp[9:] in h.name] or hits
        if not hits:
            print(f"{cam}: no clip"); continue
        clip = hits[0]
        tg = tags(clip)
        print(f"{cam}  {tg['codec_name']:5s} {tg['pix_fmt']:9s} range={tg['color_range']:8s} "
              f"matrix={tg['color_space']:9s} prim={tg['color_primaries']}")
        base = None
        for label, vf in modes.items():
            try:
                im = frame(clip, t, vf, tmp / f"{cam}-{label}.png")
            except subprocess.CalledProcessError as e:
                print(f"    {label:16s} FAILED {e.stderr.decode()[:80]}")
                continue
            s = stats(im)
            if base is None:
                base = s
                print(f"    {label:16s} luma {s['luma']:6.2f}  R/G {s['rg']:.4f}  "
                      f"B/G {s['bg']:.4f}  p1 {s['p1']:5.1f} p99 {s['p99']:5.1f} "
                      f"crush {s['crush']:5.2f}%")
            else:
                print(f"    {label:16s} luma {s['luma']:6.2f} ({s['luma']-base['luma']:+6.2f})  "
                      f"R/G {s['rg']:.4f} ({(s['rg']/base['rg']-1)*100:+5.2f}%)  "
                      f"B/G {s['bg']:.4f} ({(s['bg']/base['bg']-1)*100:+5.2f}%)  "
                      f"p1 {s['p1']:5.1f} p99 {s['p99']:5.1f} crush {s['crush']:5.2f}%")
        print()
    print(f"frames kept in {tmp}")


if __name__ == "__main__":
    main()
