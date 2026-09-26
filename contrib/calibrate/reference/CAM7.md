# CAM7 — Hero cam.

> **This file is the source of truth for CAM7.** Machine values below are diffed
> against the live camera by `python tools/calibrate.py check`. Prose is here
> because a JSON dump cannot carry which knobs lie.

- **Transport:** Dahua CGI :80 -- ACTIVE PROFILE (runs profile 2); settings applied to all 3 profiles
- **Locked:** no
- **Clip ceiling:** **224** — a patch reaching this is VOID
- **Settle after a write:** 12.0 s

## Exposed parameters — and how each range is KNOWN

`probed` = written until it refused. `device` = the device reported it.
🔴 **`assumed` = NOT VERIFIED. Treat an assumed range as a task, not a fact** —
this fleet has burned sessions on assumed ranges, and `drivers.capabilities()`
still reports every Dahua knob as 0–100, which is false for Gamma.

| knob | lo | hi | how known |
|---|---|---|---|
| `Brightness` | 0 | 100 | 🔴 **assumed** |
| `Compensation` | 0 | 100 | 🔴 **assumed** |
| `Contrast` | 0 | 100 | 🔴 **assumed** |
| `GainBlue` | 0 | 100 | 🔴 **assumed** |
| `GainRed` | 0 | 49 | probed |
| `Gamma` | 0 | 15 | probed |
| `Hue` | 0 | 100 | 🔴 **assumed** |
| `Saturation` | 0 | 100 | 🔴 **assumed** |

## Known-dead or inert knobs

_None known._ Absence of a known-dead knob is not proof one works.

## What is TRUE about this camera — measured, not assumed

- CLIP CEILING 224, NOT 255. A patch that reaches it reads sd 0.00 and R/G = B/G = 1.0000 -- a flawless FAKE neutral white balance. Never score colour on a pegged patch.
- GAMMA RANGE IS 0-15, NOT 0-100. Probed by writing and reading back: 0-15 accepted, 20/30/50/100 all rejected with HTTP 400.
- 'DARK *AND* BLOWN OUT AT THE SAME TIME' IS NEVER AN EXPOSURE OFFSET -- IT IS THE TONE CURVE. An offset slides the whole picture together; it cannot darken midtones while clipping highlights. Two hours were spent agonising between Compensation 40 and 65, a fight that could not be won because the wrong knob was being turned. Gamma 0->15 fixed the 'too dark' half outright: crush 22.6% -> 6.0%, face 128 -> 157.
- GAMMA WORKED HERE BECAUSE 22.5% OF THE FRAME WAS SOLID BLACK. On CAM4, at 0.04% crush, the identical change did nothing. Check crush first.
- A VALUE REJECTED UNDER A BROKEN CURVE MUST BE RE-TESTED AFTER FIXING IT. Compensation 40 crushed 64% of the frame at Gamma 5 and 12.9% at Gamma 15 -- the setting the user called 'way too dark' is five times brighter in the shadows on the corrected curve.
- NEVER PUSH GainRed PAST 49. It saturates around 45-49; past that it is noise for nothing. The residual -2.5% is a firmware ceiling and goes to the edit.
- IT IS OPTICALLY SOFT -- the same model that was retired from the CAM2 position. Its saturation collapse is VEILING FLARE. Clean the dome or replace it; do not spend a session on its settings.
- SmartEncode emits a keyframe every 6.66 s, so its TWAB pre-roll returns 53.5 s rather than 59.5 s -- pre-roll can only start on a keyframe.
- Its stream is UNTAGGED: 65% of its Y samples sit below 16, so it codes FULL range and must be interpreted that way on import. Decoding it as limited is worth +22% luma and -5.3% R/G, more than twice the firmware ceiling that is already on the record as unfixable.
- Compensation history: 45 (08-01) -> 65 (08-06) -> 55 (08-07). The user asked to 'back it down a little', saw 65-vs-55 frames, said 'Fine for now', and DECLINED an offered 60.

## Open / unmeasured

_Nothing outstanding._

## Last known good — the diff target

Copied verbatim from `BASELINE-2026-08-07-pm.json`. Re-seed with
`python tools/calibrate.py reseed --camera CAM7` after a deliberate change,
and record why in the history below.

