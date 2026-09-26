# CAM2 — ASH21. Second bench angle; the VISUAL reference for colour by user ruling.

> **This file is the source of truth for CAM2.** Machine values below are diffed
> against the live camera by `python tools/calibrate.py check`. Prose is here
> because a JSON dump cannot carry which knobs lie.

- **Transport:** ONVIF Imaging :80
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
| `Brightness` | 0 | 100 | 🔴 **assumed** |
| `ColorSaturation` | 0 | 100 | 🔴 **assumed** |
| `Contrast` | 0 | 100 | 🔴 **assumed** |
| `Sharpness` | 0 | 100 | 🔴 **assumed** |

🛑 **THERE IS NO WHITE BALANCE ON THIS CAMERA, AND THAT IS MEASURED, NOT INFERRED.**
`calibrate/onvif_imaging.py` fully supports ONVIF WB (`set_wb`, `set_wb_full`, CrGain/CbGain,
including the read-modify-write workaround for cameras that ignore partial ImagingSettings).
This device simply does not offer it: `GetImagingSettings` on 2026-08-09 came back with **no
`<tt:WhiteBalance>` element at all**. So a green/magenta cast on CAM2 **cannot be corrected on
the camera** — do not go looking for the knob again. Absence recorded as a value.

## Known-dead or inert knobs

- WhiteBalance -- CAM2 has NO white balance control at all. It appears in the ONVIF capability list and reaches nothing.

## What is TRUE about this camera — measured, not assumed

- USE CAM2 AS THE VISUAL REFERENCE, not CAM1. CAM1 is locked deliberately off-neutral, so matching CAM1 propagates its defect. CAM2 renders the room correctly.
- SATURATION 43 IS A USER TASTE DECISION ON RECORD, taken having seen both. The measured match to CAM1 was 28 (colSat 0.671 vs 0.685); he looked at a stacked A/B and chose 43 (colSat ~0.86). CAM2 therefore ships ~25% RICHER than CAM1 and cuts will show a saturation step. DO NOT SILENTLY FIX THIS BACK.
- Because saturation moves the cast on this camera, quote its cast at sat 43 (R/G ~0.887, -11 pp), never at the sat-28 figure.
- ONE KEY PER CALL. A batched 8-key write applied some keys and IGNORED others, silently.
- Its worst defect is OPTICAL: 3.2x softer than CAM1 on edge strength. Settings cannot fix it. Four levers were tested and ALL were inert -- ChromaSuppress 50->0, noise reduction 2D and 3D, bitrate 4096->8192 with a Blue Iris stream reconnect, and Gamma 1->2 and 1->0.
- It has LIFTED BLACKS -- bottoms at Y=45 where CAM1 reaches 6. A black test with an absolute threshold rejects a perfectly good hold on it.
- Its stream is UNTAGGED, so a decoder guesses limited range and expands it. It actually codes FULL range (40% of its Y samples sit below 16, which a limited-range encoder cannot emit). MUST be interpreted full-range on import or it sits in the timeline looking crushed.

## Open / unmeasured

- Its tonal midpoint sits at 0.47 where CAM1's sits at 0.34 -- a gamma SHAPE difference that brightness and contrast cannot reshape, and it has no gamma control. Unresolved; belongs to the edit.

## Last known good — the diff target

Copied verbatim from `BASELINE-2026-08-07-pm.json`. Re-seed with
`python tools/calibrate.py reseed --camera CAM2` after a deliberate change,
and record why in the history below.

```json
{
 "device": "Amcrest ASH21-B-V2 (ONVIF only)",
 "levels": {
  "Brightness": 36.0,
  "ColorSaturation": 43.0,
  "Contrast": 41.0,
  "Sharpness": 50.0
 }
}
```

## Validation history — APPEND ONLY, never overwrite

The user's standing rule. "Looked at it and kept it" is the evidence that a
value is still trusted, and it is lost if each entry replaces the last.

- **2026-08-01 20:57** — picked tone panel 2.
- **2026-08-01 21:08** — picked saturation option 3 (43, over the measured 28). Taste decision on record.
- **2026-08-09 02:56 — 🟢 CAM2 IS ~15% GREEN, AND IT IS AN EDIT-SIDE FIX.** Measured against
  the SpyderCheckr 24 at the subject position, **the same card in the same light in the same
  minute as CAM1's target read**, `chart_rectify --optimise --radius 45`:
  **R/G 0.8528 (−14.7%) · B/G 0.8495 (−15.1%) · grey luma 140.4 · patch_sd 1.19.**
  Green sits above BOTH red and blue — the direction the eye hunts and the one the user's own
  rule calls objectionable. It is visible in the rectified strip, not just in the numbers.
  ⚠️ Against CAM1's target (1.0116 / 1.0000) that is a **15.7 pp R/G and 15.1 pp B/G gap** —
  roughly 10x this method's precision, so it is real. Cuts between CAM1 and CAM2 will jar.
  🔧 **THE CORRECTION IS IN THE NLE, NOT ON THE RIG** (see the no-WB note above):
  **multiply R by ~1.17 and B by ~1.18 against green.** It is constant, so one correction
  applies to every CAM2 shot.
  ⚙️ Method note: the first `--optimise` run warned *"a corner hit the radius limit — the
  result may not be a true minimum"*. Re-run with `--radius 45` and cleaner corners returned
  **the same R/G to 4 decimal places**, so the warning was honest but the number held.

