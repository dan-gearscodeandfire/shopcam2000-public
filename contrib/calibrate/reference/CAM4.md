# CAM4 - Second Amcrest angle.

> **This file is the source of truth for CAM4.** Machine values below are diffed
> against the live camera by `python tools/calibrate.py check`. Prose is here
> because a JSON dump cannot carry which knobs lie.

- **Transport:** Dahua CGI :80 -- resolve the ACTIVE PROFILE first (runs profile 2)
- **Locked:** no
- **Clip ceiling:** **255** - measured 2026-08-08. Peak luma reached 255 at every point of a
  6-point Compensation sweep (50→100) and on the untouched frame, so this camera does NOT
  have a reduced ceiling like CAM7 (224) or CAM8 (241). ⚠️ Measured off a Blue Iris JPEG, not
  a raw frame; good enough to stop assuming a low ceiling, not a substitute for a chart pass.
- **Settle after a write:** 20.0 s

## Exposed parameters - and how each range is KNOWN

`probed` = written until it refused. `device` = the device reported it.
🔴 **`assumed` = NOT VERIFIED. Treat an assumed range as a task, not a fact** -
this fleet has burned sessions on assumed ranges, and `drivers.capabilities()`
still reports every Dahua knob as 0–100, which is false for Gamma.

| knob | lo | hi | how known |
|---|---|---|---|
| `Brightness` | 0 | 100 | 🔴 **assumed** |
| `Compensation` | 0 | 100 | 🔴 **assumed** |
| `Contrast` | 0 | 100 | 🔴 **assumed** |
| `GainBlue` | 0 | 100 | 🔴 **assumed** |
| `GainRed` | 0 | 100 | 🔴 **assumed** |
| `Gamma` | 0 | 15 | probed |
| `Hue` | 0 | 100 | 🔴 **assumed** |
| `Saturation` | 0 | 100 | 🔴 **assumed** |

## Known-dead or inert knobs

_None known._ Absence of a known-dead knob is not proof one works.

## What is TRUE about this camera - measured, not assumed

- WRITES TO [0][0] ARE SILENT NO-OPS. This camera runs profile 2. Always resolve the active profile before writing anything.
- GAMMA IS INERT ONLY WHEN THE FRAME IS NOT CRUSHED, and the two halves of that were measured 6 days apart. ORIGINAL (2026-08-02, lights as they were then): frame crush 0.04-0.08% at EVERY gamma from 0 to 15, all five pictures identical -- the user was right that 'these pics all look the same'. There were no blocked shadows for a curve to rescue, which is WHY it did nothing. RE-TESTED 2026-08-08 under the current lighting, with the frame crushing 15-28%: gamma is the STRONGEST knob on this camera. Gamma 5 -> 9 took crush 15.25% -> 0.00%, luma 68 -> 97, saturation 58.7 -> 22.0, and it CUT clipped highlights (5.32% -> 1.52%) rather than trading shadows for them. 🔑 So the old entry was never wrong, it was CONDITIONAL: CHECK CRUSH BEFORE REACHING FOR A CURVE, and check it again after the room changes -- the same instruction now points the other way.
- VideoInColor Gamma range is 0-15, NOT 0-100 -- 20/30/50/100 are all rejected with HTTP 400. Both Amcrests ship at Gamma 5, which looks absurd next to Brightness/Contrast/Saturation 50 and is simply a different scale.
- WDR is OFF and must stay off: 0->16 made crush WORSE (19.7% -> 25.3%).
- screencap=1 is set on this camera and it is NOT a screen-capture flag -- use `type` (4 = network camera) to tell what a camera is.

## Open / unmeasured

- Compensation is MAPPED but unused: its knee is 60 (crush 26.66% → 16.62%), then it flattens
  (80 → 15.60%, 100 → 15.42%) while clipped highlights climb 1.18% → 5.33%. Gamma 9 alone
  reaches 0.00% crush at 0.46% highlights, so Compensation stays at 50. Left here so nobody
  re-runs the sweep.
- 🛑 `tools/calibrate.py reseed` IS DESTRUCTIVE ON THIS CAMERA - see the history entry for
  2026-08-08. Edit the JSON block by hand.

## Last known good - the diff target

Copied verbatim from `BASELINE-2026-08-07-pm.json`. Re-seed with
`python tools/calibrate.py reseed --camera CAM4` after a deliberate change,
and record why in the history below.