```json
{
 "device": "Amcrest IP4M-1041W (Dahua CGI)",
 "config_profile_field": 2,
 "video_in_mode": {
  "Config[0]": "2",
  "Mode": "0"
 },
 "color": {
  "Brightness": "50",
  "ChromaSuppress": "1",
  "Contrast": "45",
  "Gamma": "15",
  "Hue": "50",
  "Saturation": "38",
  "Style": "Standard"
 },
 "exposure": {
  "AntiFlicker": "2",
  "AutoGainMax": "7",
  "Backlight": "0",
  "BacklightRegion[0]": "3096",
  "BacklightRegion[1]": "3096",
  "BacklightRegion[2]": "5096",
  "BacklightRegion[3]": "5096",
  "Compensation": "58",
  "DoubleExposure": "0",
  "Gain": "12",
  "GainMax": "7",
  "GainMin": "7",
  "GlareInhibition": "0",
  "Iris": "50",
  "IrisAuto": "false",
  "IrisMax": "50",
  "IrisMin": "0",
  "Mode": "4",
  "RecoveryTime": "900",
  "Rect[0]": "0",
  "Rect[1]": "0",
  "Rect[2]": "0",
  "Rect[3]": "0",
  "SlowAutoExposure": "0",
  "SlowShutter": "false",
  "SlowSpeed": "30",
  "Speed": "0",
  "Value1": "16.670000",
  "Value2": "16.670000",
  "WideDynamicRange": "0",
  "WideDynamicRangeMode": "0"
 },
 "white_balance": {
  "ColorTemperatureLevel": "50",
  "GainBlue": "33",
  "GainGreen": "50",
  "GainRed": "34",
  "ManualDatumRect[0]": "3096",
  "ManualDatumRect[1]": "3096",
  "ManualDatumRect[2]": "5096",
  "ManualDatumRect[3]": "5096",
  "Mode": "Manual"
 },
 "daynight_all_profiles": {
  "profile0": "Color",
  "profile1": "Color",
  "profile2": "Color"
 },
 "backlight": {
  "BacklightMode": "Default",
  "BacklightRegion[0]": "3096",
  "BacklightRegion[1]": "3096",
  "BacklightRegion[2]": "5096",
  "BacklightRegion[3]": "5096",
  "GlareInhibition": "50",
  "Intensity": "50",
  "IntensityMode": "Disable",
  "Mode": "Off",
  "WideDynamicRange": "7"
 },
 "sharpness": {
  "Level": "50",
  "Mode": "1",
  "Sharpness": "47"
 },
 "all_profiles": {
  "VideoInColor[0]": {
   "Brightness": "50",
   "ChromaSuppress": "1",
   "Contrast": "45",
   "Gamma": "15",
   "Hue": "50",
   "Saturation": "38",
   "Style": "Standard"
  },
  "VideoInColor[1]": {
   "Brightness": "50",
   "ChromaSuppress": "1",
   "Contrast": "45",
   "Gamma": "15",
   "Hue": "50",
   "Saturation": "38",
   "Style": "Standard"
  },
  "VideoInColor[2]": {
   "Brightness": "50",
   "ChromaSuppress": "1",
   "Contrast": "45",
   "Gamma": "15",
   "Hue": "50",
   "Saturation": "38",
   "Style": "Standard"
  },
  "VideoInExposure[0]": {
   "AntiFlicker": "2",
   "AutoGainMax": "7",
   "Backlight": "0",
   "BacklightRegion[0]": "3096",
   "BacklightRegion[1]": "3096",
   "BacklightRegion[2]": "5096",
   "BacklightRegion[3]": "5096",
   "Compensation": "58",
   "DoubleExposure": "0",
   "Gain": "12",
   "GainMax": "7",
   "GainMin": "7",
   "GlareInhibition": "0",
   "Iris": "50",
   "IrisAuto": "false",
   "IrisMax": "50",
   "IrisMin": "0",
   "Mode": "4",
   "RecoveryTime": "900",
   "Rect[0]": "0",
   "Rect[1]": "0",
   "Rect[2]": "0",
   "Rect[3]": "0",
   "SlowAutoExposure": "0",
   "SlowShutter": "false",
   "SlowSpeed": "30",
   "Speed": "0",
   "Value1": "16.670000",
   "Value2": "16.670000",
   "WideDynamicRange": "0",
   "WideDynamicRangeMode": "0"
  },
  "VideoInExposure[1]": {
   "AntiFlicker": "2",
   "AutoGainMax": "7",
   "Backlight": "0",
   "BacklightRegion[0]": "3096",
   "BacklightRegion[1]": "3096",
   "BacklightRegion[2]": "5096",
   "BacklightRegion[3]": "5096",
   "Compensation": "58",
   "DoubleExposure": "0",
   "Gain": "12",
   "GainMax": "7",
   "GainMin": "7",
   "GlareInhibition": "0",
   "Iris": "50",
   "IrisAuto": "false",
   "IrisMax": "50",
   "IrisMin": "0",
   "Mode": "4",
   "RecoveryTime": "900",
   "Rect[0]": "0",
   "Rect[1]": "0",
   "Rect[2]": "0",
   "Rect[3]": "0",
   "SlowAutoExposure": "0",
   "SlowShutter": "false",
   "SlowSpeed": "30",
   "Speed": "0",
   "Value1": "16.670000",
   "Value2": "16.670000",
   "WideDynamicRange": "0",
   "WideDynamicRangeMode": "0"
  },
  "VideoInExposure[2]": {
   "AntiFlicker": "2",
   "AutoGainMax": "7",
   "Backlight": "0",
   "BacklightRegion[0]": "3096",
   "BacklightRegion[1]": "3096",
   "BacklightRegion[2]": "5096",
   "BacklightRegion[3]": "5096",
   "Compensation": "58",
   "DoubleExposure": "0",
   "Gain": "12",
   "GainMax": "7",
   "GainMin": "7",
   "GlareInhibition": "0",
   "Iris": "50",
   "IrisAuto": "false",
   "IrisMax": "50",
   "IrisMin": "0",
   "Mode": "4",
   "RecoveryTime": "900",
   "Rect[0]": "0",
   "Rect[1]": "0",
   "Rect[2]": "0",
   "Rect[3]": "0",
   "SlowAutoExposure": "0",
   "SlowShutter": "false",
   "SlowSpeed": "30",
   "Speed": "0",
   "Value1": "16.670000",
   "Value2": "16.670000",
   "WideDynamicRange": "0",
   "WideDynamicRangeMode": "0"
  },
  "VideoInWhiteBalance[0]": {
   "ColorTemperatureLevel": "50",
   "GainBlue": "33",
   "GainGreen": "50",
   "GainRed": "34",
   "ManualDatumRect[0]": "3096",
   "ManualDatumRect[1]": "3096",
   "ManualDatumRect[2]": "5096",
   "ManualDatumRect[3]": "5096",
   "Mode": "Manual"
  },
  "VideoInWhiteBalance[1]": {
   "ColorTemperatureLevel": "50",
   "GainBlue": "33",
   "GainGreen": "50",
   "GainRed": "34",
   "ManualDatumRect[0]": "3096",
   "ManualDatumRect[1]": "3096",
   "ManualDatumRect[2]": "5096",
   "ManualDatumRect[3]": "5096",
   "Mode": "Manual"
  },
  "VideoInWhiteBalance[2]": {
   "ColorTemperatureLevel": "50",
   "GainBlue": "33",
   "GainGreen": "50",
   "GainRed": "34",
   "ManualDatumRect[0]": "3096",
   "ManualDatumRect[1]": "3096",
   "ManualDatumRect[2]": "5096",
   "ManualDatumRect[3]": "5096",
   "Mode": "Manual"
  },
  "VideoInDayNight[0]": {
   "Delay": "2",
   "Mode": "Color",
   "Sensitivity": "3",
   "Type": "Mechanism"
  },
  "VideoInDayNight[1]": {
   "Delay": "2",
   "Mode": "Color",
   "Sensitivity": "3",
   "Type": "Mechanism"
  },
  "VideoInDayNight[2]": {
   "Delay": "2",
   "Mode": "Color",
   "Sensitivity": "3",
   "Type": "Mechanism"
  },
  "VideoInBacklight[0]": {
   "BacklightMode": "Default",
   "BacklightRegion[0]": "3096",
   "BacklightRegion[1]": "3096",
   "BacklightRegion[2]": "5096",
   "BacklightRegion[3]": "5096",
   "GlareInhibition": "50",
   "Intensity": "50",
   "IntensityMode": "Disable",
   "Mode": "Off",
   "WideDynamicRange": "7"
  },
  "VideoInBacklight[1]": {
   "BacklightMode": "Default",
   "BacklightRegion[0]": "3096",
   "BacklightRegion[1]": "3096",
   "BacklightRegion[2]": "5096",
   "BacklightRegion[3]": "5096",
   "GlareInhibition": "50",
   "Intensity": "50",
   "IntensityMode": "Disable",
   "Mode": "Off",
   "WideDynamicRange": "7"
  },
  "VideoInBacklight[2]": {
   "BacklightMode": "Default",
   "BacklightRegion[0]": "3096",
   "BacklightRegion[1]": "3096",
   "BacklightRegion[2]": "5096",
   "BacklightRegion[3]": "5096",
   "GlareInhibition": "50",
   "Intensity": "50",
   "IntensityMode": "Disable",
   "Mode": "Off",
   "WideDynamicRange": "7"
  },
  "VideoInSharpness[0]": {
   "Level": "50",
   "Mode": "1",
   "Sharpness": "47"
  },
  "VideoInSharpness[1]": {
   "Level": "50",
   "Mode": "1",
   "Sharpness": "47"
  },
  "VideoInSharpness[2]": {
   "Level": "50",
   "Mode": "1",
   "Sharpness": "47"
  },
  "Lighting[0]": {
   "Correction": "50",
   "Angle": "50",
   "Light": "50",
   "Mode": "Off",
   "Sensitive": "3"
  },
  "Lighting[1]": {
   "Correction": "50",
   "Angle": "50",
   "Light": "50",
   "Mode": "Off",
   "Sensitive": "3"
  },
  "Lighting[2]": {
   "Correction": "50",
   "Angle": "50",
   "Light": "50",
   "Mode": "Off",
   "Sensitive": "3"
  }
 }
}
```

