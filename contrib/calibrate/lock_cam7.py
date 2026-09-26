"""Make CAM7 DETERMINISTIC. User ruling 2026-08-22: "We WANT deterministic. No auto settings."

This firmware has no manual switch to flip -- `VideoInExposure.Mode` is INERT
(all six values accepted, none does anything) and `VideoInMode[0].Mode` accepts
ONLY 0, so the camera will keep choosing its profile by light level forever.

So auto is killed a different way: COLLAPSE THE RANGES. A shutter with
Value1 == Value2 cannot float. A gain with GainMin == GainMax == AutoGainMax
cannot ramp. The loop still runs; it has nowhere to go. And every value goes to
ALL THREE PROFILES, so whichever profile the camera picks is the same picture.

NOTE (public copy): the values below are the FIRST lock of 2026-08-22. Three
later rulings the same night superseded them - the camera now runs gain 7,
Compensation 58, Contrast 45, Gamma 15, Saturation 38. `reference/CAM7.md` is
the source of truth; this script is kept for its method (collapse the range),
not its numbers.
"""
from calibrate import drivers


def _close(got: str, want: str) -> bool:
    try:
        return abs(float(got) - float(want)) < 1e-6
    except (TypeError, ValueError):
        return False


EXPOSURE = {
    "Compensation": "58",      # measured 2026-08-22: 55 -> 58 took crush 51.98% -> 4.02%
    "Value1": "16.670000",     # shutter pinned: floor == ceiling
    "Value2": "16.670000",
    "GainMin": "6",            # gain pinned: AE measured never to ask for more than 6
    "GainMax": "6",
    "AutoGainMax": "6",
    "IrisAuto": "false",       # no auto iris (fixed lens; pinned regardless)
    "Iris": "50",
}
PROFILES = (0, 1, 2)

cam = drivers.fleet()["CAM7"]._cam()
for p in PROFILES:
    cam.set_config({f"VideoInExposure[0][{p}].{k}": v for k, v in EXPOSURE.items()})
for p in PROFILES:
    cam.set_config({f"Lighting[0][{p}].Mode": "Off"})

live = cam.get_config("VideoInExposure")
lig = cam.get_config("Lighting")
bad = 0
for p in PROFILES:
    for k, want in EXPOSURE.items():
        got = live.get(f"VideoInExposure[0][{p}].{k}")
        same = got is not None and (got.strip() == want or
                                    _close(got, want))
        if not same:
            print(f"  !! [{p}] {k}: wanted {want} got {got!r}")
            bad += 1
    m = lig.get(f"Lighting[0][{p}].Mode")
    if m != "Off":
        print(f"  !! [{p}] Lighting.Mode = {m!r}")
        bad += 1
print("LOCK READ-BACK CLEAN" if not bad else f"LOCK MISMATCHES: {bad}")
cam.close()
