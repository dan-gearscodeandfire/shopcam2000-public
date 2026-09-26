"""One decode convention for the whole fleet, or the numbers are fiction.

WHY THIS MODULE EXISTS (measured 2026-07-29)
--------------------------------------------
The fleet's cameras do not agree on how their video is tagged, and ffmpeg
honours the tags. Read the same clip set two ways and the *decoder* alone
invents differences far larger than anything worth servoing:

    CAM7  as-tagged -> forced full range:  luma 19.80 -> 24.22  (+22%)
                                           R/G  1.9118 -> 1.8102 (-5.3%)
                                           crush 73.4% -> 65.3%
    CAM2  as-tagged -> forced full range:  luma 51.85 -> 55.41  (+6.9%)
    CAM9  as-tagged -> forced full range:  luma 37.34 -> 48.34  (+29%)

Cause: CAM2, CAM7 and CAM9 emit **no VUI colour information at all**
(``yuv420p``, range/matrix/primaries all unset), so ffmpeg falls back to the
standard assumption for HD -- limited range, 16..235 -- and *expands* the
signal on RGB conversion. The other six are tagged ``yuvj420p`` / ``pc``
(full range) and are converted untouched.

But the tags are WRONG for the untagged ones. Read straight off the coded Y
plane, before any RGB conversion, every scene camera populates 0..255:

    cam   min  max      <16     >235   verdict
    CAM1    0  255  57.916%   0.000%  FULL
    CAM2    0  206  39.935%   0.000%  FULL   <- untagged, ffmpeg guessed limited
    CAM3    0  255  63.317%   0.035%  FULL
    CAM4    0  255  82.559%   0.144%  FULL
    CAM5    0  255  43.986%   0.001%  FULL
    CAM6    0  255  19.296%   0.863%  FULL
    CAM7    0  198  64.621%   0.000%  FULL   <- untagged, ffmpeg guessed limited
    CAM8    0  255  23.299%   0.021%  FULL
    CAM9   14  236   0.166%   0.003%  ambiguous (desktop capture, not a scene cam)

A limited-range encoder *cannot* emit samples outside 16..235. 40-65% of
CAM2's and CAM7's samples sit below 16, so their signal is full range and the
tag is simply absent. ``in_range=pc`` is therefore not a preference -- it is
the measured truth for every scene camera, and it is a no-op on the six that
already say so. One filter string is correct for all of them.

THE CONSEQUENCE THAT IS *NOT* OURS TO FIX
-----------------------------------------
An editor's NLE applies the same fallback ffmpeg does, so CAM2 and CAM7 will
land in the timeline looking crushed and contrasty next to everything else.
That is an **interpret-footage-on-import** task in the edit, not a camera-knob
task. Never spend camera gain closing a gap the decoder invented: the fix sits
in the wrong layer and it inverts the day a tag changes.

WHAT IS STILL UNKNOWN
---------------------
The *matrix* also disagrees and this module cannot fix it. Blue Iris
re-encodes the three USB cameras and tags them ``bt470bg`` (BT.601); the IP
cameras that tag anything say ``bt709``; CAM2/CAM6/CAM7/CAM9 say nothing.
Re-reading CAM5 as BT.709 moves its B/G by -6.7%. Where a tag exists it is
honoured (Blue Iris knows what matrix it encoded with); where none exists
BT.709 is assumed, which is the correct default for 1080p and is what ffmpeg
would do anyway. That assumption is recorded in every sample so a later
target-screen measurement can settle it.
"""
from __future__ import annotations

import dataclasses
import json
import pathlib
import subprocess

import numpy as np
from PIL import Image

from .site_paths import FFMPEG, FFPROBE  # PATH / Blue Iris / SHOPCAM_FFMPEG

#: The one filter every camera is decoded through. ``in_range=pc`` states the
#: measured truth (all scene cameras code full range) and is inert on clips
#: that already declare it, so the SAME string is right for the whole fleet.
#: The matrix is deliberately left to the tag -- see the module docstring.
DECODE_VF = "scale=in_range=pc:out_range=pc,format=rgb24"

#: Cameras whose streams carry no colour tags, so the decode convention is
#: asserted rather than read. Kept explicit: if this list ever stops matching
#: what ``probe_tags`` returns, a camera's firmware or stream config changed
#: and every stored reference number is suspect.
UNTAGGED = ("CAM2", "CAM7", "CAM9")


@dataclasses.dataclass(frozen=True)
class Tags:
    """The colour signalling of one clip, as the container declares it."""
    codec: str
    pix_fmt: str
    color_range: str
    matrix: str
    transfer: str
    primaries: str
    width: int
    height: int

    @property
    def declares_full_range(self) -> bool:
        return self.color_range == "pc" or self.pix_fmt.startswith("yuvj")

    @property
    def assumed_matrix(self) -> str:
        """What the decode will actually use for YUV->RGB."""
        return self.matrix if self.matrix not in ("", "-", "unknown") else "bt709 (assumed)"

    def key(self) -> str:
        """Compact signature for change detection between runs."""
        return (f"{self.codec}/{self.pix_fmt}/{self.color_range or '-'}/"
                f"{self.matrix or '-'}/{self.width}x{self.height}")


