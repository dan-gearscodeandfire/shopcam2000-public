"""The service layer: one poller, one truth, many browsers.

A single background task polls Blue Iris and pushes the result to every
connected client. Ten open tabs cost exactly what one costs - the alternative
(each browser polling for itself) would multiply load on the machine that is
also encoding your footage, which is the one machine you least want to tax.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import pathlib
import time
from dataclasses import asdict
from typing import Any

from .blueiris import BlueIrisClient, BlueIrisError, BlueIrisOffline
from .bridges import BridgeSwitch
from .config import Config
from .hooks_runner import HookRunner
from .sorting import ClipSorter
from .paths import find_ffprobe, install_hint
from .state import ArmingLocked, ControllerState

log = logging.getLogger(__name__)

# Blue Iris writes clip database records a moment after recording stops, so a
# cliplist issued immediately after STOP can come back empty. Retry briefly.
CLIP_SETTLE_TRIES = 14
CLIP_SETTLE_DELAY = 0.6

# Blue Iris does not flip isManRec the instant it is told to. Starting or
# stopping several cameras therefore leaves a window in which camlist disagrees
# with what we just did - some cameras already changed, some have not.
#
# Reconciliation must not run inside that window. Without this, stopping a take
# would see the last camera still recording, "adopt" it as a new take, and then
# announce that the take "ended outside the Controller" a second later. Both
# messages are false, and the user would see an Adopted banner flash at the end
# of every take.
SETTLE_SECONDS = 3.0

# How long after a recording stops before the pre-trigger buffer actually starts
# accumulating again. Measured 2026-07-28 across 9 cameras and 6 re-press gaps
# (tools/twab_recovery_curve.py): the pre-roll a press captures is
#
#     preroll = clamp(gap - break_time - BUFFER_RESTART_SECONDS, 0, max_preroll)
#
# and the fit wanted ~11 s of dead time against a 10 s break time. The extra
# second is Blue Iris reopening the buffer, and it is consistent across every
# camera regardless of whether it is a native IP camera, a USB camera behind the
# bridge, or the desktop capture.
#
# 🔴 Do NOT "fix" this by enlarging the buffer. Measured the same night with CAM2
# at movieroll=1800 (180 s) against CAM3 at 600 (60 s) on the SAME presses: the
# two behaved identically (14.8 s vs 14.8 s at a 25 s gap). A trigger drains the
# pre-trigger buffer no matter how big it is. See
# Zettelkasten/a-trigger-drains-the-pre-roll-buffer-whatever-its-size.md.
BUFFER_RESTART_SECONDS = 1.0


def _parse_uptime(value: Any) -> float | None:
    """Blue Iris's ``uptime`` (``'d:hh:mm:ss'``) in seconds, or None if unreadable.

    Load-bearing for the TWAB lead-in estimate: a Blue Iris restart empties every
    camera's pre-trigger buffer, and the buffer cannot possibly hold more history
    than Blue Iris has been running. Measured 2026-07-28 - a press 2 s after a
    restart was reported as "lead-in 60.0s" and actually wrote 8.3-12.4 s clips,
    i.e. about one second of lead-in. Uptime is the only signal that catches this,
    including a restart fast enough that the poller never saw Blue Iris offline.
    """
    if not isinstance(value, str):
        return None
    parts = [p.strip() for p in value.strip().split(":")]
    if not parts or not all(p.isdigit() for p in parts):
        return None
    nums = [int(p) for p in parts][-4:]
    while len(nums) < 4:
        nums.insert(0, 0)
    days, hours, minutes, seconds = nums
    return days * 86400.0 + hours * 3600.0 + minutes * 60.0 + seconds


def _why_unavailable(camera: dict) -> str:
    """Why this camera cannot record, in words worth putting on a screen.

    These strings are the whole of what a TWAB failure tells the operator, and
    "your moment was not saved" is only actionable if it also says whether to
    walk over and power-cycle the camera or just tick it back on in Blue Iris.
    Falling back to a single "offline / no signal" for every cause reported a
    *disabled* camera as offline - measured 2026-07-28 on CAM3.

    ⚠️ Blue Iris's own flags disagree with each other for a few seconds after a
    config change, so which branch fires is partly a matter of when you looked.
    Measured 2026-07-28, disabling CAM3:

        +1s, +3s   isEnabled=true   isOnline=false  error=""
        +6s        isEnabled=false  isOnline=false  error="Disabled"

    ``isEnabled`` lags ``isOnline`` by about five seconds. Both readings are
    honest about the moment they were taken and both refuse the trigger, which
    is the part that matters - do not "fix" this by trusting one flag.
    """
    # First, because it outranks every reading below it: a camera whose bridge
    # was switched off is offline, disabled and signal-less all at once, and
    # every one of those words would send someone out to the shop to look at a
    # camera that is fine. Somebody turned it off. Say that.
    if camera.get("bridgeOff"):
        return "its bridge is switched off in the Controller"
    if not camera.get("enabled"):
        return "disabled in Blue Iris"
    if not camera.get("online"):
        reason = "offline"
    elif camera.get("noSignal"):
        reason = "no signal"
    else:
        reason = "unavailable"
    detail = (camera.get("error") or "").strip()
    return f"{reason} ({detail})" if detail else reason


class Controller:
    def __init__(self, config: Config, state: ControllerState,
                 client: BlueIrisClient, hooks: HookRunner) -> None:
        self.config = config
        self.state = state
        self.bi = client
        self.hooks = hooks
        # Files each event's clips into a folder of their own. Pure convenience
        # over footage that is already safely on disk, so it is allowed to fail.
        self.sorter = ClipSorter(config)
        # Which ffmpeg encoders are wanted. The Controller only ever declares
        # this; supervisor.ps1 starts and stops them. See bridges.py.
        self.bridges = BridgeSwitch(config)

        self._subscribers: set[asyncio.Queue] = set()
        self._snapshot: dict[str, Any] = self._empty_snapshot()
        self._thumbs: dict[str, tuple[float, bytes]] = {}
        self._poll_task: asyncio.Task | None = None
        self._thumb_task: asyncio.Task | None = None
        self._record_lock = asyncio.Lock()
        self._online = False
        self._last_error: str | None = None
        self._settle_until = 0.0
        self._collecting = False
        # The last TWAB press and what came of it, whatever pressed it. The UI
        # used to paint its own result and knew nothing about a press from the
        # physical button, so the same event looked completely different
        # depending on which button you used. Both now render from this.
        self._twab_result: dict[str, Any] | None = None
        self._twab_seq = 0
        # Wall-clock at which the pre-trigger buffer last began refilling - i.e.
        # when the last recording STOPPED, not when it started. 0.0 means "no
        # recording since this process came up", which is treated as a full
        # buffer: the rig has almost always been idle for minutes by then, and
        # guessing short would cry wolf on the first press of every session.
        self._buffer_refilling_since = 0.0
        # Blue Iris's own uptime, in seconds, from the last poll. A restart wipes
        # every pre-trigger buffer, so this is a hard ceiling on the lead-in any
        # press can catch, independent of when we last pressed.
        self._bi_uptime: float | None = None
        # Whether Blue Iris has ever been seen online by THIS Controller process.
        # Distinguishes "Blue Iris restarted" from "the Controller just started
        # up"; both look identical from the poller's online flag alone.
        self._seen_online = False
        # Live post-press verification tasks. Held so they are not garbage
        # collected mid-flight, and so shutdown can cancel them.
        self._verify_tasks: set[asyncio.Task] = set()
        # Serialises presses so each one sees the buffer state the previous press
        # left behind, rather than all of them reading it before any writes it.
        self._twab_lock = asyncio.Lock()

    # ------------------------------------------------------------- lifecycle

    async def start(self) -> None:
        self._poll_task = asyncio.create_task(self._poll_loop(), name="bi-poller")
        self._thumb_task = asyncio.create_task(self._thumb_loop(), name="bi-thumbs")

    async def stop(self) -> None:
        for task in (self._poll_task, self._thumb_task, *self._verify_tasks):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        await self.bi.close()

    # ------------------------------------------------------------ subscribers

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=4)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def _broadcast(self) -> None:
        dead = []
        for queue in self._subscribers:
            try:
                queue.put_nowait(self._snapshot)
            except asyncio.QueueFull:
                # A client that cannot keep up gets the next update instead.
                # Dropping a frame of state is harmless; blocking the poller
                # would not be.
                pass
            except Exception:
                dead.append(queue)
        for queue in dead:
            self._subscribers.discard(queue)

    @property
    def snapshot(self) -> dict[str, Any]:
        return self._snapshot

    # ------------------------------------------------------------------ poll

    def _empty_snapshot(self) -> dict[str, Any]:
        return {
            "bi": {"online": False, "version": None, "error": None,
                   "host": None, "cpu": None, "mem": None, "uptime": None},
            "disk": None,
            "cameras": [],
            "take": {"active": False, "elapsedMs": 0, "startedAt": 0,
                     "cameras": [], "started": [], "failures": {},
                     "adopted": False, "clips": [], "collecting": False},
            "armed": [],
            "overwatch": [],
            "bridges": [],
            "twab": None,
            "settings": {},
            "hooks": {"loaded": False, "functions": []},
            "ts": 0.0,
        }

    async def _poll_loop(self) -> None:
        while True:
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("unexpected error in poll loop")
            await asyncio.sleep(self._interval())

    def _interval(self) -> float:
        if not self._online:
            return self.config.polling.offline_backoff
        if self.state.take.active:
            return self.config.polling.while_recording
        return self.config.polling.idle

    async def _poll_once(self) -> None:
        try:
            cams = await self.bi.camlist()
            status = await self.bi.status()
        except BlueIrisOffline as exc:
            if self._online:
                log.warning("Blue Iris went offline: %s", exc)
            self._online = False
            self._last_error = str(exc)
            self._snapshot = self._build(None, None)
            self._broadcast()
            return
        except BlueIrisError as exc:
            self._online = False
            self._last_error = str(exc)
            self._snapshot = self._build(None, None)
            self._broadcast()
            return

        if not self._online:
            log.info("Blue Iris %s online at %s", self.bi.version or "?", self.bi.base)
            # Blue Iris came back from an outage we watched, so every pre-trigger
            # buffer restarted from empty.
            #
            # ⚠️ Only when we had seen it online BEFORE. The Controller's own first
            # poll is also a False->True transition, and treating that as a Blue
            # Iris restart made every Controller restart under-report the lead-in
            # for the next minute - measured 2026-07-28, it reported 33.7 s on a
            # rig whose buffers were full. Restarting the Controller does not touch
            # Blue Iris's buffers.
            if self._seen_online:
                self._buffer_refilling_since = time.time()
        self._seen_online = True
        self._online = True
        self._last_error = None

        uptime = _parse_uptime((status or {}).get("uptime"))
        if (uptime is not None and self._bi_uptime is not None
                and uptime < self._bi_uptime):
            # Uptime went backwards: Blue Iris restarted between two polls without
            # us ever observing it offline. Same consequence, and this is the only
            # way to notice it.
            log.warning("Blue Iris restarted between polls (uptime %.0fs < %.0fs); "
                        "pre-roll buffers are empty", uptime, self._bi_uptime)
            self._buffer_refilling_since = time.time()
        self._bi_uptime = uptime

        self._reconcile(cams)
        self._snapshot = self._build(cams, status)
        self._broadcast()

    def _reconcile(self, cams: list[dict]) -> None:
        """Make the Controller's idea of a take agree with Blue Iris's.

        Two directions:

        * Blue Iris is recording and we think we are not -> adopt it. Something
          else (the physical button, the console) started this. Show it, arm it,
          and make STOP able to end it. Anything else means the UI is lying.
        * We think a take is running and Blue Iris has nothing recording -> the
          take ended without us, most likely a Blue Iris restart. Close it out.

        Neither runs during the settle window after our own start or stop - see
        SETTLE_SECONDS. In that window disagreement is expected, not news.
        """
        if time.monotonic() < self._settle_until:
            return

        recording_now = [
            c["optionValue"] for c in cams if c.get("isManRec")
        ]
        take = self.state.take

        if recording_now and not take.active:
            self.state.adopt(recording_now)
            elapsed_ms = max(
                (int(c.get("ManRecElapsed") or 0) for c in cams if c.get("isManRec")),
                default=0,
            )
            self.state.begin(
                cameras=recording_now,
                started=recording_now,
                failures={},
                adopted=True,
                started_at=time.time() - (elapsed_ms / 1000.0),
            )
            log.info("adopted an in-progress recording on %s", ", ".join(recording_now))

        elif take.active and not recording_now:
            log.warning("take ended outside the Controller (Blue Iris restart?)")
            self.state.end(clips=[])

    def _build(self, cams: list[dict] | None, status: dict | None) -> dict[str, Any]:
        settings = self.state.settings
        take = self.state.take
        hidden = set(self.config.recording.always_hide)

        # Read once per build rather than once per camera: it is a stat() and a
        # cached parse, but the loop below runs nine times a poll.
        bridges_off = {camera for camera in self.bridges.by_camera
                       if self.bridges.is_off(camera)}
        audio_only = set(self.config.recording.audio_only or ())

        cameras = []
        for cam in cams or []:
            name = cam["optionValue"]
            if name in hidden:
                continue
            manrec = bool(cam.get("isManRec"))
            cameras.append({
                "name": name,
                "display": cam.get("optionDisplay") or name,
                "armed": name in self.state.armed,
                "overwatch": name in self.state.overwatch,
                "enabled": bool(cam.get("isEnabled")),
                "online": bool(cam.get("isOnline")),
                "noSignal": bool(cam.get("isNoSignal")),
                "paused": bool(cam.get("isPaused")),
                "recording": manrec,
                # ManRecElapsed keeps its final value after a recording stops,
                # so it is only meaningful while isManRec is true. Reporting it
                # unconditionally would show a frozen timer on an idle camera.
                "elapsedMs": int(cam.get("ManRecElapsed") or 0) if manrec else 0,
                "limitMs": int(cam.get("ManRecLimit") or 0),
                "fps": round(float(cam.get("FPS") or 0), 1),
                "bps": int(cam.get("BPS") or 0),
                "audio": bool(cam.get("audio")),
                "error": (cam.get("error") or "").strip(),
                "failure": take.failures.get(name),
                "available": bool(cam.get("isEnabled")) and bool(cam.get("isOnline"))
                             and not bool(cam.get("isNoSignal")),
                "hasThumb": name in self._thumbs,
                # Not a fault: this camera is dark because someone switched its
                # encoder off. The page renders it differently and the TWAB
                # receipt says so in words.
                "bridge": self.bridges.by_camera.get(name),
                "bridgeOff": name in bridges_off,
                # A microphone wearing a camera's clothes. See
                # RecordingConfig.audio_only - the page needs this to stop
                # drawing MIC1 as a tenth angle.
                "audioOnly": name in audio_only,
            })

        elapsed_ms = max((c["elapsedMs"] for c in cameras if c["recording"]), default=0)

        disk = None
        if status:
            disks = status.get("disks") or []
            if disks:
                d = disks[0]
                disk = {
                    "disk": d.get("disk"),
                    "usedGb": round((d.get("used") or 0) / 1024, 1),
                    "allocatedGb": round((d.get("allocated") or 0) / 1024, 1),
                    "freeGb": round((d.get("free") or 0) / 1024, 1),
                    "totalGb": round((d.get("total") or 0) / 1024, 1),
                    "error": d.get("error"),
                }
                if disk["allocatedGb"]:
                    disk["pct"] = round(100 * disk["usedGb"] / disk["allocatedGb"])
                else:
                    disk["pct"] = 0

        return {
            "bi": {
                "online": self._online,
                "version": self.bi.version,
                "host": f"{self.bi.host}:{self.bi.port}",
                "error": self._last_error,
                "cpu": (status or {}).get("cpu"),
                "mem": (status or {}).get("mem"),
                "uptime": (status or {}).get("uptime"),
                "clips": (status or {}).get("clips"),
            },
            "disk": disk,
            "cameras": cameras,
            "take": {
                "active": take.active,
                "startedAt": take.started_at,
                "elapsedMs": elapsed_ms,
                "cameras": take.cameras,
                "started": take.started,
                "failures": take.failures,
                "adopted": take.adopted,
                "audioMatters": take.audio_matters,
                "clips": take.clips,
                "recordingCount": sum(1 for c in cameras if c["recording"]),
                "collecting": self._collecting,
            },
            "armed": sorted(self.state.armed),
            "overwatch": sorted(self.state.overwatch),
            "bridges": self.bridges.view(cameras),
            "twab": self._twab_result,
            "settings": asdict(settings),
            "hooks": {"loaded": self.hooks.loaded, "functions": self.hooks.available},
            "ts": time.time(),
        }

    # --------------------------------------------------------------- actions

    def _wake_bridges_for(self, cameras: list[str]) -> list[str]:
        """Arming or watching a camera turns its encoder on. Never off.

        Arming a camera is a statement that you want its footage. An encoder
        that is off makes that impossible, and the operator has no reason to
        know which four of the nine cameras go through one - so the switch that
        expresses the intent brings up what the intent needs.

        🔑 The asymmetry is deliberate and is the whole rule: **on is implied,
        off is only ever explicit.** Disarming a camera must not stop its
        encoder. Half the reason to leave one running is to keep watching it,
        and an encoder that shut itself down because you cleared the arm set
        would be a camera that went dark without anyone deciding it should.
        Only the Enc switch (or All off) turns one off.

        It is also not instant: measured 2026-07-30, ~20 s before Blue Iris can
        record and ~80 s before there is a full minute of pre-roll behind it.
        The card says "Encoder starting" for that window rather than pretending.
        """
        if not self.bridges.available:
            return []
        wanted = [self.bridges.by_camera[c] for c in cameras
                  if c in self.bridges.by_camera]
        started = self.bridges.set_many(wanted, True)
        if started:
            log.info("encoder(s) %s were off and are being started - asked for "
                     "by arming or watching %s",
                     ", ".join(started), ", ".join(sorted(set(cameras))))
        return started

    async def set_armed(self, camera: str, armed: bool) -> None:
        self.state.set_armed(camera, armed)
        if armed:
            self._wake_bridges_for([camera])
        await self._refresh()

    async def set_armed_bulk(self, cameras: list[str]) -> None:
        # The new set only. A camera dropped from it keeps its encoder.
        self.state.set_armed_bulk(cameras)
        if cameras:
            self._wake_bridges_for(cameras)
        await self._refresh()

    async def set_overwatch(self, camera: str, overwatch: bool) -> None:
        self.state.set_overwatch(camera, overwatch)
        if overwatch:
            self._wake_bridges_for([camera])
        await self._refresh()

    async def set_bridge(self, bridge_id: str | None, on: bool) -> dict[str, bool]:
        """Ask for a bridge (or all of them) to be running, or not.

        Deliberately not locked against a take, unlike arming. Switching a
        bridge off mid-recording would be a spectacular way to lose footage, so
        the temptation is to forbid it - but the person doing it is standing in
        the shop looking at the rig, and a Controller that refuses is a
        Controller that gets worked around at the console where nothing is
        logged. The UI warns; it does not veto.

        Returns the new desired state. It is intent, not fact: nothing is
        running or stopped until the supervisor's next cycle, within a couple
        of seconds.
        """
        if not self.bridges.available:
            raise RuntimeError("bridge switching is not configured")
        if bridge_id is None:
            wanted = self.bridges.set_all(on)
        else:
            wanted = self.bridges.set(bridge_id, on)
        await self._refresh()
        return wanted

    def _expected_preroll(self, now: float) -> float:
        """Seconds of lead-in a press right now would actually capture.

        The pre-trigger buffer is a rolling window of the recent past, and a
        trigger empties it. So two presses close together cannot both have a full
        minute of lead-in - the second one only has whatever accumulated since the
        first one's recording stopped. Measured across 9 cameras and 6 gaps; the
        law is linear with no cliff, and identical on every camera.

        This is deliberately a *prediction*, not a reading: Blue Iris exposes no
        buffer-depth field, and a press is on the critical path - the button
        firmware abandons the request after a few seconds, so there is no time to
        go and look. The prediction is only ever used to qualify the receipt,
        never to refuse a press.
        """
        full = float(self.config.recording.max_preroll_seconds)
        if not self._buffer_refilling_since:
            lead = full
        else:
            elapsed = now - self._buffer_refilling_since - BUFFER_RESTART_SECONDS
            lead = max(0.0, min(full, elapsed))

        # A buffer cannot hold more history than Blue Iris has been running, no
        # matter how long ago we last pressed. This is the ceiling that catches a
        # restart, including one the poller never saw as an outage.
        if self._bi_uptime is not None:
            lead = min(lead, max(0.0, self._bi_uptime - BUFFER_RESTART_SECONDS))

        # 🔑 The poller's view is at best one cycle old, and a press does not wait
        # for it. If we believe Blue Iris is DOWN and the press nonetheless works,
        # then Blue Iris came back within the last poll interval and every buffer
        # is essentially empty - the stored uptime is from before the restart and
        # is worthless here. Measured 2026-07-28: a press 3 s after Blue Iris came
        # back was reported as 33.7 s of lead-in and wrote ~2 s.
        #
        # A one-off failed poll on a healthy Blue Iris lands here too and will
        # under-report for one cycle. That is the safe direction and it is rare.
        if not self._online:
            lead = 0.0
        return lead

    def _record_twab(self, source: str, status: int, result: dict) -> dict:
        """Publish a press outcome to every connected page.

        Broadcast rather than left for the next poll: the point of the button is
        that the moment already happened, so a receipt that arrives seconds later
        is a worse answer than the same one arriving now. `seq` lets a page that
        fired the press itself skip re-rendering what it already drew.
        """
        self._twab_seq += 1
        self._twab_result = {
            "seq": self._twab_seq,
            "at": time.time(),
            "source": source,
            "status": status,
            "triggered": result.get("triggered") or [],
            "failures": result.get("failures") or {},
            "error": result.get("error"),
            # How much lead-in this press actually caught, and whether it landed
            # inside the previous press's post-roll (in which case Blue Iris
            # extended that clip instead of writing a new one). A receipt that
            # says only "saved" is a lie by omission when the answer is 1.2 s.
            "prerollSec": result.get("prerollSec"),
            "fullLead": result.get("fullLead"),
            "extended": result.get("extended"),
        }
        self._snapshot["twab"] = self._twab_result
        self._broadcast()
        return self._twab_result

    async def twab(self, source: str = "unknown") -> dict:
        """That Was Awesome: flush every overwatch camera's pre-roll to disk.

        Sends the Blue Iris ``trigger`` command to each overwatch camera. On a
        camera configured for triggered recording with a pre-trigger buffer,
        this writes the buffered last minute plus Break-time seconds of
        post-roll into a clip. On a camera *not* yet configured that way the
        trigger is harmless - nothing records, and the result says so.

        Serialised. Presses genuinely race - the physical button and the page, two
        phones, a firmware double-fire - and the button's own repress lockout does
        nothing about a press from somewhere else. Unserialised, every racer read
        the buffer state before any of them had updated it, so three simultaneous
        presses each reported a full 60 s buffer and ``extended: false`` while Blue
        Iris merged them into ONE clip per camera (measured 2026-07-29). The
        footage was fine; the receipts sent the operator looking for three files
        that did not exist. The Blue Iris client already serialises its own calls,
        so this adds ordering, not contention.
        """
        async with self._twab_lock:
            return await self._twab_locked(source)

    async def _twab_locked(self, source: str) -> dict:
        now = time.time()
        break_time = float(self.config.recording.break_time_seconds)
        full = float(self.config.recording.max_preroll_seconds)
        lead = self._expected_preroll(now)
        # A press inside the previous press's post-roll re-triggers a recording
        # that is still running, and Blue Iris extends that clip rather than
        # starting a second one. Measured: merged at a 4.4 s gap, two separate
        # clips at 12.4 s, with break time at 10 s.
        extended = bool(self._buffer_refilling_since
                        and now < self._buffer_refilling_since)

        cameras = sorted(self.state.overwatch)
        if not cameras:
            result = {"ok": False, "error": "no overwatch cameras selected",
                      "triggered": [], "failures": {}}
            # A 409 is still a press, and "nothing is on Watch" is exactly what
            # the operator needs to see - so it is published like any other.
            self._record_twab(source, 409, result)
            return result
        triggered: list[str] = []
        failures: dict[str, str] = {}

        # 🔑 Blue Iris accepts `trigger` for a camera that cannot record and
        # returns success. Measured 2026-07-27: CAM6, offline with a hung
        # encoder, came back {"triggered":["CAM6"],"failures":{}} and wrote zero
        # clips. Trusting that reply means the button goes green on a moment that
        # was never saved - the one failure this device exists to prevent.
        #
        # So the camera's health is checked here rather than inferred from the
        # reply. The poller's snapshot is used instead of a fresh camlist because
        # a TWAB press is on the critical path: the firmware abandons the request
        # after a few seconds, and a stale-by-one-poll verdict beats a timeout.
        known = {c["name"]: c for c in self._snapshot.get("cameras", [])}
        for camera in list(cameras):
            entry = known.get(camera)
            if entry is None:
                if known:  # a populated snapshot that lacks this camera is a real answer
                    failures[camera] = "camera not present in Blue Iris"
                    cameras.remove(camera)
                continue   # empty snapshot: unknown, so try anyway rather than refuse
            # 🔑 `bridgeOff` overrides Blue Iris's own opinion, and it has to.
            # Measured 2026-07-30: 20 s after CAM9's encoder was stopped, Blue
            # Iris still reported it enabled, online and signal-bearing - it
            # holds the last state while it retries the RTSP source. Trusting
            # that reading means triggering a camera that cannot possibly
            # record and telling the operator it saved. We know first-hand that
            # nothing is publishing that path; Blue Iris is guessing.
            if not entry.get("available") or entry.get("bridgeOff"):
                why = _why_unavailable(entry)
                failures[camera] = f"not recording: {why}"
                cameras.remove(camera)
                log.error("TWAB: refusing to report %s as saved - %s", camera, why)

        for index, camera in enumerate(cameras):
            try:
                await self.bi.trigger(camera)
                triggered.append(camera)
            except BlueIrisOffline as exc:
                # Host-level failure: every remaining camera would fail the
                # same way, each only after a full timeout - and the button
                # firmware gives up on the whole request at a few seconds.
                for remaining in cameras[index:]:
                    failures[remaining] = str(exc)
                log.error("TWAB: Blue Iris offline: %s", exc)
                break
            except BlueIrisError as exc:
                failures[camera] = str(exc)
                log.error("TWAB: could not trigger %s: %s", camera, exc)
        log.info("TWAB: triggered %s%s; lead-in %.1fs%s",
                 ", ".join(triggered) or "nothing",
                 f"; failed {failures}" if failures else "",
                 lead, " (extended the previous clip)" if extended else "")
        result = {"ok": not failures, "triggered": triggered, "failures": failures,
                  "prerollSec": round(lead, 1),
                  "fullLead": lead >= full - 3.0,
                  "extended": extended}
        if triggered:
            # The recording these triggers started will run until break time has
            # elapsed; only then does the buffer begin refilling. Recorded even on
            # a partial press, because any camera that did record drained its own
            # buffer. Not recorded when nothing triggered - nothing was consumed.
            self._buffer_refilling_since = time.time() + break_time
        # Same rule the route uses, kept here so what the pages render and what
        # the firmware reads can never disagree about how the press went.
        published = self._record_twab(source, 200 if triggered else 502, result)
        if triggered and self.config.recording.verify_clips:
            # Fire-and-forget on purpose: the press has already been answered.
            task = asyncio.create_task(
                self._verify_twab(published["seq"], list(triggered), now),
                name=f"twab-verify-{published['seq']}",
            )
            self._verify_tasks.add(task)
            task.add_done_callback(self._verify_tasks.discard)
        return result

    async def _decode_clip(self, path: pathlib.Path) -> tuple[bool, float, str]:
        """(decodable, seconds, why-not) by actually decoding the file.

        ffprobe is run as a subprocess rather than in a thread because it is the
        only way to be sure a hung probe cannot wedge the event loop - it gets a
        timeout and is killed.
        """
        if not path.exists():
            return False, 0.0, "file not on disk"
        ffprobe = find_ffprobe(self.config.recording.ffprobe_path)
        if not ffprobe:
            # Not fatal and not silent: verification is the layer that opens the
            # file, so losing it means going back to trusting the layer below.
            return False, 0.0, ("ffprobe not found - install ffmpeg (" +
                                install_hint() + ") or set recording.ffprobe_path")
        try:
            proc = await asyncio.create_subprocess_exec(
                ffprobe,
                "-v", "error", "-show_entries", "format=duration",
                "-of", "csv=p=0", str(path),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            return False, 0.0, f"ffprobe unavailable: {exc}"
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=20.0)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            return False, 0.0, "ffprobe timed out"
        detail = (err or b"").decode("utf-8", "replace").strip().replace("\n", " ")
        if proc.returncode != 0 or detail:
            return False, 0.0, detail[:120] or f"ffprobe exit {proc.returncode}"
        try:
            seconds = float((out or b"0").decode().strip())
        except ValueError:
            return False, 0.0, "unreadable duration"
        if seconds <= 0.05:
            return False, seconds, "zero-length"
        return True, seconds, ""

    async def _verify_twab(self, seq: int, cameras: list[str], trigger_at: float) -> None:
        """Decode what a press wrote, and say so if any of it is not real footage.

        Deliberately late and completely off the press path. The point is not to
        make the press slower or more cautious - it is that "the button went green"
        should eventually be checked against a file that plays, and today nothing
        checks that at all.
        """
        cfg = self.config.recording
        try:
            await asyncio.sleep(cfg.verify_delay_seconds)

            clip_dir = pathlib.Path(cfg.clip_dir)
            if not clip_dir.is_dir():
                log.warning("TWAB verify: clip directory %s is not reachable; "
                            "skipping verification", clip_dir)
                return

            # Only a clip that STARTS before the press can be the one it saved, and
            # that filter has to come first - taking the newest clip and rejecting
            # it afterwards reports "saved nothing" as soon as a later press has
            # written a newer file.
            since = int(trigger_at - cfg.max_preroll_seconds - 60)
            bad: dict[str, str] = {}
            checked = 0
            found: list[dict] = []
            for camera in cameras:
                try:
                    clips = await self.bi.cliplist(camera=camera, startdate=since)
                except (BlueIrisError, BlueIrisOffline) as exc:
                    bad[camera] = f"could not ask Blue Iris: {exc}"
                    continue
                candidates = [c for c in clips if (c.get("date") or 0) <= trigger_at + 2]
                clip = max(candidates, key=lambda c: c.get("date") or 0) if candidates else None
                if not clip or not clip.get("file"):
                    bad[camera] = "Blue Iris lists no clip for this press"
                    continue
                checked += 1
                ok, seconds, why = await self._decode_clip(clip_dir / clip["file"])
                if not ok:
                    bad[camera] = f"{clip['file']}: {why}"
                    log.error("TWAB verify: %s wrote %s and it does not decode - %s",
                              camera, clip["file"], why)
                else:
                    log.debug("TWAB verify: %s %s %.1fs ok", camera, clip["file"], seconds)
                # File the bad ones too. A clip that will not decode is still the
                # evidence of what went wrong, and hunting it later in a flat
                # directory of hundreds is the exact problem being solved here.
                found.append({"camera": camera, "file": clip["file"],
                              "date": clip.get("date"), "seconds": seconds,
                              "ok": ok, "why": why})

            verdict = {"at": time.time(), "checked": checked,
                       "cameras": len(cameras), "bad": bad}
            if bad:
                log.error("TWAB verify: %d of %d cameras saved nothing playable: %s",
                          len(bad), len(cameras), bad)
            else:
                log.info("TWAB verify: all %d clips decode", checked)

            # Sorting rides on verification rather than standing alone, because
            # verification is the only place that has already waited out break
            # time and *opened every file*. There is nothing left to guess about
            # whether Blue Iris has finished writing.
            press = self._twab_result if (self._twab_result
                                          and self._twab_result.get("seq") == seq) else {}
            verdict["sort"] = await self.sorter.sort(
                "twab", found, event_at=trigger_at, seq=seq,
                extra={"source": press.get("source"),
                       "prerollSec": press.get("prerollSec"),
                       "fullLead": press.get("fullLead"),
                       "extended": press.get("extended"),
                       "notSaved": bad},
            )

            # Only attach it if this is still the press on screen. A newer press
            # has its own verification coming, and overwriting its receipt with an
            # older one would report the wrong event.
            if self._twab_result and self._twab_result.get("seq") == seq:
                self._twab_result["verify"] = verdict
                self._snapshot["twab"] = self._twab_result
                self._broadcast()
        except asyncio.CancelledError:
            raise
        except Exception:
            # Verification is a safety net. A broken net must not take down the
            # thing it is under.
            log.exception("TWAB verify failed unexpectedly")

    async def update_settings(self, changes: dict) -> None:
        self.state.update_settings(changes)
        await self._refresh()

    async def _refresh(self) -> None:
        """Rebuild and push immediately so the UI never waits for the next poll."""
        with contextlib.suppress(Exception):
            await self._poll_once()

    async def start_recording(self) -> dict:
        """Arm set -> Blue Iris. Best effort: report what failed, keep what worked."""
        async with self._record_lock:
            if self.state.take.active:
                return {"ok": True, "already": True}

            cameras = sorted(self.state.armed)
            if not cameras:
                return {"ok": False, "error": "nothing is armed"}

            self._settle_until = time.monotonic() + SETTLE_SECONDS

            started: list[str] = []
            failures: dict[str, str] = {}

            # Sequential rather than gathered: Blue Iris serialises these anyway,
            # and firing them in order keeps the failure attribution honest.
            for camera in cameras:
                try:
                    await self.bi.set_manrec(camera, True)
                    started.append(camera)
                except (BlueIrisError, BlueIrisOffline) as exc:
                    failures[camera] = str(exc)
                    log.error("could not start recording on %s: %s", camera, exc)

            if not started:
                return {"ok": False, "error": "no camera would start", "failures": failures}

            take = self.state.begin(cameras, started, failures)
            await self._refresh()
            # Hooks fire after the recording is running and after the UI has
            # been told. They are a side effect of recording, never a gate on it.
            self.hooks.record_start(started, take.audio_matters)
            return {"ok": True, "started": started, "failures": failures}

    async def stop_recording(self) -> dict:
        async with self._record_lock:
            take = self.state.take
            if not take.active:
                return {"ok": True, "already": True}

            # Stop everything we started *and* anything Blue Iris says is
            # recording - the two can differ if a camera was adopted mid-take.
            self._settle_until = time.monotonic() + SETTLE_SECONDS
            targets = set(take.started) | {
                c["name"] for c in self._snapshot.get("cameras", []) if c["recording"]
            }
            stopped: list[str] = []
            failures: dict[str, str] = {}
            for camera in sorted(targets):
                try:
                    await self.bi.set_manrec(camera, False)
                    stopped.append(camera)
                except (BlueIrisError, BlueIrisOffline) as exc:
                    failures[camera] = str(exc)
                    log.error("could not stop recording on %s: %s", camera, exc)

            # A manual take writes the pre-trigger buffer into its own clip and
            # empties it exactly like a TWAB press does, so a press straight after
            # a take has no more lead-in than a press straight after a press. The
            # buffer starts refilling now, with no break time to wait out -
            # manual recording stops the moment it is told to.
            #
            # ⚠️ Deliberately coarse: only the ARMED cameras were recording, so the
            # rest still hold a full buffer, and this will under-report their
            # lead-in until it refills. That is the safe direction - the receipt
            # promises less than it delivers, never more - and per-camera buffer
            # tracking is not worth the complexity until a take and a press
            # routinely land close together. Measured 2026-07-28: a press *during*
            # a take is fine, and an armed camera writes ONE file covering both
            # (tools/twab_during_take_test.py).
            self._buffer_refilling_since = time.time()

            started_at = take.started_at
            audio_matters = take.audio_matters
            self.state.end(clips=[])
            self._collecting = True
            await self._refresh()

            # Clip collection can take several seconds - Blue Iris commits each
            # camera's database record at its own pace. Do not make the operator
            # watch a spinning STOP button for that: the recording has already
            # stopped, which is the part that was urgent. Clips arrive over SSE
            # when they are ready.
            asyncio.create_task(
                self._finalise(stopped, started_at, audio_matters, failures),
                name="finalise-take",
            )
            return {"ok": True, "stopped": stopped, "failures": failures,
                    "collecting": True}

    async def _finalise(self, stopped: list[str], started_at: float,
                        audio_matters: bool, failures: dict[str, str]) -> None:
        """Gather the take's clips, then fire the stop hook."""
        try:
            clips = await self._collect_clips(stopped, started_at)
            self.state.take.clips = clips
            self.state.take.failures = failures
        except Exception:
            log.exception("could not collect clips for the finished take")
            clips = []
        finally:
            self._collecting = False
            with contextlib.suppress(Exception):
                await self._refresh()

        # A take gets the same treatment as a press. _collect_clips has already
        # polled until Blue Iris's reported size and duration stopped moving, so
        # the files are as settled here as they are on the press path.
        #
        # ⚠️ A clip flagged `spans` is a longer file this take was appended into,
        # so filing it copies footage from before the take as well. That is the
        # right answer - it is where the take's footage actually is - but it can
        # be much larger than the take itself.
        if clips:
            await self.sorter.sort(
                "take", clips, event_at=started_at,
                extra={"stoppedCameras": sorted(stopped),
                       "audioMatters": audio_matters,
                       "notSaved": failures},
            )

        self.hooks.record_stop(stopped, clips, audio_matters)

    async def _collect_clips(self, cameras: list[str], since: float) -> list[dict]:
        """Clips belonging to the take that just ended.

        "Find the new clip" is not quite the right question. The right one is
        "what file is this take's footage in", which *may* be a file that already
        existed - Blue Iris can group consecutive recordings into one continuing
        file rather than starting a new one. Each camera's newest clip is
        reported, flagged `spans` when the take landed inside a longer recording,
        so the operator is told where the footage is instead of being shown an
        empty list and left to assume the take was lost.

        🔑 A clip that starts before the take is NOT evidence of that, because a
        camera with a stream buffer writes its pre-roll into the clip - measured
        2026-07-27, a `manrec` take on a 60 s-buffered camera produces a clip
        starting 59 s early, in a brand new file. So the comparison has to allow
        for ``recording.max_preroll_seconds``; without that, `spans` reads true
        on every take on every buffered camera.

        ⚠️ An earlier version of this docstring blamed the per-camera
        `moviegroup` setting and named CAM4/CAM7 as appenders. That was wrong:
        `moviegroup` is **0 in all 72 places it appears in the registry**, every
        take wrote a new file, and the split tracked `movieroll` (the stream
        buffer), not camera identity. Grouping is real in Blue Iris but is not
        enabled here. See ``docs/mode-testing-runlog-2026-07-27.md``.

        Blue Iris also lists a clip before it has finished writing it, and until
        then the reported size and duration are nonsense - a freshly stopped
        seven-second take will happily report "1 sec (48B)". So poll until the
        numbers stop moving.
        """
        # Look back far enough to catch a file the take was appended to.
        window_start = int(since) - 6 * 3600

        async def newest_per_camera() -> dict[str, dict]:
            newest: dict[str, dict] = {}
            for camera in cameras:
                try:
                    found = await self.bi.cliplist(camera, window_start)
                except (BlueIrisError, BlueIrisOffline) as exc:
                    log.warning("cliplist for %s failed: %s", camera, exc)
                    continue
                if found:
                    newest[camera] = max(found, key=lambda c: c.get("date") or 0)
            return newest

        current: dict[str, dict] = {}
        signature: tuple | None = None
        for attempt in range(CLIP_SETTLE_TRIES):
            current = await newest_per_camera()
            now = tuple(sorted(
                (name, clip.get("file"), clip.get("msec"), clip.get("filesize"))
                for name, clip in current.items()
            ))
            settled = now == signature and len(current) == len(cameras)
            signature = now
            if settled and current:
                break
            if attempt < CLIP_SETTLE_TRIES - 1:
                await asyncio.sleep(CLIP_SETTLE_DELAY)

        missing = set(cameras) - set(current)
        if missing:
            log.warning("no clip at all for %s - check that camera is writing files",
                        ", ".join(sorted(missing)))

        clips: list[dict] = []
        for name, clip in current.items():
            clip = dict(clip)
            clip["url"] = f"/api/clip/{clip.get('file', '')}"
            # A clip the take was appended to starts before the take by more than
            # any pre-roll could account for. Inside that margin it is just the
            # stream buffer doing its job.
            preroll = max(0, int(self.config.recording.max_preroll_seconds))
            clip["spans"] = (clip.get("date") or 0) < int(since) - preroll - 5
            filetype = str(clip.get("filetype") or "")
            clip["codec"] = ("H265" if "H265" in filetype.upper()
                             else "H264" if "H264" in filetype.upper() else "")
            clips.append(clip)

        clips.sort(key=lambda c: (c.get("camera") or ""))
        if any(c["spans"] for c in clips):
            log.info("some cameras appended this take to an existing file: %s",
                     ", ".join(c["camera"] for c in clips if c["spans"]))
        return clips

    # -------------------------------------------------------------- thumbnails

    async def _thumb_loop(self) -> None:
        """Refresh camera thumbnails slowly.

        These exist so you can tell CAM3 from CAM6 at a glance, not to be a video
        wall. The default interval is ten minutes because the machine serving
        these frames is usually the machine recording your footage.

        The one exception is the very first pass: we do it as soon as Blue Iris is
        online and the camera list has arrived, so the wall is populated at
        startup instead of showing "no image" until the first interval elapses.
        """
        while True:
            cams = [c for c in self._snapshot.get("cameras", []) if c["available"]]
            if not self._online or not cams:
                # Not ready yet - the poller has not brought us online or handed
                # us a camera list. Check back shortly rather than burning the
                # full (ten-minute) interval before the first fetch.
                await asyncio.sleep(1.0)
                continue

            fetched = False
            for cam in cams:
                try:
                    data = await self.bi.snapshot(cam["name"])
                    self._thumbs[cam["name"]] = (time.time(), data)
                    fetched = True
                except (BlueIrisError, BlueIrisOffline) as exc:
                    log.debug("thumbnail for %s failed: %s", cam["name"], exc)
                await asyncio.sleep(0.4)  # stagger; do not burst the encoder

            if fetched:
                # Let connected browsers know a thumbnail now exists without
                # waiting for the next poll to rebuild the snapshot.
                self._mark_thumbs_and_broadcast()

            interval = max(30, int(self.state.settings.thumbnail_interval))
            await asyncio.sleep(interval)

    def _mark_thumbs_and_broadcast(self) -> None:
        """Refresh ``hasThumb`` on the current snapshot and push it to clients.

        The image URLs are cache-busted on ``ts``, so bumping it makes browsers
        re-request the thumbnails that just arrived.
        """
        for cam in self._snapshot.get("cameras", []):
            cam["hasThumb"] = cam["name"] in self._thumbs
        self._snapshot["ts"] = time.time()
        self._broadcast()

    def thumbnail(self, camera: str) -> tuple[float, bytes] | None:
        return self._thumbs.get(camera)

    async def refresh_thumbnail(self, camera: str) -> bytes | None:
        try:
            data = await self.bi.snapshot(camera)
        except (BlueIrisError, BlueIrisOffline):
            return None
        self._thumbs[camera] = (time.time(), data)
        return data
