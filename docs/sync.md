# Sync: what you will actually get

**Independent sources will not be frame-synchronised. Plan for it.**

Measured on the reference rig across one button press, three sources:

```
CAM5  ──┐
MIC1  ──┼── spread: 0.44 s
CAM4  ──┘
```

Consistent, repeatable, and the control case (the same source against itself)
agreed to **0.00 s** - so the spread is real, not measurement noise.

## Why

Every source has its own clock, its own encoder latency, its own network path and
its own buffer. Nothing in the chain is genlocked. One button press is one
*intent*; it is not one timeline.

## 🔴 This is a constraint, not a bug

Do not open a task to fix it. Specifically, **do not** add per-clip offset
metadata as a "helpful" improvement - the offsets are not stable enough to trust
and a wrong offset is worse than no offset, because you will believe it.

The governing assumption to work under:

> Assume audio will **not** be in sync unless minimally-compressed audio and
> video travel together (e.g. MJPEG).

## What to do instead

**Sync by waveform in your editor.** Every modern NLE will align clips by audio
automatically, and it is reliable in a way that timestamp arithmetic is not.

🔑 This is the reason to keep **scratch audio on every camera** even when a
dedicated microphone is recording the voice track. The camera audio is not
for listening to - it is the sync key. Strip a camera's audio to save bitrate and
you have thrown away the thing that aligns it.

## ⚠️ Do not build a slate for this

The reference rig measured the 0.44 s spread and built a full slate system -
tone, visual mark, the lot - before its owner pointed out he had been syncing by
waveform for four years and considered it a non-problem.

**Measure, write it down, then ask whether the person who lives with it thinks it
is a problem.** "Measurable" is not the same as "needs fixing", and the record
path is the last place to add a step: it optimises for start and stop latency and
nothing else.
