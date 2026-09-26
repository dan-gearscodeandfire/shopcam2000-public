# Calibration toolkit, as run on the reference rig

This is the toolkit that set, checked and recorded every image setting on the
reference rig's eight scene cameras, plus the settings themselves. It is copied
from the working rig with machine names, addresses and credentials removed.
It is not packaged: it runs from this folder, the way it runs on the rig.

**The measurements are the valuable part.** The code moves knobs and reads them
back. The `reference/` files say which knobs lie, which ranges are real, what
each camera's clip ceiling is, and what the user saw and approved, with dates.

## What is here

| Path | What it is |
|---|---|
| `reference/CAM1.md` … `CAM9.md` | **The per-camera source of truth.** Each file has the knob ranges and how each was established (probed, device-reported, or merely assumed), the knobs that read back fine and do nothing, the findings, the append-only validation history, and a fenced JSON block holding that camera's **last-known-good settings**. `tools/calibrate.py check` diffs the live camera against that block. |
| `baselines/BASELINE-2026-08-07-pm.json` | A full live read of the whole fleet on 2026-08-07, the rollback point the reference files were seeded from. CAM4 and CAM7 were re-pinned on 2026-08-22, so for those two the JSON block in `reference/CAMn.md` is current and this file is history. |
| `calibrate/` | The library. `drivers.py` puts one interface over five vendor control planes (DirectShow ProcAmp for USB cameras, Dahua/Amcrest CGI, ONVIF Imaging, XiongMai "Sofia" DVRIP, Foscam CGI) and verifies every write with a live read-back. `dahua.py`, `onvif_imaging.py`, `sofia.py` are the protocol clients; `remote/procamp.py` is the UVC helper the ProcAmp driver runs as a subprocess. `decode.py`, `metrics.py`, `bi_clips.py`, `fleet_sample.py`, `rois.py`, `chartmath.py` measure pixels from the clips Blue Iris saved. `reference.py` parses the reference files. `site_paths.py` is the one addition for this public copy (see below). |
| `tools/calibrate.py` | **The one entry point.** `check` (live vs reference, all cameras by default), `show` (what is true about a camera), `sweep` (one knob, up then down, inside one continuous recording), `score` (score a sweep from the saved clip; refuses to score a knob its metric cannot see), `reseed` (adopt live as last-known-good, round-trip verified). |
| `tools/snapshot_fleet_state.py` | The rich live read of every camera. `check` and `reseed` both use it, deliberately: a writer and its reader must be the same code path. |
| `tools/fleet_diff.py`, `tools/fleet_diff_selftest.py` | Diff two snapshots (or one against live). The selftest plants known faults in a copy of a baseline and proves the differ still goes red. Run it before believing an all-clear. |
| `tools/calibrate_check.py` | **The pixel tool.** Measures the fleet from what Blue Iris saved, under one explicit decode contract, and refuses to call anything a difference until it clears the measured noise. `--press` fires the record button for a fresh clip set. |
| `tools/grab_fleet.py`, `cam_snapshot.py`, `frame_metrics.py`, `edge_acuity.py`, `focus_meter.py`, `iris_meter.py`, `chart_rectify.py`, `yrange_probe.py`, `decode_probe.py` | Triage: live stills, luma/clipping/white-balance ratios, sharpness, live meters for turning a focus or iris ring by hand, colour-chart patch indexing, and two probes that separate "the camera" from "the decode path". Live stills are for framing and focus, never for a colour verdict. |
| `unstick.py` | Recovers a power-cycled Dahua camera that reports its stored settings but is not applying them (so `check` says identical while the picture is dark and crushed). Writes a different value, then the original back. |
| `lock_cam4.py`, `lock_cam7.py` | How two self-adapting Amcrests were made deterministic: there is no manual switch, so auto is killed by collapsing each range (min = max) on all three profiles. `lock_cam7.py` holds the first of that night's values; `reference/CAM7.md` holds the final ones. |
| `measure_one.py`, `verify_cam4_clip.py`, `verify_cam7_clip.py` | Measure one saved clip, head to tail. Measure the tail: the head of a triggered clip is pre-roll, so it shows the state *before* the change. |

## Running it

Install the Python dependencies (`pip install -r requirements.txt`, plus the
`shopcam2000` package for the four tools that go through Blue Iris), then run
from this folder:

```
python tools/calibrate.py check
python tools/calibrate.py show --camera CAM7
python tools/fleet_diff_selftest.py
```

Camera addresses and logins come from the `[cameras]` section of `config.toml`,
found the same way the Controller finds it (`SHOPCAM_HOME`, or the current
folder). `src/shopcam2000/config.example.toml` has every key, with documentation
addresses and `changeme` passwords. `calibrate/site_paths.py` lists the other
environment variables (ffmpeg location, Blue Iris clip folder, the Python used
for USB cameras, where rollback copies go). On the rig those were literal paths.

## Adapting it to your cameras

The fleet in `drivers.fleet()` and `tools/snapshot_fleet_state.py` is this
rig's: camera names CAM1 to CAM9, with each one's model and protocol. To use
it on yours, edit those two tables and write a `reference/CAMn.md` per camera by
copying the closest existing one. Nothing else is hardcoded per camera; ranges,
ceilings, settle times, dead knobs and last-known-good all come from the
reference files.

Two things will be wrong until you measure them, and the files say so rather
than guessing: any range marked **assumed**, and any clip ceiling marked
**UNMEASURED**. Treat both as tasks.

## Not included

Dozens of one-off scripts from individual sessions (single-knob ladders,
per-night experiments, Blue Iris registry probes) stayed on the rig. Some
reference entries mention them by name (`cam7_knob.py`, for example). The
method they used is in the entry; the script is not needed to follow it.
