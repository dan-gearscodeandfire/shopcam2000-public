# Audio: calibrating the thing nobody calibrates

Most multi-camera guides treat sound as a property of a camera. That is the
mistake. If your voice lives inside CAM5's audio track, then your voice exists
only when CAM5 is running, and it changes character every time you cut.

**Your voice is not an angle. Give it its own track.**

---

## 1. Make the microphone its own camera

A recorder that thinks in cameras will happily accept a "camera" that is really a
microphone. One ffmpeg process, one RTSP URL, one entry in the recorder:

```
shopcam encode --audio-only --device "Microphone (USB Audio Device)" \
               --url rtsp://127.0.0.1:8554/mic1 --dry-run
```

Now the voice track is independent of which angle is rolling, it gets the same
pre-roll as everything else, and That-Was-Awesome saves it alongside the picture.

## 2. 🔑 A microphone needs a picture

Health checks ask ffprobe for a **video** stream (`-select_streams v:0`). An
audio-only source has none, so a supervisor decides the source is dead and
restarts it every cycle, for ever.

Give it a synthetic video track and every existing tool works **unmodified**.
That is the whole trick, and it is worth more than it sounds: you do not have to
special-case the microphone anywhere else in your stack.

### ⚠️ Make it a waveform, not a black rectangle

The obvious synthetic track is a black frame. Do not.

`showwaves` **on true silence draws nothing at all.** The first frame pulled off
the reference rig's mic bridge was pure black — which is *exactly what a dead
camera looks like* in the recorder's grid. You have built an indicator that
cannot distinguish "quiet" from "broken".

So draw a **grid and a centre line** underneath the trace. Now:

| | |
|---|---|
| live and loud | a moving trace |
| **live but quiet** | **a flat line on a lit tile** |
| dead | black |

It costs nothing, it is readable from across the room, and it is usable B-roll.

## 3. 🔑 Low gain is correct. Do not "fix" it.

The single most common mistake: the meters look low, so you turn it up.

Measured on the reference rig's lav:

| | |
|---|---|
| moderate speech | **−18 to −25 dBFS** |
| a shout | **−4 dBFS** |

That is not quiet. That is **correct**, with proper headroom. Add the +10 dB that
would make the meters look "healthy" and every loud moment clips —
**unrecoverably**, and the loud moments are the ones you were recording for.

There is usually a structural reason the level looks low, and it is not a fault:
a wireless receiver's **headphone output** into a **microphone input** is a
30–40 dB mismatch. That is *why* the gain must stay low.

## 4. 🔴 A −91 dB noise floor is a squelch, not a clean preamp

This one looks like good news and is not.

If your "silence" reads around **−91 to −95 dBFS**, that is *true digital
silence* — mathematically zero. **A real analogue preamp always hisses**, at
roughly −60 to −75 dBFS. Digital silence means something upstream is gating: a
squelch or noise-reduction circuit in the receiver, deciding nothing is
happening and outputting nothing at all.

Why it matters: a gate tuned for "no signal" will also eat the **start of quiet
delivery** — the muttered aside, the thing said under your breath while
concentrating, which is often the best line in the take.

**Check for it deliberately:**

1. Record 30 seconds of a silent room. Measure the floor.
   `ffmpeg -i test.wav -af astats -f null -` and read `RMS level dB`.
2. −60 to −75 dB → a real preamp. Fine.
3. Below about −85 dB → something is gating. Find it and turn it off. It is
   nearly always in the receiver, not the computer.

Then verify the gate is not eating consonants: speak a hard consonant after
silence and check the attack. On the reference rig the gate measured **10–30 ms**
— faster than a consonant, so benign. A slower one will clip the front off every
sentence and you will blame your delivery.

## 5. Mono, but check first

USB audio adapters routinely present **stereo where both channels are
identical** — duplicated mono. If so, `-ac 1` is lossless, not a 6 dB giveaway,
and halves your bitrate for nothing.

**Verify before assuming.** On a genuinely stereo source, `-ac 1` discards half
your recording:

```
ffmpeg -i test.wav -af "channelsplit,astats" -f null -
```

Identical RMS on both channels at the same instant means duplicated mono.

Use a higher bitrate than the cameras get — 192k against their 128k. This is the
primary voice track and the bitrate is free next to any picture.

## 6. Do not expect it to be in sync

It will not be. See [sync.md](sync.md). Independent sources land up to ~0.44 s
apart, consistently, and that is a constraint to work with rather than a bug to
chase. Sync by waveform in your editor — which is another reason to keep scratch
audio on the cameras as well, even though the lav is the real track.
