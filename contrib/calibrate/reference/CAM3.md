# CAM3 - Macro / insert camera.

> **This file is the source of truth for CAM3.** Machine values below are diffed
> against the live camera by `python tools/calibrate.py check`. Prose is here
> because a JSON dump cannot carry which knobs lie.

- **Transport:** Sofia dvrip :34567 -- writes MUST go to SECTION 0 of AVEnc.VideoColor[0]
- **Locked:** no
- **Clip ceiling:** 🔴 UNMEASURED - go measure it, never assume 255
- **Settle after a write:** 20.0 s

## Exposed parameters - and how each range is KNOWN

`probed` = written until it refused. `device` = the device reported it.
🔴 **`assumed` = NOT VERIFIED. Treat an assumed range as a task, not a fact** -
this fleet has burned sessions on assumed ranges, and `drivers.capabilities()`
still reports every Dahua knob as 0–100, which is false for Gamma.

| knob | lo | hi | how known |
|---|---|---|---|
| `Brightness` | 0 | 100 | 🔴 **assumed** |
| `Contrast` | 0 | 100 | 🔴 **assumed** |
| `Hue` | 0 | 100 | 🔴 **assumed** |
| `Saturation` | 0 | 100 | 🔴 **assumed** |

## Known-dead or inert knobs

- Whitebalance -- PROVEN DEAD. It reads back perfectly and reaches nothing: a 136-unit swing moved the picture 0.02%. Also null over ONVIF (mode AND both gains). This was discovered by servoing into it and believing the result.

## What is TRUE about this camera - measured, not assumed

- SECTION 0, NOT SECTION 1. Section 1 is not live; writes there read back fine and change nothing. Sections were synced 2026-08-01 so they match.
- Saturation 30 was picked by the user from a rendered side-by-side.
- This camera has NEVER had a per-camera deep pass. Its numbers are the least-earned in the fleet.

## Open / unmeasured

- A full per-camera pass is owed.
- Its clip ceiling is UNMEASURED.

## Last known good - the diff target

Copied verbatim from `BASELINE-2026-08-07-pm.json`. Re-seed with
`python tools/calibrate.py reseed --camera CAM3` after a deliberate change,
and record why in the history below.

```json
{
 "device": "XiongMai / Sofia dvrip :34567",
 "LIVE_section0": {
  "Acutance": 3848,
  "Brightness": 30,
  "Contrast": 53,
  "Gain": 0,
  "Hue": 50,
  "Saturation": 30,
  "Whitebalance": 128
 },
 "inactive_section1": {
  "Acutance": 3848,
  "Brightness": 30,
  "Contrast": 53,
  "Gain": 0,
  "Hue": 50,
  "Saturation": 30,
  "Whitebalance": 128
 },
 "camera_param": {
  "WhiteBalance": "0x00000000",
  "DayNightColor": "0x00000001",
  "GainParam": {
   "AutoGain": 0,
   "Gain": 0
  },
  "ExposureParam": {
   "LeastTime": "0x00000100",
   "Level": 3,
   "MostTime": "0x00010000"
  }
 }
}
```

## Validation history - APPEND ONLY, never overwrite

The user's standing rule. "Looked at it and kept it" is the evidence that a
value is still trusted, and it is lost if each entry replaces the last.

- **2026-07-26** - picked Sat 30 from a rendered side-by-side. Section 1 synced to match 2026-08-01 23:35.

