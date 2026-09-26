# CAM8 — Top-down bench cam. With CAM5 this pair carries ~80% of all footage, and its face-on view makes it the best chart target on the rig.

> **This file is the source of truth for CAM8.** Machine values below are diffed
> against the live camera by `python tools/calibrate.py check`. Prose is here
> because a JSON dump cannot carry which knobs lie.

- **Transport:** DirectShow ProcAmp (UVC), --device 'USB'  (Exposure lives on camctrl, not procamp)
- **Locked:** no
- **Clip ceiling:** **241** — a patch reaching this is VOID
- **Settle after a write:** 28.0 s

## Exposed parameters — and how each range is KNOWN

`probed` = written until it refused. `device` = the device reported it.
🔴 **`assumed` = NOT VERIFIED. Treat an assumed range as a task, not a fact** —
this fleet has burned sessions on assumed ranges, and `drivers.capabilities()`
still reports every Dahua knob as 0–100, which is false for Gamma.

| knob | lo | hi | how known |
|---|---|---|---|
| `BacklightCompensation` | 0 | 2 | device |
| `Brightness` | -64 | 64 | device |
| `Contrast` | 0 | 64 | device |
| `Exposure` | -13 | -1 | device |
| `Gain` | 0 | 100 | device |
| `Gamma` | 72 | 500 | device |
| `Hue` | -40 | 40 | device |
| `Saturation` | 0 | 128 | device |
| `Sharpness` | 0 | 6 | device |
| `WhiteBalance` | 2800 | 6500 | device |

## Known-dead or inert knobs

_None known._ Absence of a known-dead knob is not proof one works.

## What is TRUE about this camera — measured, not assumed

- CLIP CEILING 241, NOT 255. Measured 2026-08-07: at Exposure -6 and -5 the chart's white patch pegs at 241 and reads sd 0.00 with R/G = B/G = 1.0000 -- a flawless FAKE neutral white balance. Scoring colour there would declare a blown-out camera perfectly balanced.
- GAMMA 100 MEANS GAMMA 1.0 on this camera's 72-500 scale, and its transfer measured NEAR-LINEAR off the DGK chart's grey ramps (8 steps 10-14 code values apart; the two opposing ramps agreeing to +/-2 on 7 of 8). THE GAMMA WAS NEVER THE PROBLEM.
- ITS WHOLE-FRAME CRUSH IS MISLEADING. 31% of the frame reads below 16, but that is the black shelf along the top edge, which is SUPPOSED to be black. The bench -- the actual subject -- crushed 0.65%. Split the frame before concluding anything from a whole-frame number.
- EXPOSURE, NOT GAMMA, WAS THE DEFECT. At -8 the chart's white patch read 170/255 with ZERO clipping anywhere -- the top half of the range unused. -8 -> -7 took white 176 -> 226 and crush 15.5% -> 3.9%. Each Exposure step is a STOP (DirectShow Exposure is log2 seconds).
- AUTO WHITE BALANCE WAS BEATEN BY MANUAL 3540 ON 2026-08-07 -- and this REVERSES a 07-26 and 07-30 ruling that said the opposite. That ruling was STALE, not wrong: both tests predate fixture #4 (07-29) and the dark-purple flood hue settling (07-30), so they measured a different room. Manual 3540 now wins on BOTH axes: R/G 0.869 vs auto 0.849, B/G 1.026 vs 0.953, which puts the residual on BLUE instead of GREEN.
- A COLOUR RULING IS ONLY VALID FOR THE LIGHTING IT WAS MEASURED IN. That is the general lesson from the above, and it applies to every camera here.
- AUTO WB RE-COMPUTES WHEN A LARGE COLOURED OBJECT IS SET ON THE BENCH -- measured: setting a chart down moved R/G +0.11 to +0.20 on every plywood patch in frame, under light that did not change. That is a real hazard for a bench camera and a second reason manual now wins.
- SCORE COLOUR BY WHICH CHANNEL IS HIGHEST, not by total deviation. The eye hunts green and forgives blue. Minimising |R/G-1|+|B/G-1| picked this camera's WORST-LOOKING setting twice.
- ITS BOXES GO STALE SILENTLY. When the chart was moved between takes the old regions still returned plausible ratios; the only tell was sd jumping 1.2 -> 33. Author regions in calibrate/rois.py, which carries authored_on provenance, and LOOK at the overlay.
- Its bench is lit by a single fixture, so it responds to the room differently from the wall-washed cameras. It is NOT wall-washed.

## Open / unmeasured

- A white-balance re-check AT Exposure -7 is owed: the 3540 pick was made at -8, and at -7 the ratios read R/G 0.950 / B/G 0.998, which is essentially neutral but leaves green fractionally on top (0.002). Below visibility, inside the measurement spread, but not confirmed.