## Validation history — APPEND ONLY, never overwrite

The user's standing rule. "Looked at it and kept it" is the evidence that a
value is still trusted, and it is lost if each entry replaces the last.

- **2026-08-01 ~19:5x** — *"Whoah! Immediately improved / absolutely better"* on the Gamma 0→15 change.
- **2026-08-06** — picked Compensation 65 from a settled A/B.
- **2026-08-07** — asked to "back it down a little", saw 65-vs-55 frames, said **"Fine for now"**, and DECLINED an offered 60. Landed on 55, all three profiles.
- **2026-08-22** — **DETERMINISTIC LOCK.** User: *"We WANT deterministic. No auto settings."*
  Picture had collapsed to **luma 36.5 / crush 51.98%** with the config reading IDENTICAL to
  reference. Cause was NOT drift: the scene CAM7 meters changed (wider FOV after the 08-21
  power-cycle/re-install), and a Compensation tuned for the old view is wrong for the new one.
  Re-writing every reference value to all three profiles changed nothing — proof the camera
  had forgotten nothing.
  **Compensation 55 -> 58** (measured on BI clips: crush 51.98% -> 4.02%; the response is a
  CLIFF and plateaus by 62, where 62 and 70 differ by 0.1 luma).
  **AutoGainMax/GainMax/GainMin 12 -> 6** on the user's own hunch of "too much gain" — it was
  right: hot(>240) 7.04% -> 4.79%, red pegged 10.17% -> 5.99%. Gain 4 measured identical to 6,
  so 6 is the knee, not a compromise.
  **Shutter pinned Value1 = Value2 = 16.67 ms**, **IrisAuto false**, **Lighting Mode Off** on
  all three profiles. `VideoInExposure.Mode` is INERT here and `VideoInMode[0].Mode` accepts
  ONLY 0, so auto is killed by COLLAPSING THE RANGES, not by flipping a mode.
  He saw the A/B and said *"They look similar to my eyes. I'll take it."*
  🎯 **THE GOOD-STATE FINGERPRINT — one clip measurement says if it has moved:**
  **luma 88.4 · crush 5.05% · hot(>=240) 5.07% · Rpeg(R>=250) 6.24% · R/G 1.276 · B/G 1.076.**
