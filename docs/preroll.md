# Pre-roll is a buffer, not a setting

**That Was Awesome** saves the last minute on every camera on *Watch*. The last
minute exists because the recorder holds a rolling pre-trigger buffer in RAM.

The thing everybody gets wrong: **that buffer refills in real time.** It is not a
setting that is either on or off — it is a bucket, and a press empties it.

## The consequence

Press twice in quick succession and the second clip does **not** have a minute in
front of it. It has however many seconds have elapsed since the first press.

Measured on the reference rig, with a 60 s buffer and a 10 s post-roll
("break time"):

```
preroll = clamp(gap - 11s, 0, 60)
```

where `gap` is seconds since the previous press. Full recovery takes **71
seconds**. Leave about a minute between presses if you want a full lead-in.

🔑 The UI reports this rather than hiding it. A press that caught 1.2 s of
lead-in and a press that caught 60 s both put the moment on disk, and reporting
both as an identical green "saved" is true and useless — the difference is
whether you have anything before the moment. It says "saved, but with only 1.2 s
of lead-in", which is the difference between someone who waits a few seconds
before pressing again and someone who finds out in the edit.

## The second press may not make a second clip

Within the post-roll window (default 10 s), the recorder is **still recording**.
A press in that window **extends the clip already being written** rather than
starting a new one.

That is fine, but it must be said out loud: otherwise you go looking for a second
file that does not exist and conclude the button failed.

## Cold start

A camera whose encoder was switched off does not come back with a full buffer.
Measured:

| | |
|---|---|
| encoder process up | ~3 s |
| recorder at full fps | ~20 s |
| **full 60 s of pre-roll** | **~80 s** |

⚠️ **Known limitation:** a camera that has *just* come back is not tracked, so the
Controller assumes it has a full buffer and can over-report `prerollSec`. If you
have just switched an encoder on, wait 80 seconds before trusting the number.
