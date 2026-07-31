# USB → H.264 → RTSP → your recorder

*How a webcam becomes something a network video recorder will talk to.*

A network video recorder speaks to IP cameras. A USB webcam, a capture card and a
desktop grab are not IP cameras. The bridge is one ffmpeg process per device: it
opens the device, encodes H.264, and publishes over RTSP. The recorder subscribes
and cannot tell the difference.

```
  webcam ──USB──> ffmpeg ──RTSP──> MediaMTX ──RTSP──> Blue Iris ──> disk
                    │
                    └── H.264, and a small substream for the live grid
```

You need an RTSP **server** in the middle. ffmpeg can publish but not serve.
[MediaMTX](https://github.com/bluenviron/mediamtx) is a single binary with a
usable default config; point ffmpeg at `rtsp://127.0.0.1:8554/cam1` and point the
recorder at the same URL.

Get a starting command with:

```
shopcam encode --list
shopcam encode --device "Your Camera" --url rtsp://127.0.0.1:8554/cam1 \
               --substream-url rtsp://127.0.0.1:8554/cam1_sub --dry-run
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

### 2. Put the substream on the CPU, not the GPU

A substream is a small second stream (640×480@15) for the recorder's live grid,
so it is not decoding four 1080p feeds to draw thumbnails.

Encode it with **libx264, not NVENC**, even when the main stream is NVENC.
Consumer GeForce drivers have historically capped concurrent NVENC sessions
(long at 2–3, later raised). Three cameras × 2 streams = 6 sessions, and you fall
off a cliff with no obvious error. 640×480@15 on x264 is negligible CPU.

> ⚠️ It is also where the power goes. Switching four bridges off on the reference
> rig saved **6.8 W of GPU and 34 percentage points of CPU** — and the CPU half
> was the x264 substreams, not the NVENC main streams. If you are surprised by
> your idle load, this is why.

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