## Last known good — the diff target

Copied verbatim from `BASELINE-2026-08-07-pm.json`. Re-seed with
`python tools/calibrate.py reseed --camera CAM8` after a deliberate change,
and record why in the history below.

```json
{
 "device": "USB Camera (USB/ProcAmp)",
 "procamp": {
  "Brightness": {
   "value": 0,
   "mode": "manual",
   "range": [
    -64,
    64,
    1
   ]
  },
  "Contrast": {
   "value": 32,
   "mode": "manual",
   "range": [
    0,
    64,
    1
   ]
  },
  "Hue": {
   "value": 0,
   "mode": "manual",
   "range": [
    -40,
    40,
    1
   ]
  },
  "Saturation": {
   "value": 56,
   "mode": "manual",
   "range": [
    0,
    128,
    1
   ]
  },
  "Sharpness": {
   "value": 3,
   "mode": "manual",
   "range": [
    0,
    6,
    1
   ]
  },
  "Gamma": {
   "value": 100,
   "mode": "manual",
   "range": [
    72,
    500,
    1
   ]
  },
  "WhiteBalance": {
   "value": 3540,
   "mode": "manual",
   "range": [
    2800,
    6500,
    1
   ]
  },
  "BacklightCompensation": {
   "value": 1,
   "mode": "manual",
   "range": [
    0,
    2,
    1
   ]
  },
  "Gain": {
   "value": 0,
   "mode": "manual",
   "range": [
    0,
    100,
    1
   ]
  }
 },
 "camctrl": {
  "Roll": {
   "value": 0,
   "mode": "manual",
   "range": [
    0,
    0,
    0
   ]
  },
  "Exposure": {
   "value": -7,
   "mode": "manual",
   "range": [
    -13,
    -1,
    1
   ]
  }
 }
}
```

## Validation history — APPEND ONLY, never overwrite

The user's standing rule. "Looked at it and kept it" is the evidence that a
value is still trusted, and it is lost if each entry replaces the last.

- **2026-07-26** — auto-WB beat manual by eye, twice. ⚠️ **In the pre-07-29 room.**
- **2026-08-07** — re-swept 2800–6500 up+down after the relight; **manual 3540 wins on both axes**. Exposure −7 from a −11..−5 up+down sweep. User: **"CAM8 looks great."**

### 2026-08-31 - MEASURED on the chart, and the driver gap CLOSED. No camera write.

**Driver fix.** `ProcAmpDriver`'s knob map is a hardcoded subset of what the device
reports, and it omitted two REAL knobs on this camera: `Gamma` (manual, range
72-500, default 100) and `Exposure` (manual, range -13..-1). So `read()` returned
`Gamma=100` while `set()` raised *"no knob named 'Gamma'"* -- and the driver could
not write this camera's exposure pin at all, even though Exp -7 is part of its own
last-known-good. Both are now declared via `extra_knobs` in `drivers.fleet()`, with
ranges read off `procamp report`, and both were verified to write and read back
(Gamma 100->120->100, Exposure -7->-8->-7). `Roll` stays excluded on purpose: its
range is [0, 0], a dead knob rather than a gap.

🔑 **A knob that READS is not a knob that WRITES.** Choose a knob by what the WRITER
accepts, never by `read()` or `capabilities()`.

**Chart measurement** (SpyderCheckr 24 left on the bench by the user, deliberately
tilted to kill glare; measured ON THE BI CLIP `CAM8.20260831_232434Z.mp4`, never a
live grab, via `chart_rectify --optimise --radius 60`):

    R/G 0.9112 (-8.9%)   B/G 1.0203 (+2.0%)
    grey luma 192.2   glare 0.0530   colour_sat 0.3236   patch_sd 0.67

Converged (corners moved <=6.8 px on the second pass, no radius warning) and the
overlay was inspected: all four grey boxes squarely inside their patches.
`patch_sd 0.67` is the tightest this camera has ever measured.

**Whole frame, same clip set: luma 131.47 / crush 1.77% / hot 0.19% /
blown-white 0.01%.** No crush and no blowout, so GAMMA HAS NOTHING TO FIX HERE --
it is newly writable, but there is no work for it. Left at 100.

**Nothing was written to the camera.** Versus the 2026-08-07 validation
(R/G 0.869 / B/G 1.026) this is ~4.2pp LESS red-deficient on R/G with B/G
unchanged. The residual red deficit is the known per-camera one that goes to the
edit tint, not to this camera -- CAM8 is -22% against CAM1 and does NOT share
CAM1+CAM4's single adjustment layer.

⚠️ Measured with a large lit monitor above the bench. The chart lies flat below it
rather than in front of it, and `glare 0.0530` is inside the known-good band, but
this is not the same illuminant geometry as the 08-07 pass -- **a chart number is
only valid for the light it was measured in.**
