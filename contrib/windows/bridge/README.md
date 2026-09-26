# USB cameras → Blue Iris: the bridge scripts, as run on the reference rig

Blue Iris talks to IP cameras. A USB webcam is not one. Each script here is one
ffmpeg process that opens a USB device (or the desktop, or a microphone),
encodes H.264, and publishes it as an RTSP stream on `rtsp://127.0.0.1:8554/<name>`
through [MediaMTX](https://github.com/bluenviron/mediamtx). Blue Iris then
subscribes to that URL exactly as it would to a network camera - and gets the
pre-roll buffer, the direct-to-disk recording and the trigger behaviour that a
USB camera in Blue Iris never gets (measured: a pre-trigger setting on a USB
camera *writes* and does nothing).

`shopcam encode --dry-run` generates a command like these from scratch. The
scripts below are the ones actually running on the reference rig, with every
flag argued for in the comments. Copy the closest one and change the device name.

| Script | What it bridges | Notes |
|---|---|---|
| `run_cam1.cmd` | OBSBOT Tiny 4K, USB, 1080p30 + its own mic | `-pix_fmt` is required (see `docs/usb-to-h264.md`); wall-clock audio timestamps so a dropout cannot drift the track |
| `run_cam5.cmd` | OBSBOT Tiny, USB, with a lav receiver mixed in | Same audio treatment; this device still steps ±0.2 s on its own, sync it by picture |
| `run_cam8.cmd` | A generic top-down bench USB camera | The fps fix that cured a 32.3 fps stream and the x264 slice miscount |
| `run_cam9.cmd` | The desktop itself (gdigrab) | A screen recording as a camera, so the rig's own display is one more angle |
| `run_mic1.cmd` | A wireless lav receiver on USB, **audio only** | Publishes a waveform as its picture so the supervisor's video health check passes. `docs/audio.md` |
| `run_controller.cmd` | The Controller itself | `python.exe`, never `pythonw.exe` - the banner print kills it silently under pythonw |
| `supervisor.ps1` | Keeps all of the above alive | Reads the Controller's desired-state file, starts/stops to match, health-checks by pulling a frame, not by listing processes |
| `install_supervisor_task.ps1`, `install_controller_task.ps1` | Scheduled Tasks at logon | Uses `$env:USERNAME` |
| `toggle_bridge.ps1` | Switch a bridge on/off through the Controller API and time it | |
| `add_rtsp_camera.ps1` | **Add a new camera to Blue Iris** that pulls one of these streams | Clones a working network camera's registry tree and repoints it. Blue Iris has no create-camera API |
| `convert_cam_to_rtsp.ps1` | **Convert an existing USB camera in Blue Iris** into an RTSP one | Same trick; keeps the camera's identity, saves a `.reg` fallback first |
| `set_pretrigger.ps1` | Set a camera's pre-trigger buffer per profile, in the registry | Only meaningful on a network (bridged) camera |

## Adding your own USB camera, start to finish

1. `shopcam encode --list` - find the device's exact DirectShow name.
2. Copy `run_cam8.cmd` to `run_<name>.cmd`, put the device name in, pick a stream
   path (`/cam10`), and run it in a window. `ffprobe rtsp://127.0.0.1:8554/cam10`
   should show a video stream within a few seconds.
3. Register it in Blue Iris: `add_rtsp_camera.ps1 -Cam CAM10 -Template <a working IP camera> -StreamPath /cam10 -Index <n>`.
   (Or, by hand: Add camera → Network IP → RTSP, that URL, no auth.)
4. Add `"cam10:CAM10"` to `[bridges] bridges` in `config.toml` so the Controller
   gets an encoder switch for it, and add the component to `supervisor.ps1`.
5. `shopcam doctor`.

The registry scripts stop Blue Iris with a **forced** kill on purpose: a clean
exit rewrites the registry from memory and undoes the edit. They refuse to run
if the target key already exists, and they export a fallback first.

Log files go to `C:\shopcam2000-bridge\` by default; change the path at the
top of each script if you keep things elsewhere.