def probe_tags(path: pathlib.Path) -> Tags:
    r = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {path.name}: {r.stderr.strip()[:160]}")
    streams = json.loads(r.stdout).get("streams") or []
    if not streams:
        raise RuntimeError(f"{path.name} has no video stream")
    s = streams[0]
    g = lambda k: str(s.get(k, "") or "")
    return Tags(codec=g("codec_name"), pix_fmt=g("pix_fmt"),
                color_range=g("color_range"), matrix=g("color_space"),
                transfer=g("color_transfer"), primaries=g("color_primaries"),
                width=int(s.get("width") or 0), height=int(s.get("height") or 0))


def extract_frame(path: pathlib.Path, t: float, out: pathlib.Path) -> np.ndarray:
    """One frame at ``t`` seconds, decoded under the fleet convention.

    PNG, not JPEG: a second lossy generation would add its own chroma
    subsampling on top of the camera's, which is exactly the signal being
    measured. Returns float64 RGB so callers can hand it straight to
    ``chartmath`` / ``metrics``.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    # -ss BEFORE -i seeks by keyframe and is orders of magnitude faster on a
    # 70 s clip. Frame-exactness does not matter here: a colour measurement
    # wants a representative frame, not a specific one.
    r = subprocess.run(
        [FFMPEG, "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(path),
         "-frames:v", "1", "-vf", DECODE_VF, str(out)],
        capture_output=True, text=True, timeout=180)
    if r.returncode != 0 or not out.exists():
        raise RuntimeError(f"extract failed on {path.name} @ {t:.1f}s: "
                           f"{r.stderr.strip()[:160]}")
    return np.asarray(Image.open(out).convert("RGB")).astype(np.float64)


def coded_range_verdict(path: pathlib.Path, t: float = 30.0,
                        frames: int = 8) -> dict:
    """Is this clip's coded Y plane full range or limited range?

    The check that produced the table in the module docstring. Worth re-running
    whenever a camera is replaced or its stream reconfigured, because the whole
    decode contract rests on the answer. Reads the Y plane raw, before any RGB
    conversion, so nothing in the measurement path can bias it.
    """
    tags = probe_tags(path)
    w, h = tags.width, tags.height
    if not (w and h):
        raise RuntimeError(f"{path.name}: no dimensions to size the Y plane")
    frame_bytes = w * h * 3 // 2
    r = subprocess.run(
        [FFMPEG, "-v", "error", "-ss", f"{t:.3f}", "-i", str(path),
         "-frames:v", str(frames), "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"],
        capture_output=True, timeout=240)
    got = len(r.stdout) // frame_bytes
    if got == 0:
        raise RuntimeError(f"{path.name}: decoded no frames "
                           f"({len(r.stdout)} bytes)")
    y = np.concatenate([
        np.frombuffer(r.stdout[i * frame_bytes:i * frame_bytes + w * h],
                      dtype=np.uint8) for i in range(got)])
    below = float((y < 16).mean() * 100)
    above = float((y > 235).mean() * 100)
    if below > 0.5 or above > 0.5:
        verdict = "full"          # a studio-swing encoder cannot do this
    elif below == 0.0 and above == 0.0:
        verdict = "limited"
    else:
        verdict = "ambiguous"
    return dict(camera=path.name.split(".")[0], frames=got,
                y_min=int(y.min()), y_max=int(y.max()),
                pct_below_16=below, pct_above_235=above,
                verdict=verdict, tagged_full=tags.declares_full_range,
                tags=tags.key())


def audit(clips: dict[str, pathlib.Path],
          baseline: dict[str, str] | None = None) -> dict:
    """Phase-0 gate: read every camera's tags and compare against a baseline.

    A camera whose signature changed since the reference was captured
    invalidates that reference -- the pixels are being interpreted differently
    than when the target numbers were measured. Returns the findings rather
    than raising, so the caller decides whether that aborts a run or merely
    annotates a dry run.
    """
    tags = {cam: probe_tags(p) for cam, p in sorted(clips.items())}
    changed = {}
    if baseline:
        for cam, t in tags.items():
            was = baseline.get(cam)
            if was and was != t.key():
                changed[cam] = {"was": was, "now": t.key()}
    untagged_now = tuple(c for c, t in tags.items()
                         if not t.declares_full_range)
    matrices = {t.assumed_matrix for t in tags.values()}
    return {
        "signatures": {c: t.key() for c, t in tags.items()},
        "assumed_matrix": {c: t.assumed_matrix for c, t in tags.items()},
        "decode_vf": DECODE_VF,
        "untagged_range": untagged_now,
        "untagged_range_expected": tuple(c for c in UNTAGGED if c in tags),
        "untagged_unexpected": tuple(c for c in untagged_now
                                     if c not in UNTAGGED),
        "matrix_split": len(matrices) > 1,
        "matrices_in_play": sorted(matrices),
        "tags_changed": changed,
        # The edit-side consequence, stated in the run report so it reaches the
        # timeline instead of dying in a log.
        "edit_note": (
            "Interpret these clips as FULL range on import; the NLE will "
            "otherwise expand them and they will read crushed next to the "
            f"rest of the fleet: {', '.join(untagged_now) or 'none'}"),
    }
