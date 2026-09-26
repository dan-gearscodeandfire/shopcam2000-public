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
shopcam encode --audio-only --device "Microphone (Your Receiver)" \
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

### 🔴 And make its x264 stream unsliced

The waveform is encoded with libx264 at `-tune zerolatency`, which turns on sliced
threads. **Blue Iris counts slices as frames**: it read the 15 fps microphone
stream at 75 fps, and to keep A/V "in sync" it discarded audio, a **265 ms hole
every 2.3 s**, about 11% of the voice track, cutting through the middle of words.
The bridge itself was clean; only the Blue Iris recording had holes.
`-threads 1 -x264-params sliced-threads=0` fixes it (`shopcam encode --audio-only`
includes both). Fix the slicing, not the audio path.

## 3. 🔑 Know your chain before you judge the level

What "correct" gain looks like depends entirely on the chain, and the reference
rig has had two.

**The old chain** was an analogue lav receiver's **headphone output** into a USB
adapter's **microphone input**, a 30–40 dB mismatch. There, low meters were
correct and turning them up clipped every loud moment. That rule died with that
chain; do not carry it to a different one.

**The current chain** is a 2.4 GHz digital wireless lav whose receiver plugs
straight into USB (it opens natively at 48 kHz). Measured on a labelled take:

| | peak |
|---|---|
| mutter | −23.2 dBFS |
| conversational | −15.4 dBFS |
| full shout | −11.6 dBFS, zero samples at full scale |

- **It is linear**, not compressed: slope 0.966 over 35 dB of input. The top of the
  curve is *clipping* at about −8 dBFS, reached only at a sound level no voice makes
  at lav distance. The narrow mutter-to-shout span is the speaker, not an AGC.
  (An earlier "limiter" reading was taken with the transmitter nowhere near the
  speaker. Measure with the transmitter where it will be worn.)
- **There is no working gain control, and none is needed.** The receiver was
  already at minimum, and the Windows capture-level slider is **inert** on this
  device: the API says OK, the slider moves and reads back −12 dB, and the audio is
  unchanged. A read-back is a claim; only the recording is evidence.
- The only real level control is **physical**: lav placement (twice the distance
  is −6 dB). **Do not turn the transmitter gain up**: it spends the only headroom
  (~3.5 dB at the loudest shout) and buys nothing, because the noise floor is
  already 35 dB down.

## 4. 🔴 A −91 dB floor is a gate, not a clean preamp

If your "silence" reads around **−91 to −95 dBFS**, that is *true digital
silence*. **A real analogue preamp always hisses**, at roughly −60 to −75 dBFS.
Digital silence means something upstream is gating, nearly always in the
receiver.

A gate can cost you in two different ways, so check both:

1. **Does it eat quiet speech?** A gate tuned for "no signal" can clip the start of
   a muttered aside, which is often the best line in the take. On the current
   receiver it does not: a 22 s muttered aside came back 8% silent and fully
   intelligible, with an attack of +25 to +40 dB in 30 ms.
2. **Does it leave room tone?** This one does not: after speech stops it gates to
   true silence (peak −78 dBFS). So the voice track has **no room tone** and will
   cut audibly against camera audio that has some. Plan for it in the edit.

**Check yours deliberately:**

1. Record 30 seconds of a silent room: `ffmpeg -i test.wav -af astats -f null -`,
   and read `RMS level dB`. −60 to −75 dB is a real preamp; below about −85 dB,
   something is gating.
2. Speak a hard consonant after silence and look at the attack. Faster than
   ~30 ms is benign; slower will clip the front of every sentence.

### 🔴 When you change the microphone, name the new device

When the reference rig swapped receivers, the **old USB adapter was still plugged
in** and still enumerated under its old name with nothing connected to it. A
bridge using the old name **bound successfully and recorded nothing**, while every
health check stayed green. Name the device explicitly, and check the recording,
not the status light. (The new receiver is also an **exclusive** device: unlike the
old adapter, it cannot be opened twice, so a second capture of it fails.)

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