```json
{
 "device": "Amcrest IP-cam (Dahua CGI)",
 "config_profile_field": 2,
 "video_in_mode": {
  "Config[0]": "2",
  "Mode": "0"
 },
 "color": {
  "Brightness": "50",
  "ChromaSuppress": "1",
  "Contrast": "50",
  "Gamma": "11",
  "Hue": "50",
  "Saturation": "50",
  "Style": "Standard"
 },
 "exposure": {
  "AntiFlicker": "2",
  "AutoGainMax": "8",
  "Backlight": "0",
  "BacklightRegion[0]": "3096",
  "BacklightRegion[1]": "3096",
  "BacklightRegion[2]": "5096",
  "BacklightRegion[3]": "5096",
  "Compensation": "50",
  "DoubleExposure": "0",
  "Gain": "1",
  "GainMax": "8",
  "GainMin": "8",
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
  "GainBlue": "34",
  "GainGreen": "50",
  "GainRed": "23",
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
  "WideDynamicRange": "0"
 },
 "sharpness": {
  "Level": "50",
  "Mode": "1",
  "Sharpness": "40"
 },
 "all_profiles": {
  "VideoInColor[0]": {
   "Brightness": "50",
   "ChromaSuppress": "1",
   "Contrast": "50",
   "Gamma": "11",
   "Hue": "50",
   "Saturation": "50",
   "Style": "Standard"
  },
  "VideoInColor[1]": {
   "Brightness": "50",
   "ChromaSuppress": "1",
   "Contrast": "50",
   "Gamma": "11",
   "Hue": "50",
   "Saturation": "50",
   "Style": "Standard"
  },
  "VideoInColor[2]": {
   "Brightness": "50",
   "ChromaSuppress": "1",
   "Contrast": "50",
   "Gamma": "11",
   "Hue": "50",
   "Saturation": "50",
   "Style": "Standard"
  },
  "VideoInExposure[0]": {
   "AntiFlicker": "2",
   "AutoGainMax": "8",
   "Backlight": "0",
   "BacklightRegion[0]": "3096",
   "BacklightRegion[1]": "3096",
   "BacklightRegion[2]": "5096",
   "BacklightRegion[3]": "5096",
   "Compensation": "50",
   "DoubleExposure": "0",
   "Gain": "1",
   "GainMax": "8",
   "GainMin": "8",
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
   "AutoGainMax": "8",
   "Backlight": "0",
   "BacklightRegion[0]": "3096",
   "BacklightRegion[1]": "3096",
   "BacklightRegion[2]": "5096",
   "BacklightRegion[3]": "5096",
   "Compensation": "50",
   "DoubleExposure": "0",
   "Gain": "1",
   "GainMax": "8",
   "GainMin": "8",
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
   "AutoGainMax": "8",
   "Backlight": "0",
   "BacklightRegion[0]": "3096",
   "BacklightRegion[1]": "3096",
   "BacklightRegion[2]": "5096",
   "BacklightRegion[3]": "5096",
   "Compensation": "50",
   "DoubleExposure": "0",
   "Gain": "1",
   "GainMax": "8",
   "GainMin": "8",
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
   "GainBlue": "34",
   "GainGreen": "50",
   "GainRed": "23",
   "ManualDatumRect[0]": "3096",
   "ManualDatumRect[1]": "3096",
   "ManualDatumRect[2]": "5096",
   "ManualDatumRect[3]": "5096",
   "Mode": "Manual"
  },
  "VideoInWhiteBalance[1]": {
   "ColorTemperatureLevel": "50",
   "GainBlue": "34",
   "GainGreen": "50",
   "GainRed": "23",
   "ManualDatumRect[0]": "3096",
   "ManualDatumRect[1]": "3096",
   "ManualDatumRect[2]": "5096",
   "ManualDatumRect[3]": "5096",
   "Mode": "Manual"
  },
  "VideoInWhiteBalance[2]": {
   "ColorTemperatureLevel": "50",
   "GainBlue": "34",
   "GainGreen": "50",
   "GainRed": "23",
   "ManualDatumRect[0]": "3096",
   "ManualDatumRect[1]": "3096",
   "ManualDatumRect[2]": "5096",
   "ManualDatumRect[3]": "5096",
   "Mode": "Manual"
  },
  "VideoInDayNight[0]": {
   "Delay": "6",
   "Mode": "Color",
   "Sensitivity": "2",
   "Type": "Mechanism"
  },
  "VideoInDayNight[1]": {
   "Delay": "6",
   "Mode": "Color",
   "Sensitivity": "2",
   "Type": "Mechanism"
  },
  "VideoInDayNight[2]": {
   "Delay": "6",
   "Mode": "Color",
   "Sensitivity": "2",
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
   "WideDynamicRange": "16"
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
   "WideDynamicRange": "16"
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
   "WideDynamicRange": "0"
  },
  "VideoInSharpness[0]": {
   "Level": "50",
   "Mode": "1",
   "Sharpness": "40"
  },
  "VideoInSharpness[1]": {
   "Level": "50",
   "Mode": "1",
   "Sharpness": "40"
  },
  "VideoInSharpness[2]": {
   "Level": "50",
   "Mode": "1",
   "Sharpness": "40"
  },
  "Lighting[0]": {
   "Correction": "50",
   "Angle": "50",
   "Light": "50",
   "Mode": "Off",
   "Sensitive": "3"
  },
  "Lighting[1]": {
   "_absent": "Lighting[0][1] not exposed by this camera"
  },
  "Lighting[2]": {
   "_absent": "Lighting[0][2] not exposed by this camera"
  }
 }
}
```

