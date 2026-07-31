# Calibration: making nine cameras look like one shop

Cutting between angles that do not match is the fastest way to make a
multi-camera edit look cheap. The goal is not "good colour" — it is **the same
colour**, so a cut does not jump.

Method: pick one camera as the reference, make it as good as it can be, then
normalise every other camera **to it** rather than to an ideal.

## 🔑 A white-balance temperature knob is a TWO-AXIS actuator

The finding that cost the most, and the one most likely to bite you.

UVC/ProcAmp exposes white balance as a single "temperature" slider. It is not one
axis. **It moves R/G and B/G at different rates and in opposite directions.**

So if you build a servo that optimises a single-axis objective — "get R/G to
1.0" — it will happily converge, report success, and **wreck the blue channel on
the way**. It is not broken; it did what you asked. You asked the wrong thing.

**Score both axes, always.** An objective that cannot see B/G cannot be trusted
to report on a knob that moves it.

## 🔑 The camera lies; check the wire

A read-back is a **claim**. Set a value, read it back, and the camera will
cheerfully echo what you wrote whether or not it applied it.

- **Measure from what the recorder actually saved**, not from the camera's API.
- **Perturb massively when probing.** A knob moved by 5% may produce a change
  smaller than sensor noise, and you will conclude it does nothing. Slam it to
  both extremes and see whether the picture moves at all.
- **Every camera is erratic in its own way.** Vendor firmware differs wildly in
  which controls exist, which are honoured, and which silently clamp.

## Order of operations

1. **Fix the room first.** ⚠️ If exposure doubles and a colour cast vanishes with
   settings untouched, **the room changed** — someone opened a door, the sun
   moved, a lamp got bumped. Check that before tracing the pipeline. It will cost
   you an evening otherwise.
2. **Reference camera.** Best exposure, WB and sharpness you can get. Freeze it
   and record the settings.
3. **Normalise the rest to it**, one control plane at a time.
4. **Re-measure from saved clips**, never from live preview.
5. **Save a last-known-good file** so you can get back.

## Know when to stop

At some point the camera side is exhausted and the remaining difference belongs
in the **edit**, as a per-camera gain in your NLE. That is not defeat — it is
cheaper, more precise, and reversible.

Two things worth checking before you give up on the pipeline:

- **Range signalling.** A camera flagged limited-range but producing full-range
  data will look washed out until you tell your NLE to interpret it as full.
- **Veiling flare.** A dome that has not been cleaned lifts blacks across the
  whole frame and no amount of white balance will fix it. **Clean the dome.**