- 🔴 **2026-08-22 (later) — RETRACTION: the fingerprint in the entry above (`luma 88.4 /
  crush 5.05%`) IS WRONG AND MUST NOT BE USED.** It was measured on the clip's PRE-ROLL, i.e.
  the state BEFORE that lock was written. A TWAB clip is ~70 s with the press ~60 s in, so
  sampling t=10..55 s reports the PREVIOUS setting. Measured honestly, that lock (gain 6 @
  16.67 ms) produced **luma ~35** -- it UNDER-exposed the camera, and the verification was
  structurally incapable of showing it. See [[measure-the-tail-not-the-preroll]].
- **2026-08-22 (final) — SETTLED BY HIS EYE, from a 4-rung ladder shot back-to-back.** He chose
  rung C: *"lg10s60 looks best."*
  **PINNED: gain 10 (GainMin = GainMax = AutoGainMax) - shutter Value1 = Value2 = 16.67 ms -
  Compensation 58 - IrisAuto false - Gamma 15 - Lighting Mode Off - ALL THREE PROFILES.**
  Rejected on the way: gain 4 @ 1/30 and gain 8 (blown out, 11-23% of frame hot); Gamma 9 (a
  LOWER gamma number is the brighter, flatter end on this camera -- 26% hot).
  ✅ **VERIFIED ON A FULLY-SETTLED CLIP** (written >2 min before the press, so pre-roll and tail
  agree): flat **luma 102.6 / crush 3.70%** at every timestamp from t=5 s to t=66 s; the
  fleet run read **luma 105.0 / crush 2.90%** against CAM1 at 86.0.
  🎯 **FINGERPRINT (tail-honest this time): luma ~102.6 - crush ~3.7% - R/G 1.259 - B/G 1.071.**
  ⚠️ **TIME-BOUND.** Readings taken an hour apart did not chain tonight even with the camera
  pinned; compare only within one clip or one back-to-back ladder.
  🔴 **THE POWER-CYCLE FAULT IS STILL OPEN** -- his words: *"there is a real problem that is very
  hard to hunt down every time it power cycles."* This entry is a calibration, not a fix.