## Validation history - APPEND ONLY, never overwrite

The user's standing rule. "Looked at it and kept it" is the evidence that a
value is still trusted, and it is lost if each entry replaces the last.

- **2026-08-02 00:47** - kept Gamma 5 from a 5-up sheet, re-measured with WDR off; WB re-checked same pass.
- **2026-08-02 00:54** - kept Compensation 50 from a 6-up sheet, first sweep of it with WDR off.
- **2026-08-08 20:02** - **Gamma 5 → 9.** The user reported CAM4 "looks dark and
  oversaturated" and asked for the last-known-good to be checked first: `check` said
  IDENTICAL, 266/266, and was right - no setting had drifted. The room had. He then declared
  the current lighting (door shut, all lights on) the REFERENCE LIGHTING, and asked only to
  "stop crushing to black". Crush 28.17% → **0.00%**, luma 44.6 → 75.9, saturation 59.7 →
  22.7 (the 08-07 frame was 22.9), clipped highlights 1.16% → 0.46%. Brightness / Contrast /
  Saturation / Hue read back unchanged at 50 afterwards; Compensation left at 50. **User saw
  the A/B and said "CAM4 looks way better".** 🔑 The magenta cast was never a colour fault -
  blocked shadows left the coloured wall washes as the only thing carrying level.
- **2026-08-08 20:1x - ⚠️ `reseed --camera CAM4` DESTROYED THIS FILE AND WAS REVERTED.**
  It rewrites the last-known-good block from `drivers.read()`, which on CAM4 returns only the
  WB and exposure blocks - no colour block at all - while `check` compares 266 keys from the
  richer fleet reader. Result: **294 deletions, 16 insertions**, and `check` went from clean
  to **310/310 different, every one `reference='<absent>'`**. Recovered with
  `git checkout -- reference/CAM4.md` (it was uncommitted). **The Gamma edit above was then
  made BY HAND** in both places `check` compares: `color.Gamma` and
  `all_profiles.VideoInColor[2].Gamma`. ~~Profiles [0] and [1] stay at 5 - the camera runs
  profile 2~~ 🔴 **SUPERSEDED 2026-08-16: THAT DECISION WAS THE BUG.** `Mode=0` means
  SELF-ADAPTING - the camera picks its own profile by light level, and `Config[0]` is a
  static field that cannot see a live switch. Leaving [0] and [1] at Gamma 5 meant every
  time the shop dimmed, CAM4 silently reverted to the crushed curve. **Write every knob to
  ALL THREE profiles on this camera.**
  🛑 **Do not run `reseed` on this camera until that tool is fixed.**

