"""Encoder bridges: the ffmpeg processes that turn a local device into RTSP.

A USB webcam, a capture card or a desktop grab is not something a network video
recorder can talk to. An ffmpeg process makes it one: it opens the device and
publishes H.264 over RTSP, which the recorder then treats as an ordinary IP
camera. See docs/usb-to-h264.md.

Those processes cost real power - measured on the reference rig, four of them
were 6.8 W of GPU and 34 percentage points of CPU, running 24/7 whether or not
anyone was filming. So they can be switched off, per camera, from the UI.

🔑 THE CONTROLLER DOES NOT START OR STOP THEM. It writes what is WANTED to a
state file; a supervisor process reads that file and makes reality match. Kill a
bridge directly and the supervisor restarts it within its poll interval and logs
a fault while doing so - which is exactly the job you gave it. Declare intent;
do not fight your own supervisor. A reference supervisor for Windows is in
contrib/windows/ (see its README).

⚠️ Switching a bridge off takes that camera OFFLINE in the recorder. It cannot
record and a That-Was-Awesome press will not save it. The UI says so by name.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .config import STATE_DIR
from .paths import bridge_state_path

log = logging.getLogger(__name__)

# How long after a toggle to describe a bridge as "pending" rather than
# contradicting itself on screen. The supervisor wakes within ~2 s of the file
# changing, ffmpeg takes a moment to publish, and Blue Iris takes a few seconds
# more to reconnect and admit the camera is back - so between the tap and the
# truth there is a window in which "on" and "offline" are both accurate.
SETTLE_SECONDS = 25.0


@dataclass
class BridgeView:
    """One bridge as the page should see it."""

    id: str            # 'cam1' - the RTSP path and the run_<id>.cmd name
    camera: str        # 'CAM1' - what Blue Iris calls it
    enabled: bool      # what we have ASKED for
    online: bool       # what Blue Iris actually reports
    pending: bool      # asked recently, not yet agreed

    def as_dict(self) -> dict:
        return {"id": self.id, "camera": self.camera, "enabled": self.enabled,
                "online": self.online, "pending": self.pending}


class BridgeSwitch:
    """Reads and writes the supervisor's desired-state file."""

    def __init__(self, config) -> None:
        cfg = config.bridges
        # Available when bridges are configured at all - an empty state_path is
        # now a DEFAULT rather than "switched off", so availability keys off
        # whether any bridges were declared.
        self.available = bool(cfg.enabled) and bool(cfg.bridges)
        self.path = bridge_state_path(cfg.state_path, STATE_DIR) if self.available else None
        self.default_on = bool(cfg.default_on)
        self._lock = threading.Lock()
        # id -> camera name. Written as "cam1:CAM1" so it survives both TOML and
        # a SHOPCAM_BRIDGES_BRIDGES environment override, which can only carry a
        # comma-separated string.
        self.map: dict[str, str] = {}
        for entry in cfg.bridges:
            bridge_id, _, camera = str(entry).partition(":")
            bridge_id = bridge_id.strip()
            if bridge_id:
                self.map[bridge_id] = (camera.strip() or bridge_id.upper())
        self.by_camera = {camera: bridge_id for bridge_id, camera in self.map.items()}
        # bridge id -> monotonic deadline for "we asked, give it a moment"
        self._pending: dict[str, float] = {}
        self._cache: tuple[tuple, dict[str, bool]] | None = None

    # ---------------------------------------------------------------- reading

    def _stat_key(self) -> tuple:
        try:
            st = self.path.stat()
        except OSError:
            return (0, 0.0)
        return (st.st_size, st.st_mtime)

    def desired(self) -> dict[str, bool]:
        """What each bridge is currently asked to be. Never raises."""
        if not self.available:
            return {}
        key = self._stat_key()
        if self._cache and self._cache[0] == key:
            return dict(self._cache[1])

        wanted = {bridge_id: self.default_on for bridge_id in self.map}
        try:
            raw = self.path.read_text(encoding="utf-8-sig")
            saved = json.loads(raw).get("bridges") or {}
        except FileNotFoundError:
            saved = {}
        except (OSError, ValueError) as exc:
            # A read can lose the race with our own atomic rename, and an
            # operator can hand-edit the file into something invalid. Neither is
            # a reason to claim four cameras are off - say so and answer with
            # the last good reading if there is one.
            log.warning("could not read %s (%s)", self.path.name, exc)
            if self._cache:
                return dict(self._cache[1])
            saved = {}
        for bridge_id, value in saved.items():
            if bridge_id in wanted:
                wanted[bridge_id] = bool(value)
        self._cache = (key, dict(wanted))
        return wanted

    # ---------------------------------------------------------------- writing

    def _write(self, wanted: dict[str, bool], by: str = "controller") -> None:
        payload = {
            "version": 1,
            "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            # WHO asked, not just that someone did. Since the idle clock was
            # added there is more than one answer, and "why is CAM5 off?" has to
            # be answerable from the file itself - an encoder that is off for an
            # unexplained reason is indistinguishable from a broken one.
            # "controller" = a human at the UI. "idle-timer" = the clock.
            "by": by,
            # Written for whoever opens this file at the machine, which is the
            # only place it can be opened - the note is the interface.
            "note": ("Desired state for the ffmpeg bridges. supervisor.ps1 reads "
                     "this every cycle and starts or stops them to match. "
                     "Missing key = off."),
            "bridges": {bridge_id: bool(wanted.get(bridge_id, self.default_on))
                        for bridge_id in self.map},
        }
        tmp = self.path.with_suffix(".tmp")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic: the supervisor may read at any moment, and half a JSON file is
        # an unreadable file. os.replace is atomic on Windows for same-volume
        # renames. utf-8 with no BOM - PowerShell's ConvertFrom-Json copes with
        # a BOM but nothing is gained by making it.
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)
        self._cache = None

    def set(self, bridge_id: str, on: bool, by: str = "controller") -> dict[str, bool]:
        if bridge_id not in self.map:
            raise KeyError(bridge_id)
        with self._lock:
            wanted = self.desired()
            wanted[bridge_id] = bool(on)
            self._write(wanted, by)
            self._pending[bridge_id] = time.monotonic() + SETTLE_SECONDS
        log.info("bridge %s -> %s (%s)", bridge_id, "ON" if on else "OFF", by)
        return wanted

    def set_many(self, bridge_ids: list[str], on: bool,
                 by: str = "controller") -> list[str]:
        """Set several at once, in ONE write. Returns the ones that changed.

        One write, not one per bridge: the supervisor wakes on the file's
        timestamp, so four separate writes are four wake-ups, and the first
        three see a state that was already stale when it read them.
        """
        wanted_ids = [b for b in bridge_ids if b in self.map]
        if not wanted_ids:
            return []
        with self._lock:
            wanted = self.desired()
            changed = [b for b in wanted_ids if bool(wanted.get(b)) != bool(on)]
            if not changed:
                return []
            for bridge_id in changed:
                wanted[bridge_id] = bool(on)
            self._write(wanted, by)
            deadline = time.monotonic() + SETTLE_SECONDS
            for bridge_id in changed:
                self._pending[bridge_id] = deadline
        log.info("bridges %s -> %s (%s)", ", ".join(changed),
                 "ON" if on else "OFF", by)
        return changed

    def set_all(self, on: bool, by: str = "controller") -> dict[str, bool]:
        with self._lock:
            wanted = {bridge_id: bool(on) for bridge_id in self.map}
            self._write(wanted, by)
            deadline = time.monotonic() + SETTLE_SECONDS
            self._pending = {bridge_id: deadline for bridge_id in self.map}
        log.info("all bridges -> %s (%s)", "ON" if on else "OFF", by)
        return wanted

    # ------------------------------------------------------------------ views

    def view(self, cameras: list[dict]) -> list[dict]:
        """Merge intent with what Blue Iris reports, for the page."""
        if not self.available:
            return []
        wanted = self.desired()
        online = {cam["name"]: bool(cam.get("available")) for cam in cameras}
        now = time.monotonic()
        out = []
        for bridge_id, camera in self.map.items():
            enabled = wanted.get(bridge_id, self.default_on)
            is_online = online.get(camera, False)
            # Pending is a claim about disagreement, not a timer: once Blue Iris
            # agrees with the switch there is nothing to wait for, so it clears
            # early rather than leaving a spinner up for the full settle window.
            pending = (self._pending.get(bridge_id, 0.0) > now
                       and enabled != is_online)
            out.append(BridgeView(bridge_id, camera, enabled, is_online, pending).as_dict())
        return out

    def is_off(self, camera: str) -> bool:
        """Is this Blue Iris camera dark because its bridge was switched off?"""
        if not self.available:
            return False
        bridge_id = self.by_camera.get(camera)
        if bridge_id is None:
            return False
        return not self.desired().get(bridge_id, self.default_on)