- **2026-08-22 (whites) — he looked at the pinned result and asked for it "SLIGHTLY less
  blown out on the whites."** Measured the actual complaint rather than mean luma: **blown-white
  = fraction of frame with R, G and B all >= 250** (a white that has lost its detail).
  Back-to-back ladder: g10/c50 **3.50%** · g9/c50 3.16% · g10/c45 3.14% · **g9/c45 2.77%**.
  **APPLIED: gain 9 (min=max=auto) + Contrast 50 -> 45, all three profiles.**
  🔑 **CONTRAST 45 IMPROVED BOTH ENDS AT ONCE** — it pulled the whites down AND opened the
  shadows: crush 9.35% -> 1.92% on the ladder, and **0.16%** on the settled clip. Lowering
  contrast was strictly better here than taking light away, which is why gain only moved one
  step. Gamma stays 15 (it is already the most contrast this camera has; a LOWER gamma number
  is the brighter, flatter end).
  ✅ Verified on a settled clip: **luma 103.8 · crush 0.16%** against CAM1 at 86.2.
  🎯 **FINGERPRINT: luma ~103.8 · crush ~0.2% · blown-white ~2.8% · R/G 1.259 · B/G 1.073.**
  ⚠️ Still TIME-BOUND — compare within one clip or one back-to-back ladder only.

