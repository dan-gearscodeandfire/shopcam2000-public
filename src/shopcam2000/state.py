"""Controller state: what is armed, what is settings, what take is running.

The division of responsibility is the single most important idea in this
project, so it is worth stating plainly:

* **Arming is ours.** Blue Iris has no concept of a camera being "armed". It is
  the Controller's invention, it lives on the server (not in a browser), it is
  shared by every connected client, and it is persisted so a restart does not
  silently disarm the rig.
* **Recording is Blue Iris's.** We never assume a camera is recording because we
  asked it to. We report ``isManRec`` as Blue Iris gives it to us. If the
  physical button, the Blue Iris console, or anything else starts a recording,
  the Controller shows the truth rather than its own optimistic guess.

Getting this backwards is how the previous ESP32 controller ended up displaying
a recording state that had not been true for hours.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


class ArmingLocked(RuntimeError):
    """Arming cannot change while a take is in progress."""


@dataclass
class Take:
    """One recording session, from RECORD to STOP."""

    active: bool = False
    started_at: float = 0.0           # wall clock, UTC seconds
    cameras: list[str] = field(default_factory=list)   # what we asked to record
    started: list[str] = field(default_factory=list)   # what actually started
    failures: dict[str, str] = field(default_factory=dict)
    audio_matters: bool = True
    adopted: bool = False             # started by something other than us
    clips: list[dict] = field(default_factory=list)


@dataclass
class Settings:
    """User-adjustable presentation, shared across every device.

    Deliberately server-side: when the camera is rolling you want to flip the
    theme once and have every visible screen agree, not walk round the shop
    setting each one.
    """

    theme: str = "cogitator"
    hide_unavailable: bool = False
    audio_matters: bool = True
    thumbnail_interval: int = 600
    stop_hold_ms: int = 1000
    ui_scale: int = 100          # percent, 75-150; see config.UiConfig
    keep_awake: bool = True


class ControllerState:
    """Everything the Controller knows that Blue Iris does not.

    Guarded by a lock because the poller and the HTTP handlers both touch it.
    """

    def __init__(self, state_dir: Path, defaults: Settings,
                 default_armed: list[str] | None = None) -> None:
        self._path = state_dir / "state.json"
        self._lock = threading.RLock()
        self.settings = defaults
        self.armed: set[str] = set(default_armed or [])
        self.overwatch: set[str] = set()
        self.take = Take(audio_matters=defaults.audio_matters)
        self._load()

    # ------------------------------------------------------------- persistence

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            saved = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("could not read %s (%s); starting from defaults",
                        self._path.name, exc)
            return
        self.armed = set(saved.get("armed", self.armed))
        self.overwatch = set(saved.get("overwatch", self.overwatch))
        for key, value in (saved.get("settings") or {}).items():
            if hasattr(self.settings, key):
                setattr(self.settings, key, value)
        log.info("restored %d armed, %d overwatch camera(s) from %s",
                 len(self.armed), len(self.overwatch), self._path.name)

    def _save(self) -> None:
        payload = {"armed": sorted(self.armed),
                   "overwatch": sorted(self.overwatch),
                   "settings": asdict(self.settings)}
        tmp = self._path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(self._path)
        except OSError as exc:
            log.warning("could not persist state: %s", exc)

    # -------------------------------------------------------------- arming

    def set_armed(self, camera: str, armed: bool) -> set[str]:
        """Arm or disarm one camera.

        Refused while a take is running: the camera set is fixed when RECORD is
        pressed, so every clip in a take shares the same bounds.
        """
        with self._lock:
            if self.take.active:
                raise ArmingLocked(
                    "cannot change arming while recording - stop the take first"
                )
            if armed:
                self.armed.add(camera)
            else:
                self.armed.discard(camera)
            self._save()
            return set(self.armed)

    def set_armed_bulk(self, cameras: list[str]) -> set[str]:
        with self._lock:
            if self.take.active:
                raise ArmingLocked(
                    "cannot change arming while recording - stop the take first"
                )
            self.armed = set(cameras)
            self._save()
            return set(self.armed)

    # ------------------------------------------------------------ overwatch

    def set_overwatch(self, camera: str, overwatch: bool) -> set[str]:
        """Mark a camera as part of the "overwatch" set.

        Overwatch cameras keep a rolling pre-trigger buffer in Blue Iris (the
        last minute, held in RAM) and are the ones the "That Was Awesome"
        button flushes to disk. Selection is ours; the buffering itself is
        configured in Blue Iris per camera.

        Deliberately NOT locked during a take: changing which cameras the
        *next* button press will save does not alter the recording in
        progress, so there is nothing to protect.
        """
        with self._lock:
            if overwatch:
                self.overwatch.add(camera)
            else:
                self.overwatch.discard(camera)
            self._save()
            return set(self.overwatch)

    def adopt(self, cameras: list[str]) -> None:
        """Arm cameras that Blue Iris reports as already recording.

        This is the one path that may change arming during a take, and it is not
        a contradiction of the lock: the lock exists to stop *the operator*
        changing the camera set mid-take. Reconciliation is not the operator -
        it is the Controller refusing to pretend a running recording isn't
        happening. Something already made this true; we are catching up to it.
        """
        with self._lock:
            new = [cam for cam in cameras if cam not in self.armed]
            if not new:
                return
            self.armed.update(new)
            self._save()
            log.info("adopted in-progress recording on %s", ", ".join(new))

    # -------------------------------------------------------------- settings

    def update_settings(self, changes: dict) -> Settings:
        with self._lock:
            for key, value in changes.items():
                if not hasattr(self.settings, key):
                    continue
                current = getattr(self.settings, key)
                if isinstance(current, bool):
                    value = bool(value)
                elif isinstance(current, int) and not isinstance(value, bool):
                    value = int(value)
                setattr(self.settings, key, value)
            if self.settings.theme not in {"cogitator", "clean"}:
                self.settings.theme = "cogitator"
            # Clamped rather than rejected: this multiplies the root font size,
            # so a bad value does not raise an error, it makes the interface
            # unreadable - including the control you would use to put it back.
            self.settings.ui_scale = max(75, min(150, int(self.settings.ui_scale)))
            self._save()
            return self.settings

    # ---------------------------------------------------------------- takes

    def begin(self, cameras: list[str], started: list[str],
              failures: dict[str, str], adopted: bool = False,
              started_at: float | None = None) -> Take:
        with self._lock:
            self.take = Take(
                active=True,
                started_at=started_at if started_at is not None else time.time(),
                cameras=list(cameras),
                started=list(started),
                failures=dict(failures),
                audio_matters=self.settings.audio_matters,
                adopted=adopted,
            )
            return self.take

    def end(self, clips: list[dict] | None = None) -> Take:
        with self._lock:
            self.take.active = False
            if clips is not None:
                self.take.clips = clips
            return self.take
