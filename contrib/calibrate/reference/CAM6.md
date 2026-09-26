# CAM6 — The roamer. No fixed position, no calibration slot.

> **This file is the source of truth for CAM6.** Machine values below are diffed
> against the live camera by `python tools/calibrate.py check`. Prose is here
> because a JSON dump cannot carry which knobs lie.

- **Transport:** Foscam CGI :88
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
| `brightness` | 0 | 100 | 🔴 **assumed** |
| `contrast` | 0 | 100 | 🔴 **assumed** |
| `hue` | 0 | 100 | 🔴 **assumed** |
| `saturation` | 0 | 100 | 🔴 **assumed** |
| `sharpness` | 0 | 100 | 🔴 **assumed** |

## Known-dead or inert knobs

_None known._ Absence of a known-dead knob is not proof one works.

## What is TRUE about this camera — measured, not assumed

- ITS CGI ZEROES A KNOB ON A WRONG PARAMETER NAME AND REPORTS SUCCESS. `setBrightness&value=58` returns result=0 and sets brightness to 0, because the expected `brightness` param was absent and defaulted. That is how this camera ended up parked on contrast 0 while every response said ok. Only a re-read after every write catches it.
- `setContrast` expects the parameter spelled `constrast` -- a TYPO IN THE CAMERA'S OWN FIRMWARE. Spelled correctly it returns result=0 and does nothing.
- WB stays AUTO on this camera.
- Hard-capped at 25 fps; conform its frame rate in the edit.
- Its ROI is stale the moment it moves, which is often. Do not inherit its regions.

## Open / unmeasured

- Its clip ceiling is UNMEASURED.

## Last known good — the diff target

Copied verbatim from `BASELINE-2026-08-07-pm.json`. Re-seed with
`python tools/calibrate.py reseed --camera CAM6` after a deliberate change,
and record why in the history below.

```json
{
 "device": "Foscam R2C (CGI :88)",
 "getImageSetting": {
  "brightness": "50",
  "contrast": "50",
  "hue": "50",
  "saturation": "30",
  "sharpness": "35",
  "denoiseLevel": "50"
 },
 "getInfraLedConfig": {
  "mode": "0"
 },
 "getVideoStreamParam": {
  "resolution0": "7",
  "resolution1": "0",
  "resolution2": "1",
  "resolution3": "7",
  "bitRate0": "1048576",
  "bitRate1": "524288",
  "bitRate2": "262144",
  "bitRate3": "4194304",
  "frameRate0": "25",
  "frameRate1": "15",
  "frameRate2": "15",
  "frameRate3": "25",
  "GOP0": "30",
  "GOP1": "30",
  "GOP2": "30",
  "GOP3": "50",
  "isVBR0": "0",
  "isVBR1": "1",
  "isVBR2": "1",
  "isVBR3": "1"
 }
}
```

## Validation history — APPEND ONLY, never overwrite

The user's standing rule. "Looked at it and kept it" is the evidence that a
value is still trusted, and it is lost if each entry replaces the last.

- **2026-07-26** — grayscale fix confirmed by eye.