- **2026-08-22 20:1x — 🔒 NEW LAST KNOWN GOOD: gain 9 → 7, Saturation 50 → 38.** His complaint,
  in his words: CAM7 is fixed *"but it's a TINY bit more bright and saturated than the other
  cameras."* Two complaints, two knobs, **laddered separately so they could not confound each
  other** — and both back-to-back, the only comparison that chains on this rig.
  🛑 **THE OBVIOUS KNOB WAS THE WRONG ONE AND WAS NOT TOUCHED.** Compensation sits at 58 and
  **55 crushed 51.98% of the frame** — the response is a cliff, so a "small trim" there is the
  single riskiest move available. `Brightness` was already measured and rejected (a black-lift
  that milks the picture) and `Gamma` is pegged at 15, its most-contrast end, where *lower*
  means brighter. **Gain was the only clean light lever left.**

  | gain | 9 | 8 | 7 |
  |---|---|---|---|
  | luma | 97.81 | 86.59 | 88.66 |
  | blown-white | 6.08% | 4.15% | 4.16% |

  ⚠️ **8 and 7 did not separate** — 2 luma apart and non-monotonic, i.e. inside scene noise with
  him moving in frame. **9 → 8 is the only real step in that table.** He picked 7 from the
  stills anyway (*"slight preference for third picture"*), which is a legitimate eye call on an
  unresolvable measurement — recorded as such, not dressed up as a measured optimum.

  | Saturation | 50 | 44 | 38 | 50 (return) |
  |---|---|---|---|---|
  | measured sat | 38.78% | 35.85% | 32.33% | **38.86%** |
  | luma | 89.58 | 89.95 | 90.47 | 89.14 |
  | R/G | 1.3020 | 1.2598 | 1.2210 | 1.3032 |

  ✅ **THE RETURN RUNG IS WHY THIS ONE IS TRUSTWORTHY: 38.78% out, 38.86% back — a 0.08pp gap.**
  Up-then-down is evidence; that is what the gain ladder could not produce. **Saturation is
  independent of exposure here** (luma flat 89.1–90.5 across all four rungs), so it does not
  disturb the gain pick. His ruling: **"saturation 38 looks the most natural."**
  🎯 **NEW FINGERPRINT** (BI clip `CAM7.20260822_200956.mp4`, filed in a
  separate sub-folder of the Blue Iris clip directory):
  **`luma 94.07 · crush 0.27% · hot 5.16% · blown-white 1.09% · R/G 1.1882 · B/G 1.0707`.**
  🔴 **SUPERSEDES the earlier `luma 103.8 · crush 0.16% · blown-white 2.77% · R/G 1.259` — that
  was gain 9 / Saturation 50 and is now WRONG.** The deltas are exactly what the two writes
  predict: dimmer, far fewer blown whites (2.77% → 1.09%), less red (1.259 → 1.188).
  ✅ Verified by waiting out the pre-roll before pressing: **t = 35 / 50 / 62 s read 94.07 /
  94.14 / 94.15** — flat to 0.08 luma. (The 4.4 head-to-tail spread is him moving in the first
  20 s, not the camera converging; the tail is the settled state.)
  🛑 **A PROCESS FAULT WORTH KNOWING: an interrupt does not recall an ssh command already in
  flight.** Mid-ladder the user interrupted, and two queued commands had already executed —
  the camera was left at gain 8 / Saturation 38 while everyone believed it was at the value in
  the last still he saw. Caught only by polling the camera instead of trusting the transcript.
  **After any interrupted write sequence on this rig, READ THE CAMERA BEFORE SAYING ANYTHING
  ABOUT ITS STATE.**
  Reseeded twice (274 keys each, round-trip verified) — once for the gain, once for the
  saturation; both diffs inspected and confined to the intended keys.
  Ladder frames + rollbacks: `var/calibrate/cam7-ab-2026-08-22/`; fingerprint frames in
  `var/calibrate/cam7-verify-2026-08-22/`. Tool: `cam7_knob.py` (ladders one knob across all
  three profiles, `Gain` expands to Min/Max/AutoMax), `verify_cam7_clip.py`.

### 2026-08-31 - VALIDATED after a power-cycle un-stick (no config change)

User reported "the exact same regression" after a power cycle. `check` read IDENTICAL
(274 keys) throughout, before AND after. **No calibration value was changed.**

The picture was restored by writing a DIFFERENT `VideoInColor.Gamma` value and then
writing 15 back on all three profiles: a power-cycled Dahua reports its stored config
without applying it, and a same-value rewrite is discarded by the firmware. Tool:
`unstick.py CAM7` (toolkit root).

- before: luma 37.27 / crush 59.09% / hot 0.00% / R/G 1.2313
- after:  luma 93.21 / crush 0.21% / hot 5.08% / blown-white 1.11% / R/G 1.1927
- fingerprint: luma 94.07 / crush 0.27% / hot 5.16% / blown-white 1.09% / R/G 1.1882

Verified on BI clip `CAM7.20260831_222930Z.mp4`, flat head to tail (0.03 luma spread).
**The locked values are unchanged and re-confirmed: gain 7, shutter 16.67 ms,
Compensation 58, Contrast 45, Gamma 15, Saturation 38, IrisAuto false, Lighting Off.**

NOTE: Compensation is now INERT on this camera (58 -> 70 = +1.9 luma) because the lock
collapsed gain and shutter, leaving the AE loop nowhere to move. Do not ladder it to
recover from this fault.
