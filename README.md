# Shopcam 2000

**A multi-camera workshop rig you drive from your phone — and a button that saves
the moment you forgot to press record on.**

Built for [Gears, Code, and Fire](https://www.youtube.com/@gearscodeandfire).
This is the real thing, extracted: nine cameras and a lav mic in a workshop,
running every day.

```
pip install shopcam2000
shopcam init          # a commented config.toml in the current directory
shopcam doctor        # checks the things that fail silently.  START HERE.
shopcam serve         # the controller, on http://<this machine>:8787
```

---

## The idea

Recording every angle all the time is easy and useless — you get terabytes and
find nothing. Recording on purpose means pressing a button *before* the
interesting thing happens, which is precisely when you are holding something hot
and thinking about something else.

So there are two buttons:

- **RECORD** — a decision you make in advance. Arms a set of cameras, rolls them
  together, stops them together.
- **THAT WAS AWESOME** — the one that matters. Every camera on *Watch* keeps a
  rolling pre-trigger buffer in the recorder. This flushes the **last minute** to
  disk on all of them at once. It is the only control here that can rescue
  something that has **already happened**.

That asymmetry runs through the whole design. RECORD has a hold-to-stop guard,
because ending a take by brushing your phone costs a shot. TWAB has no guard at
all, because a stray press costs one clip and a press one second too late costs
the moment.

## What you get

- **A web UI that is actually usable on a phone in a workshop.** One scale model
  drives the whole interface, so it fits a 1080p monitor, a 796×360 phone in
  landscape and any browser window you drag, without a fixed layout for each.
  Installable as a PWA. On a 796×360 screen all ten cameras fit with no
  scrolling.
- **Server-side state.** Arming lives on the server, not in a browser tab, so
  every screen in the building agrees and a restart does not silently disarm you.
- **Clip verification.** After a save, it goes back and *decodes what it wrote*.
  On the reference rig, 1 file in 99 was 5.56 MB with no H.264 start code inside
  a press that returned success with no failures. Every layer trusted the one
  below it and none of them opened the file.
- **Auto-sorting.** Every press copies its clips into one folder named for when
  it happened, so nine angles of a moment are nine files in a folder instead of
  nine files among hundreds.
- **`shopcam encode`** — turn a USB webcam, capture card or desktop into an RTSP
  camera your recorder will accept.
- **`shopcam doctor`** — the check that a status field is not lying to you.

## 🔴 What you need first, and the honest caveat

**This currently requires [Blue Iris](https://blueirissoftware.com/) — paid,
Windows-only.** It is the recording backend for everything here.

That is a real limit and it is not hidden in the small print. A `Recorder`
interface that would allow a pure-ffmpeg backend is the top of the roadmap, and
the hard part is already specified — the pre-roll behaviour, the break-time
window and the sync characteristics are all *measured* and written down in
[`docs/`](docs/). Until then: if you do not have Blue Iris, the documentation is
still worth your time and the code is not yet.

You also need **ffmpeg** on PATH for `shopcam encode` and for clip verification.
It is not a pip dependency — it cannot be — so `shopcam doctor` finds it and
tells you how to install it.

## First run

```
pip install .
mkdir my-rig && cd my-rig
shopcam init      # asks where Blue Iris is, tests the login, lists your cameras
shopcam doctor    # checks the things that fail silently
shopcam serve     # the Controller, on your LAN
```

`shopcam init` will not write a config it could not verify without telling you.
It asks for the Blue Iris host, port, user and password, logs in, prints every
camera it found, and lets you hide any and mark any as a microphone. Cameras are
not configured here — add them in Blue Iris and they appear. Change your mind
later in `config.toml`, which stays fully commented.

## The physical button (firmware)

[`firmware/`](firmware/) holds four ESP-IDF projects: the battery **button**
(Seeed XIAO ESP32S3), the mains **master** (ESP-NOW receiver, no WiFi), the
mains **bridge** (WiFi + OTA, posts to the Controller), and the original
one-chip button. Read
[firmware/README.md → *Where your own values go*](firmware/README.md) first: it
names the three files that take your key, your MAC and your WiFi, and nothing
else needs editing.

## USB cameras, and cameras Blue Iris cannot see

[`contrib/windows/bridge/`](contrib/windows/bridge/) has the exact ffmpeg
scripts the reference rig runs to turn USB cameras, a microphone and the desktop
into RTSP cameras, the supervisor that keeps them alive, and two PowerShell
scripts that **add** such a camera to Blue Iris or **convert** an existing USB
camera into a bridged one. [`contrib/hooks/`](contrib/hooks/) is what runs after
a press: colour re-tagging and transcription.

## The documentation is the point

Most of this code is ordinary. What is not ordinary is that the awkward parts
were *measured* rather than guessed, usually after losing something.

| Doc | The finding |
|---|---|
| [usb-to-h264.md](docs/usb-to-h264.md) | `-pix_fmt` is load-bearing, and an ffmpeg-to-ffmpeg test **cannot see** the bug it causes |
| [audio.md](docs/audio.md) | A mic can be its own camera. Low gain is *correct*. A −91 dB floor is a squelch, not a clean preamp |
| [preroll.md](docs/preroll.md) | Pre-roll is a buffer that refills in real time, not a setting |
| [sync.md](docs/sync.md) | Independent sources land ~0.44 s apart, consistently. That is a constraint, not a bug |
| [calibration.md](docs/calibration.md) | A white-balance temperature knob is a **two-axis** actuator |
| [https.md](docs/https.md) | Why the browser refuses to install the app, and the two-minute fix |

## Standing rules this project will not break

Learned expensively, stated so nobody has to relearn them:

1. **The record path optimises for start/stop latency and nothing else.** No
   slates, no tones, no countdowns, no confirmation step. Sync is a post
   problem; a missed moment is not recoverable.
2. **The controller declares intent; a supervisor owns processes.** Never kill an
   encoder directly — the supervisor will restart it and log a fault, correctly.
3. **The camera lies; check the wire.** A status field is a claim. A frame pulled
   off the wire is evidence. A recorder will report a dead camera as online for
   ~20 seconds after its source dies.
4. **On is implied; off is only ever explicit.** Arming a camera wakes its
   encoder. Nothing ever switches one off except you.

## Licence

MIT. Cinzel is SIL OFL and ships with its licence. No binaries are vendored.