- **2026-08-16 20:3x - 🎯 GAMMA 9 WRITTEN TO ALL THREE PROFILES. THIS IS THE FIX FOR
  "CAM4 IS CONSISTENTLY PROBLEMATIC."** Measured on **Blue Iris clips**, per the user's
  2026-08-16 ruling that BI clips are the calibration reference:
  **frame crush 43.56% → 1.46%, luma 53.20 → 82.40** (CAM1 = 84.61, so CAM4 now sits within
  2.2 of the fleet anchor). One write, verified per profile on read-back.
  🔴 **Root cause: the 2026-08-08 Gamma 9 fix went to profile 2 ONLY.** With
  `VideoInMode[0].Mode = 0` the camera self-adapts, so whenever it switched to profile 0 or 1
  it ran the old Gamma 5 and crushed. The symptom looked intermittent and unexplainable
  because it was - it tracked the room light, not the config.
  ⚠️ **A JPEG-snapshot sweep of Gamma 5..13 measured `crush 0.00%` at EVERY value and
  concluded gamma was inert. THAT CONCLUSION WAS FALSE** - the camera's JPEG snapshot path and
  its H.264 mainstream are different images (same family as CAM6's snapPicture2-vs-videoMain
  bug). `yrange_probe` on the recorded stream showed **45.6% of the coded Y plane below 16**,
  correctly tagged FULL range. 🔑 **Sweep the transport the consumer uses. A snapshot sweep on
  this camera is worse than no sweep, because it returns confident zeros.**
  ⚠️ One clip, `CAM4.20260817_002356Z.mp4`, was recorded DURING that sweep with gamma stepping
  through it, and was deleted at the user's instruction.
  Rollback: `var/calibrate/rollback-CAM4-Gamma-unify-2026-08-16.json` (+ durable copy).

- **2026-08-16 20:4x - 🔴 CORRECTED A LATENT ERROR IN THIS FILE'S OWN LAST-KNOWN-GOOD.**
  After the gamma fix, `check` reported 2 differences: `backlight.WideDynamicRange` and
  `all_profiles.VideoInBacklight[2].WideDynamicRange` at **reference 16 vs live 0**.
  ✅ **Corrected to 0 on profile 2 and on the flat `backlight.` view - the camera was right.**
  ⚠️ **But profiles [0] and [1] genuinely read 16 and were left at 16.** My first patch set all
  four to 0 and `check` immediately caught it - the reference then disagreed with the camera in
  the opposite direction. 🔑 **`VideoInBacklight[0][*].Mode = Off` on ALL THREE profiles, so the
  `WideDynamicRange` integer is an INERT STRENGTH REGISTER, not a live setting.** WDR is off at
  the master switch fleet-wide; the 16 is a harmless leftover. **Do not "fix" it on the camera -
  writing it would be servoing a value that does nothing.** This is NOT the gamma bug repeating,
  and the difference is the master switch.
  **The camera was right and this file was wrong.** The prose above has said since 2026-07-26
  that *WDR is OFF and must stay off (0→16 made crush WORSE, 19.7%→25.3%)*, and the standing
  fleet rule is WDR/HDR always off - but the JSON block still carried 16.
  🔑 **A last-known-good that contradicts its own file's prose is a trap, not a record:**
  anyone "restoring CAM4 to last known good" would have switched WDR back ON and undone the
  gamma fix in the same motion. Set to 0 to match the live camera and the written rule.
  **No camera was touched for this - the fix was to the reference.**

