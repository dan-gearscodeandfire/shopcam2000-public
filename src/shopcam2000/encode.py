"""``shopcam encode`` - turn a local device into an RTSP camera.

A USB webcam, a capture card or a desktop grab is not something a network video
recorder can talk to. This builds the ffmpeg command that makes it one.

It is a COMMAND BUILDER, not a daemon. `--dry-run` prints the command so you can
paste it into a .cmd/.sh, read it, and own it. That is deliberate: the thing you
run 24/7 next to your footage should be something you can read, not something a
Python process is hiding from you.

────────────────────────────────────────────────────────────────────────────────
Everything below was measured on the reference rig. These are the four findings
that cost an evening each, and none of them are in any tutorial.

🔑 1. -pix_fmt IS LOAD-BEARING.
   Left unset, ffmpeg chose yuvj444p for one camera and yuvj420p for another.
   Blue Iris decodes 4:4:4 differently, and that camera rendered 5.76 percentage
   points off in R/G - a visible colour shift between angles in the same cut.
   ⚠️ An ffmpeg-to-ffmpeg check CANNOT SEE THIS. The bridge output is correct;
   it is the CONSUMER's decode that differs. Pin the format.

🔑 2. THE SUBSTREAM GOES ON THE CPU, NOT THE GPU.
   Consumer GeForce drivers have historically capped concurrent NVENC sessions
   (long at 2-3, later raised). Three cameras x 2 streams = 6 sessions and you
   fall off a cliff you cannot see coming. A 640x480@15 substream on libx264 is
   negligible CPU and removes the risk entirely.
   ⚠️ It is also where the power goes: switching four bridges off saved 6.8 W of
   GPU and 34 percentage points of CPU - and the CPU part was the x264
   substreams, not the NVENC main streams.

🔑 3. TRY THE MJPEG PIN BEFORE THE H.264 PIN.
   Many UVC cameras advertise a native h264 pin that delivers ZERO frames on
   some hosts. MJPEG in, H.264 out, re-encoded, is the boring thing that works.

🔑 4. A MICROPHONE NEEDS A PICTURE.
   Health checks ask ffprobe for a video stream; an audio-only source has none,
   so a supervisor will call it dead and restart it for ever. Give it a
   synthetic video track and every existing tool works unmodified.
   ⚠️ Make it a WAVEFORM, not black - see `audio_command`.
────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import shlex
import subprocess
import sys

from .paths import find_ffmpeg, install_hint

# dshow on Windows, avfoundation on macOS, v4l2 on Linux.
_INPUT_FORMAT = {"win32": "dshow", "darwin": "avfoundation"}


def input_format() -> str:
    return _INPUT_FORMAT.get(sys.platform, "v4l2")


def list_devices() -> int:
    """Print the capture devices ffmpeg can see, in ffmpeg's own spelling.

    🔴 The spelling matters more than it looks. A device name is passed straight
    through to ffmpeg, and on Windows it must match EXACTLY, including case and
    punctuation. Guessing it from Device Manager does not work.
    """
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        print(f"ffmpeg not found. Install: {install_hint()}", file=sys.stderr)
        return 1

    fmt = input_format()
    if fmt == "dshow":
        args = [ffmpeg, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"]
    elif fmt == "avfoundation":
        args = [ffmpeg, "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""]
    else:
        print("Linux: v4l2 devices are files. Listing /dev/video*:\n")
        subprocess.run(["sh", "-c", "ls -1 /dev/video* 2>/dev/null; v4l2-ctl --list-devices 2>/dev/null || true"])
        return 0

    # These enumeration modes exit non-zero and print to stderr BY DESIGN - there
    # is no input to open. Treating that as failure is a classic wrong turn.
    proc = subprocess.run(args, capture_output=True, text=True)
    print(proc.stderr or proc.stdout)
    return 0


def video_command(
    device: str,
    url: str,
    *,
    audio_device: str = "",
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
    bitrate: str = "8M",
    encoder: str = "libx264",
    pix_fmt: str = "yuvj420p",
    input_codec: str = "mjpeg",
    substream_url: str = "",
) -> list[str]:
    """The main stream, and optionally a low-res substream for the live grid."""
    ffmpeg = find_ffmpeg() or "ffmpeg"
    fmt = input_format()

    cmd = [ffmpeg, "-hide_banner", "-y", "-f", fmt]
    if fmt == "dshow":
        if input_codec:
            cmd += ["-vcodec", input_codec]          # 🔑 finding 3
        cmd += ["-video_size", f"{width}x{height}", "-framerate", str(fps),
                "-audio_buffer_size", "80"]
        source = f"video={device}"
        if audio_device:
            source += f":audio={audio_device}"
        cmd += ["-i", source]
    else:
        cmd += ["-framerate", str(fps), "-video_size", f"{width}x{height}", "-i", device]

    cmd += ["-map", "0:v"]
    if audio_device:
        cmd += ["-map", "0:a"]

    cmd += ["-pix_fmt", pix_fmt]                     # 🔑 finding 1 - never omit
    cmd += ["-c:v", encoder]
    if encoder.endswith("nvenc"):
        cmd += ["-preset", "p4"]
    else:
        cmd += ["-preset", "veryfast", "-tune", "zerolatency"]
    cmd += ["-b:v", bitrate, "-maxrate", bitrate, "-bufsize", "16M", "-g", str(fps)]

    if audio_device:
        cmd += ["-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "1"]

    cmd += ["-f", "rtsp", "-rtsp_transport", "tcp", url]

    if substream_url:
        # 🔑 finding 2 - always libx264 here, whatever the main stream uses.
        cmd += ["-map", "0:v", "-an",
                "-vf", "scale=640:480,fps=15",
                "-pix_fmt", "yuv420p",
                "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency",
                "-b:v", "512k", "-maxrate", "768k", "-bufsize", "1M", "-g", "15",
                "-f", "rtsp", "-rtsp_transport", "tcp", substream_url]
    return cmd


def audio_command(audio_device: str, url: str, *, bitrate: str = "192k") -> list[str]:
    """A microphone as its own camera, with a waveform for a picture.

    Why a picture at all: 🔑 finding 4 above.

    Why a WAVEFORM and not a black rectangle - this is the part that is only
    obvious after it bites you:

      showwaves on TRUE SILENCE DRAWS NOTHING. The first frame pulled off the
      reference rig's mic bridge was pure black, which is exactly what a DEAD
      camera looks like in a recorder's grid. So the picture carries a drawn
      GRID and a CENTRE LINE underneath the trace. Now "live but quiet" reads as
      a flat line on a lit tile, and "dead" still reads as black. Measured, not
      assumed.

    It also costs nothing and is usable B-roll, which if you are instrumenting a
    workshop is rather the point.
    """
    ffmpeg = find_ffmpeg() or "ffmpeg"
    fmt = input_format()
    source = f"audio={audio_device}" if fmt == "dshow" else audio_device

    graph = (
        "[0:a]asplit=2[aenc][awav];"
        "[awav]showwaves=s=640x360:mode=cline:rate=15:colors=0x38bdf8,"
        "drawgrid=w=80:h=45:t=1:c=0x1e3a5f@0.7,"
        "drawbox=y=179:w=640:h=2:c=0x38bdf8@0.30:t=fill,"
        "format=yuv420p[v]"
    )
    return [
        ffmpeg, "-hide_banner", "-y",
        "-f", fmt, "-audio_buffer_size", "80", "-i", source,
        "-filter_complex", graph,
        "-map", "[v]", "-map", "[aenc]",
        "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency",
        "-b:v", "400k", "-maxrate", "600k", "-bufsize", "1M", "-g", "15",
        # 🔑 -ac 1 is lossless when the device presents duplicated mono, which
        # USB adapters routinely do (both channels identical, same instant).
        # CHECK YOURS before assuming: on a genuine stereo source this discards
        # half your recording. 192k because this is the primary voice track and
        # the bitrate is free next to any camera.
        "-c:a", "aac", "-b:a", bitrate, "-ar", "48000", "-ac", "1",
        "-f", "rtsp", "-rtsp_transport", "tcp", url,
    ]


def render(cmd: list[str]) -> str:
    if sys.platform == "win32":
        return subprocess.list2cmdline(cmd)
    return shlex.join(cmd)


def main(args) -> int:
    if args.list:
        return list_devices()

    if not args.device or not args.url:
        print("Need --device and --url (or --list). Try `shopcam encode --list` first.",
              file=sys.stderr)
        return 2

    if not find_ffmpeg():
        print(f"ffmpeg not found. Install: {install_hint()}", file=sys.stderr)
        return 1

    cmd = (audio_command(args.device, args.url)
           if args.audio_only else
           video_command(args.device, args.url,
                         audio_device=args.audio_device,
                         width=args.width, height=args.height, fps=args.fps,
                         bitrate=args.bitrate, encoder=args.encoder,
                         pix_fmt=args.pix_fmt, input_codec=args.input_codec,
                         substream_url=args.substream_url))

    if args.dry_run:
        print(render(cmd))
        return 0

    print(render(cmd), file=sys.stderr)
    print("--- running; Ctrl-C to stop ---", file=sys.stderr)
    try:
        return subprocess.call(cmd)
    except KeyboardInterrupt:
        return 130
