# CAM1 - Hero angle, station 2. The exposure/framing anchor for the fleet.

> **This file is the source of truth for CAM1.** Machine values below are diffed
> against the live camera by `python tools/calibrate.py check`. Prose is here
> because a JSON dump cannot carry which knobs lie.

- **Transport:** DirectShow ProcAmp (UVC) via calibrate/remote/procamp.py, --device 'OBSBOT Tiny 4K Camera'
- **Locked:** 🔴 YES - colour and exposure must not be written
- **Clip ceiling:** 🔴 UNMEASURED - go measure it, never assume 255
- **Settle after a write:** 20.0 s

## Exposed parameters - and how each range is KNOWN

`probed` = written until it refused. `device` = the device reported it.
🔴 **`assumed` = NOT VERIFIED. Treat an assumed range as a task, not a fact** -
this fleet has burned sessions on assumed ranges, and `drivers.capabilities()`
still reports every Dahua knob as 0–100, which is false for Gamma.

| knob | lo | hi | how known |
|---|---|---|---|
| `BacklightCompensation` | 0 | 18 | probed |
| `Brightness` | 0 | 100 | 🔴 **assumed** |
| `Contrast` | 0 | 100 | 🔴 **assumed** |
| `Gain` | 0 | 100 | 🔴 **assumed** |
| `Hue` | 0 | 100 | 🔴 **assumed** |
| `Saturation` | 0 | 100 | 🔴 **assumed** |
| `Sharpness` | 0 | 100 | 🔴 **assumed** |
| `WhiteBalance` | 2800 | 6500 | device |

## Known-dead or inert knobs

- Gamma -- NOT EXPOSED. Probed: read_all silently swallows properties the driver rejects, so absence is a real capability answer, not a reporting gap. Do not re-open.

## What is TRUE about this camera - measured, not assumed

- LOCKED BY USER DECISION for colour and exposure. Writing to it invalidates every other camera's target. Framing (pan/tilt/zoom) is NOT locked -- the user moves it freely and a gimbal move is not a regression.
- CANNOT REACH NEUTRAL **IN THE LIGHT THAT WAS MEASURED**, and the physics is closed: a CCT knob rides the blackbody locus and cannot correct an error perpendicular to it, which is what a green-deficient light produces. R/G = 1.00 needs ~3900 K, where B/G = 1.28; the locus never passes near (1,1).
  🔄 **2026-08-09 - RE-MEASURED AT THE SUBJECT POSITION AND THE PREMISE NO LONGER HOLDS.** With the card hung at head height, square to the camera, under the final filming light, CAM1 reads **R/G 1.0116 · B/G 1.0000** on the SpyderCheckr greys. That is neutral. **The light at the FACE is not green-deficient**; the light at the earlier sample point was. So this entry was never wrong about the physics - it was measuring a different place in the room. 🔑 **The claim "CAM1 cannot be neutral" is conditional on WHERE you sample. At the face, in this lighting, it already is.** Same shape as CAM4's gamma ruling: valid for its conditions, misleading outside them.
- Hue is a dead end too: the whole 31->71 range moves R/G by 3% and does it NON-MONOTONICALLY.
- BacklightCompensation range is 0-18, NOT 0-100. Value 9 is its default and is mid-scale. Same trap family as CAM7's Gamma 0-15.
- Focus is on a PLATEAU: laplacian variance is flat across focus 0-8, then falls monotonically. Current Focus 2 is inside the plateau and its 0.4% deficit vs nominal-best 3 is far under the pass-to-pass spread. DO NOT RE-SWEEP.
- OBSBOT FOV / zoom / gimbal are NOT scriptable -- the vendor channel is compiled protobuf over ZMQ and its OSC channel is set-only with no read-back. Chasing it burned a session. Print the manual console step instead (widest FOV 86 deg, zoom 1.0x, gimbal off).
- It LATCHED about 7 pp once and it was not reproducible. No setting prevents it. Take a grey reference at the HEAD *and* the TAIL of every CAM1 session -- that is the only thing that detects it.
- A framing move VOIDS its ROIs even though the shot looks identical. Re-author the regions; do not go re-aim the camera.

## Open / unmeasured

- ~~Its residual red is owed to the edit as one tint pass~~ - **see the re-measurement above:
  at the subject position under the final filming light the residual is too small to need a pass
  (R/G +1.2%, B/G 0.0%). The tint pass is NOT owed unless a shot samples light from the old
  position.** Left here rather than deleted so the earlier reasoning stays findable.
- 🔍 **HYPOTHESIS, UNTESTED (user, 2026-08-09): "CAM1 will be shot at varying zoom, but the
  output tonality should be similar independent of zoom."** Recorded so it can be proven
  wrong rather than assumed.
  **Why it should hold:** Exposure and WhiteBalance are both `manual` here, and the OBSBOT's
  zoom is DIGITAL - a crop-and-scale of the same sensor readout, not a varifocal lens. No
  light path changes and nothing auto re-meters, so grey ratios and grey luma should be
  invariant.
  **The three ways it could fail, in likelihood order:**
  1. 🔴 **Anything auto re-engaging** - the OBSBOT's own AI tracking / auto-framing modes are
     the main risk, since they can take exposure back. If tonality moves with zoom, suspect an
     auto mode before suspecting optics.
  2. **Digital zoom magnifies noise and destroys acuity** - that is NOT tonality and will not
     show in R/G or luma, but it is real and it will show on screen. Do not read "tonality is
     stable" as "the zoomed shot is as good".
  3. Sensor-crop binning changes on some devices at particular zoom steps, altering noise and
     occasionally black level.
  **The test, when he next changes zoom:** card at the face, unmoved; measure at the two
  extreme zooms in one sitting; compare R/G, B/G and grey luma. **Prediction: all three move
  less than the ~0.4 pp method precision. If grey luma moves more than ~2%, the hypothesis is
  wrong and an auto mode is the first suspect.** ⚠️ A zoom change VOIDS the ROIs regardless.

