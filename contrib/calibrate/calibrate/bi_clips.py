"""Find the clip set Blue Iris actually wrote, and refuse the ones it only listed.

The measurement plane for calibration is **what Blue Iris saved**, not a live
snapshot: the recorded file is the thing the edit cuts together, and a read-back
of a camera's own settings is a claim while the written file is evidence.

One "That Was Awesome" press produces one clip per camera, all covering the
same wall-clock window, which is what makes a cross-camera comparison legal at
all (see ``same-regime-control-frames``: every camera measured in the same
instant, with the reference camera present in the same sample as its own drift
control).

Traps this module exists to absorb, all of them previously measured on this rig:

* **A clip is listed before it is finished.** A freshly stopped 7-second
  recording reported ``1 sec (48B)``. Sizes are polled until they stop moving.
* **Blue Iris can list a clip with no decodable video** (1 in 99, inside a press
  that reported no failures). Nothing but a decode proves a file has pixels, so
  every clip in the set is decoded before it is used.
* **A press can tile into two files** with a real sub-second hole between them.
  For measurement the longest file in the window is used and the tiling is
  recorded; frames are taken from the middle, far from either boundary.
* **A camera can be missing entirely.** Missing is reported as an error, never
  as a smaller fleet -- the same discipline ``snapshot_fleet_state.py`` uses,
  because an invisible gap reads as a clean run.
* **Names are the trigger time, not the footage time**, and they differ by a
  second or two between cameras, so a set is a time *cluster*, not an exact
  string match.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import pathlib
import re
import subprocess
import time

from . import decode

from .site_paths import CLIP_DIR  # SHOPCAM_CLIP_DIR, default C:\BlueIris\New

#: ``CAM1.20260729_175745Z.mp4`` -- camera, UTC stamp, extension.
NAME_RE = re.compile(r"^(?P<cam>CAM\d+)\.(?P<stamp>\d{8}_\d{6})Z\.(?P<ext>mp4|bvr)$",
                     re.IGNORECASE)

#: CAM9 is a desktop capture with no image settings and no scene; it is carried
#: through the sampler for completeness but never calibrated.
SCENE_CAMERAS = ("CAM1", "CAM2", "CAM3", "CAM4", "CAM5", "CAM6", "CAM7", "CAM8")
ALL_CAMERAS = SCENE_CAMERAS + ("CAM9",)


@dataclasses.dataclass
class Clip:
    camera: str
    path: pathlib.Path
    stamp: dt.datetime
    size: int
    duration: float | None = None
    tags: decode.Tags | None = None
    tiled_with: int = 0          # other files for this camera in the window
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.duration)

    def sample_times(self, n: int, edge: float = 4.0) -> list[float]:
        """``n`` sample points spread across the clip, clear of both ends.

        ``edge`` keeps samples away from the pre-roll boundary at the head and
        from a tiling seam at the tail -- the two places where a frame may come
        from a different regime than the rest of the clip.
        """
        if not self.duration:
            return []
        lo, hi = edge, max(edge, self.duration - edge)
        if hi <= lo:
            return [self.duration / 2]
        if n == 1:
            return [(lo + hi) / 2]
        step = (hi - lo) / (n - 1)
        return [lo + i * step for i in range(n)]


@dataclasses.dataclass
class ClipSet:
    stamp: dt.datetime
    clips: dict[str, Clip]
    missing: tuple[str, ...]

    @property
    def label(self) -> str:
        return self.stamp.strftime("%Y%m%d_%H%M%SZ")

    @property
    def usable(self) -> dict[str, Clip]:
        return {c: k for c, k in self.clips.items() if k.ok}

    @property
    def failures(self) -> dict[str, str]:
        out = {c: k.error or "no duration" for c, k in self.clips.items() if not k.ok}
        out.update({c: "no clip in this set" for c in self.missing})
        return out

    def summary(self) -> str:
        lines = [f"clip set {self.label}  "
                 f"{len(self.usable)}/{len(self.clips) + len(self.missing)} usable"]
        for cam in ALL_CAMERAS:
            k = self.clips.get(cam)
            if k is None:
                lines.append(f"  {cam}  MISSING")
            elif not k.ok:
                lines.append(f"  {cam}  UNUSABLE  {k.error}")
            else:
                extra = f"  (+{k.tiled_with} more file(s) -- press tiled)" if k.tiled_with else ""
                lines.append(f"  {cam}  {k.duration:5.1f}s  {k.size / 1e6:6.1f} MB  "
                             f"{k.tags.key() if k.tags else '?'}{extra}")
        return "\n".join(lines)


def _parse(path: pathlib.Path) -> tuple[str, dt.datetime] | None:
    m = NAME_RE.match(path.name)
    if not m:
        return None
    stamp = dt.datetime.strptime(m["stamp"], "%Y%m%d_%H%M%S").replace(
        tzinfo=dt.timezone.utc)
    return m["cam"].upper(), stamp


def _probe_duration(path: pathlib.Path) -> float:
    r = subprocess.run(
        [decode.FFPROBE, "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=90)
    try:
        return float(r.stdout.strip().split(",")[0])
    except (ValueError, IndexError):
        raise RuntimeError(f"no duration: {r.stderr.strip()[:120] or 'ffprobe said nothing'}")


def find_sets(clip_dir: pathlib.Path = CLIP_DIR,
              tolerance_s: float = 5.0) -> list[ClipSet]:
    """Every clip in the directory, clustered into press-sets, newest first.

    Cameras stamp a press up to a second or two apart, so clips are grouped by
    proximity in time rather than by an identical name -- and a camera that
    wrote two files for one press (tiling) contributes both, with the longest
    chosen later.
    """
    found: list[tuple[str, dt.datetime, pathlib.Path]] = []
    for p in clip_dir.iterdir():
        parsed = _parse(p)
        if parsed and p.is_file():
            found.append((parsed[0], parsed[1], p))
    found.sort(key=lambda t: t[1])

    clusters: list[list[tuple[str, dt.datetime, pathlib.Path]]] = []
    for item in found:
        if clusters and (item[1] - clusters[-1][-1][1]).total_seconds() <= tolerance_s:
            clusters[-1].append(item)
        else:
            clusters.append([item])

    sets: list[ClipSet] = []
    for cluster in clusters:
        by_cam: dict[str, list[tuple[dt.datetime, pathlib.Path]]] = {}
        for cam, stamp, path in cluster:
            by_cam.setdefault(cam, []).append((stamp, path))
        clips: dict[str, Clip] = {}
        for cam, items in by_cam.items():
            # Largest file first: on a tiled press the long file holds the
            # buffer and the short one holds the seam.
            items.sort(key=lambda sp: sp[1].stat().st_size, reverse=True)
            stamp, path = items[0]
            clips[cam] = Clip(camera=cam, path=path, stamp=stamp,
                              size=path.stat().st_size,
                              tiled_with=len(items) - 1)
        sets.append(ClipSet(stamp=min(c[1] for c in cluster), clips=clips,
                            missing=tuple(c for c in ALL_CAMERAS if c not in clips)))
    sets.sort(key=lambda s: s.stamp, reverse=True)
    return sets


def settle(clip_set: ClipSet, timeout_s: float = 45.0,
           quiet_s: float = 3.0, poll_s: float = 1.5) -> ClipSet:
    """Wait until every file in the set stops growing.

    Blue Iris lists a clip while it is still being written and the reported
    size and duration are nonsense until it finishes. Growth is the signal:
    two consecutive polls with no size change on any file means the set has
    landed. Times out rather than blocking a run forever -- the caller sees a
    partially settled set and can decide.
    """
    deadline = time.monotonic() + timeout_s
    last = {c: -1 for c in clip_set.clips}
    stable_since: float | None = None
    while time.monotonic() < deadline:
        moved = False
        for cam, clip in clip_set.clips.items():
            try:
                size = clip.path.stat().st_size
            except OSError:
                moved = True
                continue
            if size != last[cam]:
                moved = True
                last[cam] = size
                clip.size = size
        now = time.monotonic()
        if moved:
            stable_since = None
        elif stable_since is None:
            stable_since = now
        elif now - stable_since >= quiet_s:
            return clip_set
        time.sleep(poll_s)
    return clip_set


def verify(clip_set: ClipSet, work_dir: pathlib.Path) -> ClipSet:
    """Probe and DECODE every clip. A listing is not evidence of pixels.

    Blue Iris has been observed listing a clip containing no decodable video,
    once in 99 presses, inside a press that reported no failures at all. The
    only test that catches it is decoding a frame, so that is the test.
    """
    for cam, clip in clip_set.clips.items():
        try:
            clip.duration = _probe_duration(clip.path)
            clip.tags = decode.probe_tags(clip.path)
            if clip.duration < 2.0:
                clip.error = f"only {clip.duration:.2f}s long -- not a usable sample"
                continue
            probe_at = clip.sample_times(1)[0]
            decode.extract_frame(clip.path, probe_at,
                                 work_dir / "verify" / f"{cam}.png")
        except Exception as e:                      # noqa: BLE001 -- report, never raise
            clip.error = f"{type(e).__name__}: {e}"[:200]
    return clip_set


def newest_usable_set(clip_dir: pathlib.Path = CLIP_DIR,
                      work_dir: pathlib.Path | None = None,
                      max_age_s: float | None = None,
                      do_settle: bool = True) -> ClipSet:
    """The freshest press-set on disk, settled and decode-verified.

    ``max_age_s`` guards against silently measuring hours-old footage when a
    press failed to record: the room, the light and the framing have all had
    time to change, and a stale sample is worse than no sample because it looks
    exactly like a fresh one.
    """
    work_dir = work_dir or pathlib.Path.cwd() / "var" / "calibrate" / "frames"
    sets = find_sets(clip_dir)
    if not sets:
        raise RuntimeError(f"no Blue Iris clips found in {clip_dir}")
    latest = sets[0]
    if max_age_s is not None:
        age = (dt.datetime.now(dt.timezone.utc) - latest.stamp).total_seconds()
        if age > max_age_s:
            raise RuntimeError(
                f"newest clip set {latest.label} is {age / 60:.1f} min old "
                f"(limit {max_age_s / 60:.1f} min) -- press again rather than "
                f"measuring stale footage")
    if do_settle:
        settle(latest)
    return verify(latest, work_dir)
