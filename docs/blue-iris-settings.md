# Blue Iris: the recording config that works

*The settings the reference rig is locked on (2026-08-20), and the measurement behind each one.*

Blue Iris can do everything That Was Awesome needs, but almost none of it is the
default. The button only guarantees that a `trigger` fires; whether that trigger
saves the previous minute is decided here.

---

## Two modes per camera

| Mode | How it is set | What it does |
|---|---|---|
| **Record** (a manual take) | `camconfig manrec=true` / `false` (the Controller's Record button) | Records until stopped. Set the global `manrecsec=0`, or Blue Iris silently caps a manual clip at 30 s (with it at 0, a 45.2 s hold gave a 45.90 s clip). A manual take also flushes the buffer, so it starts up to ~59 s before the command. |
| **Watch** (That Was Awesome) | Camera record mode "When triggered", **motion detection off** | Nothing is written until a `trigger`. Then the buffer plus the break time become one clip. Motion off means only the button can trigger it. |

## 🔑 The pre-roll lives in `movieroll`, not `rectime`

Per camera, per profile, under
`HKLM\SOFTWARE\Perspective Software\Blue Iris\Cameras\<cam>\Clips\<profile>\`.
Every camera has **seven** profiles and every value is in **deciseconds**.

| Key | Value | Why |
|---|---|---|
| `rectime` / `playtime` | `600` | Pre-trigger record time, 60 s |
| `movieroll` | `600` | 🔴 **The actual stream buffer.** The default is `50` (5 s) and it silently caps `rectime`. Measured: `rectime=600` with `movieroll=50` saved **4.1 s** of lead-in; with `movieroll=600`, **58.7 s**. |
| `continuous` | `0` | Not continuous recording |
| `moviegroup` | `0` | Do not combine clips (combining held files open and locked them) |
| `movieformat` | `3` | MP4. (AVI 0, BVR 1, WMV 2, MP4 3. `defformat` is *not* the format key.) |
| break time | 10 s (`setmotion.breaktime=100`) | Post-roll after the trigger, fleet-wide. Matches `break_time_seconds` in the Controller config. |

[`contrib/windows/bridge/set_pretrigger.ps1`](../contrib/windows/bridge/set_pretrigger.ps1)
writes these. How the buffer behaves between presses is in [preroll.md](preroll.md).

> ⚠️ A working button with `movieroll` at its default saves five seconds of the
> moment and none of the run-up. Everything reports success.

## USB cameras need a bridge to have a buffer at all

Blue Iris keeps an **encoded** pre-trigger buffer only for a **network** camera
recording direct to disk. A USB camera's buffer is raw frames, capped at about one
second: changing one's pre-trigger from 20 s to 5 s moved the saved clip by 10 ms,
and the real lead-in was ~0.1 s. Bridge every USB device to RTSP
([usb-to-h264.md](usb-to-h264.md)); the same camera then saved 58.2 s.

## The rest of the lock

- **Direct to disk, no re-encode.** Blue Iris stream-copies the cameras' H.264.
  Seven cameras measured 31–35% CPU with 0% NVDEC.
- **Hardware decode (`ip_hwaccel`) = No or NVIDIA, never Intel** on a machine with
  no active iGPU: the Intel path fell back after a probe timeout on every stream,
  so streams opened slowly.
- **Live view at 15 fps** (`Options\livefps=15`). Recording is unaffected.
- **MP4, not BVR.** BVR dropped video at the same rate as MP4 and showed the same
  audio zero-runs (1.18/min over 11 minutes); Blue Iris zeroes that audio upstream of
  any container. And the Blue Iris timeline is **one lane**, not one per camera, and
  MP4 draws it fine, so "the timeline needs BVR" does not hold.
- **One keyframe per second on every source.** Bridges `-g 30` at 30 fps, the
  microphone `-g 15` at 15 fps, IP cameras' own GOP set to 30. It costs +11.4% in
  file size and is the biggest single improvement to scrubbing; keyframes also line
  up across cameras.
- **No substreams on bridged cameras** (below).

### Root causes behind the lock

1. **A consumed substream poisons the main recording.** With a bridge's substream in
   use: 2–10 video gaps per ~114 s take. Unused: zero. The network capture was clean
   in both, so the loss is inside Blue Iris. Bridges publish one stream
   (`ip_subpath=''`); Blue Iris decodes the mains for its grid.
2. **Blue Iris trims B-frames from the oldest part of the pre-roll.** NVENC's
   automatic B-frames left ~107 ms gaps at 1 Hz in the first ~8 s of every clip.
   `-bf 0` on the bridges removed them. (The IP cameras emit none.)
3. **Blue Iris counts x264 slices as frames.** A sliced 15 fps stream read as
   75 fps, and Blue Iris discarded audio to reconcile it: a 265 ms hole every 2.3 s.
   Every x264 stream is encoded with `sliced-threads=0`.
4. **Blue Iris does not resample frame rates.** It writes variable-frame-rate MP4
   on a 1/90000 timebase with real arrival times. ffprobe's `r_frame_rate` is
   meaningless on these files; use `nb_frames ÷ duration`.
5. **Residual audio dropouts are accepted.** About one short silence per minute on
   the bridged cameras' scratch audio, proportional to video bitrate (the microphone,
   at ~60 kbps, shows none). The silence is written in place, so A/V holds within
   3 ms over 11 minutes, and the voice has its own track.

## Clip names are a minute late

Blue Iris names a clip after the **trigger instant, in UTC** (`CAM8.20260730_032815Z.mp4`).
The footage starts ~60 s earlier: `start = file creation time − pre-roll`,
`end = creation time + break time`. The Controller copies each press (never
moves, so Blue Iris's database keeps working) into a folder named for the earliest
real clip start in local time, renames the files to their real start, and writes a
`shopcam.json` manifest. Grouping happens in the Controller, not a folder watcher,
because about a third of presses straddle a second boundary.

Disk: nine cameras recording is **21.7 GB/hour**; one nine-camera press is about
**460 MB**.

## Changing the config from a script

- **Force-kill Blue Iris first.** A clean exit writes its in-memory config back to
  the registry and undoes your edit (and it ignores a polite window close).
  Kill, `reg add`, wait at least 5 s, relaunch.
- **Relaunch elevated** (`schtasks ... /rl HIGHEST`), or it starts without its web
  server.
- The hive is **HKLM**, not HKCU.
- Blue Iris has **no API to create a camera**.
  [`add_rtsp_camera.ps1`](../contrib/windows/bridge/add_rtsp_camera.ps1) clones a
  working camera's registry tree and repoints it.
- `camconfig` returns `success` for parameters it does not apply (`ptz=`,
  `enabled=`). **Read everything back** ([blue-iris-api.md](blue-iris-api.md)).