- **2026-08-22 19:29 - 🔒 CAM4 MADE DETERMINISTIC, THE CAM7 TREATMENT. Gamma 9 → 11,
  gain pinned 8, `IrisAuto` false - ALL THREE PROFILES.** User's ask: "do the quick fix you
  did for CAM7 for CAM4." His ruling on the precondition: the shop lighting as of 19:14 IS
  the condition to pin to. `check` said IDENTICAL (266 keys) before anything was touched -
  again, nothing had drifted.
  🔑 **THE CAM7 RECIPE TRANSFERRED; ITS NUMBERS DID NOT.** CAM4 arrived with its ranges
  ALREADY collapsed (`Value1` == `Value2` == 16.67, `GainMin` == `AutoGainMax` == 2), so the
  lever that fixed CAM7 was already pulled here - and pulled shut at 2. **Gain is a slope on
  this camera and it is not the answer:** back-to-back ladder 2 / 5 / 8 / 11 read crush
  47.32% → 44.24% → 39.57% → **37.45%**, luma 46.2 → 63.1, plateauing between 8 and 11
  (+1.5 luma) at the hardware ceiling of 12. **At maximum gain CAM4 still crushed 37% of the
  frame.**
  🥇 **GAMMA IS THE CLIFF, exactly as this file has said since 2026-08-08.** At gain 8:

  | Gamma | luma | crush | hot ≥240 | blown-white | R/G |
  |---|---|---|---|---|---|
  | 9 (old) | 61.27 | **39.34%** | 1.81% | 0.31% | 1.4353 |
  | **11** | 93.35 | **0.72%** | 0.44% | 0.03% | 1.1518 |
  | 13 | 94.17 | 0.71% | 0.43% | 0.03% | 1.1499 |
  | 15 | 98.52 | 0.58% | 0.51% | 0.03% | 1.1473 |

  **It plateaus immediately by 11** - 11 → 15 is 5 luma and no change in crush. User saw the
  9 / 11 / 15 stills and ruled **"Gamma 11 was best, 15 not that much different."** He also
  saw it move on the live feed mid-ladder and said *"something just fixed it"* - that was the
  9 → 11 step.
  🔑 **THE RED CAST WENT WITH THE CRUSH, AGAIN. R/G 1.4353 → 1.1518 with no white-balance
  knob touched** - the same thing this file recorded on 2026-08-08 ("the magenta cast was
  never a colour fault"). **On this camera, a colour complaint measured under crush is a
  TONE complaint.** Do not reach for `GainRed`/`GainBlue` before crush is under control.
  ⚠️ **Gain 8 vs gain 2 at the settled gamma is a live choice, not a finding:** gain 2 reads
  luma 82.27 / crush 1.51% - which is almost exactly this camera's 2026-08-16 known-good
  (82.40 / 1.46%) and is the quieter sensor setting. Gain 8 reads 95.4 / 0.65%. **8 was kept
  on the user's instruction ("leave it where it is now unless you see obvious problems").**
  Gain 2 is the fallback if noise ever becomes the complaint.
  🎯 **FINGERPRINT, measured on the Blue Iris clip `CAM4.20260822_232929Z.mp4`:
  `luma 95.23 · crush 0.73% · hot 0.49% · blown-white 0.03% · R/G 1.1502 · B/G 0.9064`.**
  Verified properly: **the press came >60 s after the write, so the pre-roll already
  carried the new state and the clip is FLAT head to tail (2.47 luma spread across
  t = 5 / 20 / 35 / 50 / 62 s)**. A uniform profile is the proof it settled
  (`measure-the-tail-not-the-preroll`, paid for on CAM7 the night before).
  Ladder frames + rollback dumps: `var/calibrate/cam4-ladder-2026-08-22/`
  (`rollback-VideoInExposure.json`, `rollback-VideoInColor.json`); verification frames in
  `var/calibrate/cam4-verify-2026-08-22/`. Clip filed in a
  separate sub-folder of the Blue Iris clip directory at the user's request, so
  agent-generated takes stay separable from his footage.
  🛑 **Edited BY HAND in all four places `check` compares** (the flat `color.` / `exposure.`
  views and `all_profiles.VideoInColor[0..2]` / `VideoInExposure[0..2]`) - `reseed` is still
  destructive on this camera, see the 2026-08-08 entry.

- **2026-08-22 19:5x - ✅ GAIN 8 SETTLED, NOT PROVISIONAL. User's instruction: "use whatever
  the gain is now for CAM4."** Polled before recording rather than assumed: `GainMin` ==
  `GainMax` == `AutoGainMax` == **8** on all three profiles, Gamma 11, `IrisAuto` false.
  That is already this file's last-known-good, so **no camera write was needed and none was
  made.** The gain 8 vs gain 2 question raised in the entry above is **CLOSED** - 8 is the
  ruling. Gain 2 (luma 82.27 / crush 1.51%, quieter) stays on record as the fallback **if
  noise ever becomes the complaint**, but it is no longer an open decision waiting on him.

### 2026-08-31 - VALIDATED after a power-cycle un-stick (no config change)

Found stuck in the same way as CAM7, and had been since before 08-29 (healthy on
08-25; already collapsed in the 08-29 02:52 press, where CAM7 was still perfect - the
two cameras fail independently). `check` read IDENTICAL (266 keys) before AND after.
**No calibration value was changed.**

Restored with `unstick.py CAM4` (write a different `VideoInColor.Gamma`, then write 11
back, all three profiles).

- before: luma 64.17 / crush 37.04% / R/G 1.4265
- after:  luma 92.89 / crush 0.90% / hot 0.68% / blown-white 0.05% / R/G 1.1355
- fingerprint: luma 95.23 / crush 0.73% / hot 0.49% / blown-white 0.03% / R/G 1.1502

Verified on BI clip `CAM4.20260831_222930Z.mp4`, flat head to tail (0.15 luma spread).
**Locked values unchanged and re-confirmed: Gamma 11, gain 8, shutter 16.67 ms,
IrisAuto false, Iris 50, B/C/Sat/Hue 50, WDR 0.**

The red cast died with the crush again (R/G 1.4265 -> 1.1355) with no WB knob touched -
third time. On CAM4 a COLOUR complaint measured under crush is a TONE complaint.
