# USB → H.264 → RTSP → your recorder

*How a webcam becomes something a network video recorder will talk to.*

A network video recorder speaks to IP cameras. A USB webcam, a capture card and a
desktop grab are not IP cameras. The bridge is one ffmpeg process per device: it
opens the device, encodes H.264, and publishes over RTSP. The recorder subscribes
and cannot tell the difference.

```
  webcam ──USB──> ffmpeg ──RTSP──> MediaMTX ──RTSP──> Blue Iris ──> disk
                    │
                    └── one H.264 stream (no substream: see finding 2)
```

You need an RTSP **server** in the middle. ffmpeg can publish but not serve.
[MediaMTX](https://github.com/bluenviron/mediamtx) is a single binary with a
usable default config; point ffmpeg at `rtsp://127.0.0.1:8554/cam1` and point the
recorder at the same URL.

Get a starting command with:

```
shopcam encode --list
shopcam encode --device "Your Camera" --audio-device "Your Camera Mic" \
               --encoder h264_nvenc --url rtsp://127.0.0.1:8554/cam1 --dry-run
```

`--dry-run` prints the command instead of running it. Keep it in a script you can
read. The thing running 24/7 next to your footage should not be hidden inside a
Python process.

---

## 🔑 Four findings, each of which cost an evening

### 1. `-pix_fmt` is load-bearing, and the bug it causes is invisible to testing

Left unset, ffmpeg picks a pixel format per source. On the reference rig it chose
**yuvj444p** for one camera and **yuvj420p** for another — both perfectly valid
H.264.

Blue Iris decodes 4:4:4 differently. That camera rendered **5.76 percentage
points off in R/G**: a visible colour shift between two angles in the same cut,
which is exactly the thing a multi-camera edit cannot survive.

> ⚠️ **An ffmpeg-to-ffmpeg check cannot see this.** Decode the bridge output with
> ffmpeg and it is correct. The difference is in the **consumer's** decoder. If
> you are diffing your own output against your own input you will conclude
> everything is fine, and you will be wrong.

**Always pin `-pix_fmt`.** `yuvj420p` unless you have a specific reason.

### 2. One stream, no B-frames, no slices (what Blue Iris needs)

These three were found the hard way on Blue Iris and are locked on the reference
rig (2026-08-20). `shopcam encode` builds them in.

- **No substream.** A substream is the usual advice for a recorder's live grid.
  With Blue Iris consuming a bridge's substream, the *main* recording lost frames:
  2–10 gaps per ~2-minute take, and **zero** with the substream unused, on a wire
  that captured clean both times. Publish one stream and let Blue Iris decode it
  for the grid (four 1080p30 mains cost it ~35% CPU on the reference box).
- **`-bf 0`.** Blue Iris trims B-frames from the oldest part of the pre-roll:
  with NVENC's automatic B-frames there were ~107 ms gaps at 1 Hz across the
  first ~8 s of every saved clip. With `-bf 0`, none.
- **No x264 slices.** `-tune zerolatency` turns on sliced threads (5 slices per
  frame), and **Blue Iris counts slices as frames**: a 15 fps stream read as
  75 fps, and Blue Iris "fixed" the A/V sync by discarding audio, a 265 ms hole
  every 2.3 s. Any libx264 stream gets `-x264-params sliced-threads=0` (plus
  `-threads 1` on a small one like the microphone's waveform).

The locked NVENC main stream, per bridged camera:

```
-f dshow -vcodec mjpeg -video_size 1920x1080 -framerate 30 -use_wallclock_as_timestamps 1
-pix_fmt yuvj420p
-c:v h264_nvenc -preset p4 -tune ll -rc cbr -b:v 8M -maxrate 8M -bufsize 8M -g 30 -bf 0 -delay 0
-c:a aac -b:a 128k -ar 48000 -ac 1
-f rtsp -rtsp_transport tcp rtsp://127.0.0.1:8554/cam1
```

The exact scripts the rig runs are in [`contrib/windows/bridge/`](../contrib/windows/bridge/)
(`run_cam1.cmd` is the reference). Two per-camera additions you may need:

- **A camera whose clock runs fast** (one declared 30 fps and delivered 32.3):
  wall-clock timestamps plus `--hold-fps` (`-vf fps=30`). `-r 30` alone does not fix it.
- **A camera whose audio slides in 200 ms steps** against the voice track (dshow
  drops audio chunks while its timestamps stay continuous): `-rtbufsize 256M
  -audio_buffer_size 500` on the input and `-af aresample=async=1000:first_pts=0`.
  `shopcam encode` adds both whenever `--audio-device` is set. It turns a lost chunk
  into a short silence in the right place; it cannot recover a loss inside the
  device itself (one camera on the rig still does that, and it is synced by picture).

> ⚠️ Power: four bridges, when each also ran an x264 substream, cost **6.8 W of
> GPU and 34 percentage points of CPU**, most of the CPU in the substreams. If you
> keep a substream for another recorder, encode it with libx264, not NVENC:
> consumer GeForce drivers have historically capped concurrent NVENC sessions.

### 3. Try the MJPEG pin before the H.264 pin

Many UVC cameras advertise a native H.264 pin. On some hosts it delivers **zero
frames**, silently — ffmpeg sits there and nothing happens.

Take MJPEG in and re-encode to H.264. It costs CPU you have and it works.
`shopcam encode` defaults to `-vcodec mjpeg` for this reason; `--input-codec ""`
leaves the choice to ffmpeg.

### 4. Get the device name exactly right

The device name is passed straight through. On Windows it must match **exactly**,
including case and punctuation. The name in Device Manager is frequently not the
name DirectShow uses. `shopcam encode --list` prints ffmpeg's own spelling —
copy from there.

> Enumeration exits non-zero and prints to stderr **by design** (there is no
> input to open). That is not an error.

---

## Switching them off

Encoders run whether or not anyone is filming. The Controller can switch them per
camera, but it **never starts or stops a process** — it writes desired state to a
file and a supervisor reconciles.

Kill a bridge directly and the supervisor restarts it within its poll interval
and logs a fault, which is exactly the job you gave it. *Declare intent; do not
fight your own supervisor.* A reference supervisor for Windows is in
[`contrib/windows/`](../contrib/windows/).

⚠️ **A camera whose encoder is off is offline in the recorder.** It cannot record
and That-Was-Awesome will not save it. The UI says so, by name, before you do it.

Coming back is not instant. Measured, all four at once:

| | |
|---|---|
| encoder process up | ~3 s |
| recorder at full fps | ~20 s |
| **full 60 s of pre-roll** | **~80 s** |

The wait is the recorder's reconnect, not the encoder. There is nothing to tune.