## Last known good - the diff target

Copied verbatim from `BASELINE-2026-08-07-pm.json`. Re-seed with
`python tools/calibrate.py reseed --camera CAM1` after a deliberate change,
and record why in the history below.

```json
{
 "device": "OBSBOT Tiny 4K Camera (USB/ProcAmp)",
 "procamp": {
  "Brightness": {
   "value": 50,
   "mode": "manual",
   "range": [
    0,
    100,
    1
   ]
  },
  "Contrast": {
   "value": 60,
   "mode": "manual",
   "range": [
    0,
    100,
    1
   ]
  },
  "Hue": {
   "value": 51,
   "mode": "manual",
   "range": [
    0,
    100,
    1
   ]
  },
  "Saturation": {
   "value": 53,
   "mode": "manual",
   "range": [
    0,
    100,
    1
   ]
  },
  "Sharpness": {
   "value": 45,
   "mode": "manual",
   "range": [
    0,
    100,
    1
   ]
  },
  "WhiteBalance": {
   "value": 4800,
   "mode": "manual",
   "range": [
    2800,
    6500,
    100
   ]
  },
  "BacklightCompensation": {
   "value": 9,
   "mode": "manual",
   "range": [
    0,
    18,
    1
   ]
  },
  "Gain": {
   "value": 1,
   "mode": "manual",
   "range": [
    1,
    48,
    1
   ]
  }
 },
 "camctrl": {
  "Pan": {
   "value": -1,
   "mode": "manual",
   "range": [
    -130,
    130,
    1
   ]
  },
  "Tilt": {
   "value": 14,
   "mode": "manual",
   "range": [
    -90,
    90,
    1
   ]
  },
  "Zoom": {
   "value": 0,
   "mode": "manual",
   "range": [
    0,
    100,
    1
   ]
  },
  "Exposure": {
   "value": -6,
   "mode": "manual",
   "range": [
    -13,
    -2,
    1
   ]
  },
  "Iris": {
   "value": 0,
   "mode": "manual",
   "range": [
    0,
    0,
    0
   ]
  },
  "Focus": {
   "value": 2,
   "mode": "manual",
   "range": [
    0,
    100,
    1
   ]
  }
 }
}
```

## Validation history - APPEND ONLY, never overwrite

The user's standing rule. "Looked at it and kept it" is the evidence that a
value is still trusted, and it is lost if each entry replaces the last.

- **2026-08-01 20:28** - picked contrast panel 4 from a 6-up sheet. Rest 07-24→07-26.
- **2026-08-09 02:55 - 🎯 THE FLEET COLOUR TARGET, MEASURED. CHANGED NOTHING.** First reading
  against a SpyderCheckr 24 hung **at the subject position (head height, square to camera,
  under the key light)**, lights in their final filming state, `tools/chart_rectify.py
  --optimise`: **R/G 1.0116 (+1.2%) · B/G 1.0000 (+0.0%) · grey luma 129.9 · glare 0.0115 ·
  patch_sd 0.85 · colour_sat 0.6033.** 🔑 **CAM1 IS NEUTRAL. The lock stands - there is
  nothing to correct.** Every other camera is matched to THESE numbers.
  🔴 **A near-miss to remember:** twenty minutes earlier the same camera, unchanged, read
  **R/G +6.0% / B/G +9.3%** with the card propped on a dark monitor four feet away, and I was
  one step from proposing the lock be broken to "fix" it. The camera never moved; the card
  did. See vault `the-card-goes-where-the-face-goes`. **Re-measure with the card at the face
  before ever believing CAM1 has drifted.**
- **2026-08-09 03:05 - PTZ reseeded to the HERO FRAMING, on the user's word: "CAM1 is
  absolutely where it needs to be right now for the CAM1 hero shot."** Pan −92 → −1,
  Tilt 20 → 14, Zoom unchanged at 0. He re-aimed it by hand to face the colour card, then
  confirmed that aim is the shot. **Colour and exposure were NOT touched and did not move** -
  `check` had reported exactly 3 differences, all camctrl, before the reseed and IDENTICAL
  (71/71) after. Reseed reported 71 keys → 71, round-trip verified clean, and the file diff
  was 2 lines.
  ⚠️ **This pins the framing, so a later accidental knock now shows up as drift** - which is
  the point. It also **voids CAM1's ROIs**; they are authored against the old aim and need
  re-authoring before any ROI-based scoring.
  🔴 The 7 pp latch on this camera is still unexplained and undetectable by settings: **take a
  grey reference at the HEAD and the TAIL of every CAM1 session.** Today's is the head
  reading; no tail reading was taken.

