# The Blue Iris JSON API, as the Controller uses it

*Verified against Blue Iris 6.0.8.4 and the manual shipped with it. The client is
[`src/shopcam2000/blueiris.py`](../src/shopcam2000/blueiris.py); this page is the
findings that shaped it, including the ones the manual does not spell out.*

Everything is `POST http://<blue-iris-host>:<port>/json` with a JSON body (port 81
by default). The Controller uses nothing else: no MQTT, no `/admin?` URLs.

---

## Login is a two-step challenge

```text
→ {"cmd": "login"}
← {"result": "fail", "session": "<nonce>"}            the "fail" is expected
→ {"cmd": "login", "session": "<nonce>",
   "response": md5("<user>:<nonce>:<password>")}
← {"result": "success", "session": "<nonce>",
   "data": {"version": "...", "clipcreate": true, ...}}
```

- The nonce becomes the session for every later command.
- The user needs Blue Iris's **clip creation** permission. Without it, starting and
  stopping manual recording fails; the client warns when `clipcreate` is false.
- **Sessions expire quickly and without warning.** Any non-`success` result is
  treated as an expired session: log in once more and retry, all under one lock so
  concurrent callers do not race the re-login. A second failure is real.

## The commands

| Purpose | Body (plus `"session"`) | Notes |
|---|---|---|
| list cameras | `{"cmd":"camlist"}` | Groups arrive in the same array; they start with `+` in `optionDisplay` (`+All cameras`) and are dropped, because arming a group would arm its members. Fields used: `optionValue`, `isEnabled`, `isOnline`, `isNoSignal`, `isPaused`, `isManRec`, `ManRecElapsed`, `ManRecLimit`, `FPS`, `audio`. |
| status | `{"cmd":"status"}` | `uptime` as `d:hh:mm:ss`. A Blue Iris restart empties every pre-roll buffer, so the Controller caps its predicted lead-in at this. |
| manual take | `{"cmd":"camconfig","camera":"CAM1","manrec":true}` | 🔴 `manrec` must be a **top-level** parameter. Nested inside a `data` object it is accepted and does nothing. |
| That Was Awesome | `{"cmd":"trigger","camera":"CAM1"}` | Cancel early with `"trigger":0`, also top-level. |
| find clips | `{"cmd":"cliplist","camera":"CAM1","startdate":<UTC epoch s>}` | Fields used: `file`, `date` (whole seconds only), `msec`, `filesize`, `filetype`. |
| snapshot | `GET /image/<cam>?session=…&q=50&s=40` | `s=40` cuts a 1080p frame from ~118 KB to ~34 KB. |
| a clip | `GET /clips/<file>` | Real `video/mp4`, honours Range requests. 🔴 `/file/clips/<file>` returns a **JPEG thumbnail** with an `.mp4` name. |

## What `trigger` does

On a camera recording "when triggered" with a pre-trigger buffer
([blue-iris-settings.md](blue-iris-settings.md)), `trigger` flushes the buffered
pre-roll plus the break time into one clip. Re-triggering while the clip is still
being written **extends** it rather than starting a new one (a 4.4 s gap between
presses merged; a 12.4 s gap made two clips). `trigger` is **not idempotent**,
which is why the bridge only retries when nothing was triggered.

## 🔴 Success is not evidence

Several calls return `success` and do nothing. Each one below cost a debugging
session:

- **`trigger` on a camera not configured for triggered recording:** accepted,
  records nothing.
- **`trigger` on a dead camera:** accepted. An offline camera "triggered" and
  wrote zero clips. The Controller checks each camera against its poller's last
  `camlist` before triggering, and reports it as failed instead.
- **A dead RTSP source reads as online for ~20 s.** For bridged cameras, the bridge
  supervisor's state overrides Blue Iris's.
- **`camconfig` with a parameter Blue Iris does not apply live** (`ptz=`,
  `enabled=`): `success`, no change. Read it back.
- **A clip reported by `cliplist` is not a playable clip.** A fresh clip reports
  junk (`1 sec`, 48 bytes) until it settles, and 1 file in 99 was written with no
  H.264 start code inside a press that returned 200.

## How a press is handled

1. Presses are **serialised** under one lock (three simultaneous presses once
   produced three receipts all claiming 60 s for one merged clip).
2. Each camera on Watch is checked against the poller; dead ones are reported,
   not triggered.
3. The live ones are triggered in turn. If Blue Iris itself is unreachable, the
   rest are skipped.
4. The Controller records when the buffer starts refilling (for the next press's
   predicted lead-in, [preroll.md](preroll.md)), publishes the receipt to the UI,
   and answers the bridge: **200** if at least one camera triggered, **409** if no
   camera is on Watch, **502** if nothing triggered.
5. **Verification runs in the background.** After 16 s (the 10 s break time plus
   settle), it asks `cliplist` for clips from two minutes before the trigger, takes
   the newest one dated at or before the trigger (+2 s), and runs
   `ffprobe -show_entries format=duration` on it with a 20 s timeout. Any stderr,
   a non-zero exit or a duration at or below 0.05 s fails the clip.
6. Verified clips are copied into a sorted folder and the `on_twab_filed` hook runs
   ([contrib/hooks/](../contrib/hooks/)).

Manual takes are collected the same way, except the clip list is polled (14 × 0.6 s)
until each clip's file, length and size stop changing.

Measured press-to-verdict: **765 ms** with one camera on Watch, **1,423 ms** with
three, **1,164 ms** with ten. It does not scale with the camera count; the variance
is Blue Iris's response time.

## Polling

The Controller polls `camlist` every **1 s while recording**, **4 s idle**, and
backs off to **20 s** while Blue Iris is unreachable (all in `config.example.toml`).
