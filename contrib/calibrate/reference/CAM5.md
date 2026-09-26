# CAM5 — Station 1 face angle. With CAM8 this pair carries ~80% of all footage.

> **This file is the source of truth for CAM5.** Machine values below are diffed
> against the live camera by `python tools/calibrate.py check`. Prose is here
> because a JSON dump cannot carry which knobs lie.

- **Transport:** DirectShow ProcAmp (UVC), --device 'OBSBOT Tiny Camera'
- **Locked:** no
- **Clip ceiling:** 🔴 UNMEASURED — go measure it, never assume 255
- **Settle after a write:** 20.0 s

## Exposed parameters — and how each range is KNOWN

`probed` = written until it refused. `device` = the device reported it.
🔴 **`assumed` = NOT VERIFIED. Treat an assumed range as a task, not a fact** —
this fleet has burned sessions on assumed ranges, and `drivers.capabilities()`
still reports every Dahua knob as 0–100, which is false for Gamma.

| knob | lo | hi | how known |
|---|---|---|---|
| `BacklightCompensation` | 0 | 18 | 🔴 **assumed** |
| `Brightness` | 0 | 100 | 🔴 **assumed** |
| `Contrast` | 0 | 100 | 🔴 **assumed** |
| `Gain` | 0 | 100 | 🔴 **assumed** |
| `Hue` | 0 | 100 | 🔴 **assumed** |
| `Saturation` | 0 | 100 | 🔴 **assumed** |
| `Sharpness` | 0 | 100 | 🔴 **assumed** |
| `WhiteBalance` | 2800 | 6500 | device |

## Known-dead or inert knobs

- Gamma -- not exposed on the OBSBOTs.

## What is TRUE about this camera — measured, not assumed

- Already within 1.2% of target on white balance. There is little left to win here on colour.
- CAM8<->CAM5 IS THE MOST FREQUENT CUT IN THE VIDEO. A change to either one is a change to that cut -- look at them together afterwards.
- THE VIEW DOES NOT COME BACK AFTER A PTZ MOVE. Measured with a no-move control in the same regime: control 3.66 mean abs frame difference (the noise floor) vs 8.25 after Pan 12->24->12 with 15 s to settle. The NUMERIC restore was EXACT and the picture still did not return. Confirmed clip-to-clip with three untouched cameras as controls in the same press: CAM5 18.54 raw / 11.61 aligned vs CAM1 5.63, CAM8 3.95, CAM2 7.26.
- BUT LOOK BEFORE ESCALATING: side by side those two frames are the SAME SHOT -- about 10 px of 1080. What a 1% shift actually breaks is pixel-exact ROIs, not the framing. The honest response is 're-author the regions', not 'go re-aim the camera'.
- THERE IS NO PRESET RECOVERY PATH. Blue Iris advertises PTZ on CAM5 (ptz=True, ptzdirect=True) and every command returns HTTP 200 'Ok' while doing nothing -- preset=1, preset=0, pos=4 (home) and a plain pan-left all measured frame deltas of 3.4-3.9, i.e. noise. Blue Iris has no working PTZ channel to a USB camera. Only procamp's DirectShow camctrl moves it.
- The OBSBOT QUANTISES: a request for Pan 24 read back as 23.

## Open / unmeasured

- Its clip ceiling is UNMEASURED.

## Last known good — the diff target

Copied verbatim from `BASELINE-2026-08-07-pm.json`. Re-seed with
`python tools/calibrate.py reseed --camera CAM5` after a deliberate change,
and record why in the history below.

```json
{
 "device": "OBSBOT Tiny Camera (USB/ProcAmp)",
 "procamp": {
  "Brightness": {
   "value": 45,
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
   "value": 12,
   "mode": "manual",
   "range": [
    -130,
    130,
    1
   ]
  },
  "Tilt": {
   "value": -1,
   "mode": "manual",
   "range": [
    -90,
    90,
    2
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
  }
 }
}
```

## Validation history — APPEND ONLY, never overwrite

The user's standing rule. "Looked at it and kept it" is the evidence that a
value is still trusted, and it is lost if each entry replaces the last.

- **2026-08-01 17:52** — held the chart for the 2800–6500 K sweep.
- **2026-08-02 00:2x** — delegated the contrast pick.

