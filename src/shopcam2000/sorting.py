"""Put the nine files a press wrote into one folder named for when it happened.

The problem this solves is not storage, it is *finding*. Blue Iris writes every
camera's clip into one flat directory, so a press produces nine files that are
adjacent in a listing of two hundred and adjacent to nothing in an edit. The
answer is a folder per event, named in local time, with the clips inside and a
manifest saying what the event was.

🔑 **Why this lives in the Controller and not in a folder watcher.** A watcher
sees filenames; the Controller saw the press. Two consequences, both measured:

1. **A press straddles a second.** Blue Iris names clips ``CAM8.20260730_032815Z.mp4``
   - camera, then the clip's *start* in UTC. Nine cameras do not all start in the
   same second: of 28 press-groups in ``C:\\BlueIris\\New`` on 2026-07-30, **9 were
   split across two adjacent seconds** (7/2, 8/1, 8/1 …). A watcher grouping by
   the timestamp token therefore shreds roughly a third of all presses into two
   folders. The Controller knows a press triggered nine named cameras and asks
   Blue Iris which file each one wrote, so the grouping is exact rather than
   inferred.

2. **"Is the file finished?" already has a real answer here.** A watcher has to
   guess with size-stability polling, which is *known wrong on this rig*: a file
   can be size-stable and undecodable (the 5.56 MB clip with no H.264 start code,
   2026-07-28) and decodable while still growing. ``Controller._verify_twab``
   already waits out Blue Iris's break time and then **ffprobes** every clip. By
   the time this module is called the file has been opened and played. That is
   the strongest completion test available and it costs nothing extra.

Everything here is best-effort and swallows its own errors. Sorting is a
convenience laid on top of footage that is already safely on disk; a bug in the
convenience layer must never cost a take.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Copy under a temporary name and rename into place. A rename on the same volume
# is atomic, so an interrupted copy leaves a visible ``.part`` rather than a
# short file that looks like footage.
PART_SUFFIX = ".part"

# Name of the manifest dropped beside the clips.
MANIFEST_NAME = "shopcam.json"


def _local_stamp(epoch: float) -> str:
    """``2026-07-29_23-28-15`` - the folder name, in the operator's own timezone.

    Deliberately *not* the UTC token from the filenames. The clips keep their
    Blue Iris names, so nothing is lost, but the folder answers "when did I shoot
    this" the way a human asks it. An evening press reads as that evening rather
    than as the next morning.
    """
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d_%H-%M-%S")


# Blue Iris clip names: camera, then the clip's trigger moment in UTC, then Z.
CLIP_NAME_RE = re.compile(
    r"^(?P<cam>[A-Za-z0-9_-]+)\.(?P<stamp>\d{8}_\d{6})Z(?P<ext>\.[A-Za-z0-9]+)$")


def _local_clip_name(name: str, started_at: float | None = None) -> str:
    """``CAM1.20260801_031952Z.mp4`` -> ``CAM1.20260731_231853.mp4``.

    The sorted copy is named the way the operator thinks about it, in local
    time, so a folder and the files inside it finally agree. Two deliberate
    choices:

    * **Local, not UTC.** The UTC token is why an evening press read as the next
      morning. The folder was already local; the files were not, and the
      mismatch was the whole complaint (user, 2026-07-31).
    * **The clip's real START, not Blue Iris's token.** BI stamps the file with
      the *trigger* moment, but the clip begins ~60 s earlier because of
      pre-roll. ``started_at`` is Blue Iris's own record of when the file
      actually opened, which is what the folder name already uses, so the two
      now derive from the same instant.

    Falls back to converting the UTC token when no start is known, and returns
    unrecognised names untouched - a name this cannot parse is a name it must
    not mangle.
    """
    m = CLIP_NAME_RE.match(name)
    if not m:
        return name
    if started_at:
        when = datetime.fromtimestamp(float(started_at))
    else:
        when = (datetime.strptime(m["stamp"], "%Y%m%d_%H%M%S")
                .replace(tzinfo=timezone.utc).astimezone())
    return f"{m['cam']}.{when.strftime('%Y%m%d_%H%M%S')}{m['ext']}"


class ClipSorter:
    """Copies a group of clips into one timestamped folder.

    Copy, never move: Blue Iris's clip database indexes the originals, and moving
    them empties the timeline and breaks the Controller's own ``/api/clip`` proxy.
    The cost is duplicate bytes - a nine-camera press is ~460 MB, so a sorted copy
    of it is another ~460 MB - which is what ``sort_min_free_gb`` is guarding.
    """

    def __init__(self, config) -> None:
        self.config = config

    # ------------------------------------------------------------------ paths

    @property
    def enabled(self) -> bool:
        return bool(self.config.recording.sort_clips)

    @property
    def clip_dir(self) -> Path:
        return Path(self.config.recording.clip_dir)

    @property
    def sort_root(self) -> Path:
        """Where event folders are created.

        Empty config means "inside the clip directory", i.e. the batches land one
        level deeper than the flat pile they came from.

        ⚠️ ``C:\\BlueIris\\New`` is a folder Blue Iris *manages*: the registry has
        it at ``action=2`` (move to Stored) with ``archdays=7`` and a 300 GB cap.
        Blue Iris archives out of its clip database rather than by walking the
        filesystem, so copies it has no record of should be left alone - but that
        is reasoning, not a measurement. Point ``sort_dir`` at a folder outside
        ``New`` (``C:\\BlueIris\\Sorted``) for zero exposure to that question.
        """
        configured = (self.config.recording.sort_dir or "").strip()
        return Path(configured) if configured else self.clip_dir

    # ------------------------------------------------------------------- main

    async def sort(self, kind: str, clips: list[dict], *,
                   event_at: float | None = None,
                   seq: int | None = None,
                   extra: dict[str, Any] | None = None) -> dict[str, Any]:
        """Copy ``clips`` into one folder. Returns a summary for the receipt.

        ``clips`` are Blue Iris clip records - ``camera``, ``file``, ``date``
        (epoch seconds), optionally ``seconds``/``ok``/``why``. Never raises.
        """
        if not self.enabled:
            return {"sorted": False, "reason": "disabled"}
        try:
            return await asyncio.to_thread(
                self._sort_blocking, kind, clips, event_at, seq, extra or {})
        except Exception as exc:  # pragma: no cover - the net must not fall
            log.exception("sort: failed to file %s clips", kind)
            return {"sorted": False, "reason": f"{type(exc).__name__}: {exc}"}

    # --------------------------------------------------------------- internals

    def _sort_blocking(self, kind: str, clips: list[dict],
                       event_at: float | None, seq: int | None,
                       extra: dict[str, Any]) -> dict[str, Any]:
        usable = [c for c in clips if c.get("file")]
        if not usable:
            return {"sorted": False, "reason": "no clips to file"}

        clip_dir = self.clip_dir
        if not clip_dir.is_dir():
            log.warning("sort: clip directory %s is not reachable; not filing %s",
                        clip_dir, kind)
            return {"sorted": False, "reason": f"{clip_dir} unreachable"}

        sources = [(c, clip_dir / str(c["file"])) for c in usable]
        wanted = sum(p.stat().st_size for _, p in sources if p.exists())

        # 🔴 Refuse rather than fill the disk that is also recording. 21.7 GB/hr
        # all-9 (measured 2026-07-28) means headroom disappears faster than an
        # operator will notice, and a full disk stops Blue Iris writing at all.
        ok, free = self._space_ok(wanted)
        if not ok:
            log.error("sort: NOT filing %s - %.1f GB free, need %.1f GB plus a "
                      "%.1f GB floor. Footage is safe in %s; only the sorted copy "
                      "was skipped.",
                      kind, free / 1e9, wanted / 1e9,
                      self.config.recording.sort_min_free_gb, clip_dir)
            return {"sorted": False, "reason": "not enough free space",
                    "freeBytes": free, "neededBytes": wanted}

        # Name the folder for the earliest clip *start*, not for the press. The
        # clips carry ~60 s of pre-roll, so the press happened a minute after the
        # footage begins, and "the same timestamp as the files" is what makes the
        # folder findable next to the filenames inside it.
        starts = [float(c.get("date") or 0) for c in usable if c.get("date")]
        stamp_at = min(starts) if starts else (event_at or time.time())
        # Identifies the event, so a re-run refills its own folder instead of
        # being handed a suffixed duplicate. A press has a seq; a take is
        # identified by when it started.
        token = f"{kind}:{seq if seq is not None else int(event_at or stamp_at)}"
        dest = self._reserve(self.sort_root, _local_stamp(stamp_at), token)

        copied: list[dict[str, Any]] = []
        failed: dict[str, str] = {}
        total = 0
        for clip, src in sources:
            camera = str(clip.get("camera") or "?")
            try:
                filed = _local_clip_name(src.name, clip.get("date"))
                size = self._copy_one(src, dest / filed)
            except FileNotFoundError:
                # Blue Iris rotated it out from under us, or never wrote it.
                failed[camera] = f"{src.name}: not on disk"
                continue
            except OSError as exc:
                failed[camera] = f"{src.name}: {exc.strerror or exc}"
                log.error("sort: could not copy %s: %s", src, exc)
                continue
            total += size
            msec = clip.get("msec")
            copied.append({
                "camera": camera,
                "file": filed,
                # The Blue Iris original keeps its UTC name in the flat
                # clip dir; this is the thread back to it and to BI's DB.
                "sourceFile": src.name,
                "bytes": size,
                "startedAt": clip.get("date"),
                # The press path measures duration with ffprobe; the take path
                # only has Blue Iris's own figure, in milliseconds.
                "seconds": clip.get("seconds") if clip.get("seconds") is not None
                           else (round(msec / 1000.0, 1) if msec else None),
                # Carried through from the ffprobe verifier so the manifest says
                # whether this file was ever actually opened and played.
                "decoded": clip.get("ok"),
                "why": clip.get("why") or "",
                "spans": clip.get("spans"),
            })

        summary = {
            "sorted": bool(copied),
            "folder": str(dest),
            "name": dest.name,
            "kind": kind,
            "files": len(copied),
            "bytes": total,
            "failed": failed,
        }
        self._write_manifest(dest, token, kind, seq, event_at, stamp_at,
                             copied, failed, extra)

        if failed:
            log.error("sort: %s -> %s, %d file(s) filed, %d missing: %s",
                      kind, dest.name, len(copied), len(failed), failed)
        else:
            log.info("sort: %s -> %s, %d files, %.0f MB",
                     kind, dest.name, len(copied), total / 1e6)
        return summary

    def _space_ok(self, wanted: int) -> tuple[bool, int]:
        floor = float(self.config.recording.sort_min_free_gb) * 1e9
        try:
            free = shutil.disk_usage(self.sort_root.anchor or self.clip_dir).free
        except OSError:
            return True, -1  # Cannot tell; do not block on a failed measurement.
        return (free - wanted) >= floor, free

    def _reserve(self, root: Path, name: str, token: str) -> Path:
        """The event folder, created, and never shared with a different event.

        Two events in the same second are close to impossible under the ~70 s
        operating rule, but "close to" is not a guarantee and silently merging two
        takes into one folder is exactly the failure this whole feature exists to
        prevent. A collision gets a suffix instead.
        """
        root.mkdir(parents=True, exist_ok=True)
        candidate = root / name
        for attempt in range(1, 50):
            if not candidate.exists():
                candidate.mkdir()
                return candidate
            manifest = candidate / MANIFEST_NAME
            try:
                existing = json.loads(manifest.read_text(encoding="utf-8"))
                if existing.get("token") == token:
                    return candidate  # Same event, re-run or resumed. Reuse it.
            except (OSError, ValueError):
                pass
            candidate = root / f"{name}-{attempt + 1}"
        return candidate  # 49 collisions in one second is not a real world.

    def _copy_one(self, src: Path, dest: Path) -> int:
        """Copy via ``.part`` + atomic rename. Idempotent on a matching file."""
        size = src.stat().st_size  # Raises FileNotFoundError, which is the point.
        if dest.exists() and dest.stat().st_size == size:
            return size
        part = dest.with_name(dest.name + PART_SUFFIX)
        try:
            shutil.copy2(src, part)
            os.replace(part, dest)
        except BaseException:
            # Cleanup must not mask the original failure.
            with contextlib.suppress(OSError):
                part.unlink()
            raise
        return size

    def _write_manifest(self, dest: Path, token: str, kind: str, seq: int | None,
                        event_at: float | None, stamp_at: float,
                        copied: list[dict], failed: dict[str, str],
                        extra: dict[str, Any]) -> None:
        """The line between a folder of files and a search result.

        The clips alone say which nine angles exist. The manifest says what the
        event *was* - which press, how much lead-in it caught, whether every file
        decoded - which is the part that is gone forever once the Controller
        restarts.
        """
        payload = {
            "token": token,
            "kind": kind,
            "seq": seq,
            "folder": dest.name,
            "startedAt": stamp_at,
            "startedAtLocal": datetime.fromtimestamp(stamp_at).isoformat(timespec="seconds"),
            "eventAt": event_at,
            "eventAtLocal": (datetime.fromtimestamp(event_at).isoformat(timespec="seconds")
                             if event_at else None),
            "filedAt": datetime.now().isoformat(timespec="seconds"),
            "cameras": sorted({c["camera"] for c in copied}),
            "clips": sorted(copied, key=lambda c: c["camera"]),
            "missing": failed,
            "sourceDir": str(self.clip_dir),
            **extra,
        }
        try:
            (dest / MANIFEST_NAME).write_text(
                json.dumps(payload, indent=2), encoding="utf-8")
        except OSError as exc:
            log.warning("sort: could not write %s: %s", MANIFEST_NAME, exc)
