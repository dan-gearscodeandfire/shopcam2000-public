"""CAM4 final pinned state, user's pick 2026-08-22: Gamma 11 at gain 8.

Same shape as CAM7's lock and for the same reason: `VideoInMode[0].Mode` accepts
only 0 on this firmware, so the camera picks its profile by light level forever.
Identical profiles make that harmless. Auto is killed by COLLAPSING THE RANGE
(GainMin == GainMax == AutoGainMax), not by a mode flip.

Difference from CAM7: gain was never CAM4's lever -- at gain 11 (hardware max 12)
crush was still 37%. GAMMA is this camera's cliff, 9 -> 11: crush 39.34% -> 0.72%.
"""
from calibrate import drivers

EXPOSURE = {"GainMin": "8", "GainMax": "8", "AutoGainMax": "8",
            "Value1": "16.670000", "Value2": "16.670000",
            "IrisAuto": "false", "Iris": "50", "WideDynamicRange": "0"}
COLOR = {"Gamma": "11", "Brightness": "50", "Contrast": "50",
         "Saturation": "50", "Hue": "50", "Style": "Standard"}
P = (0, 1, 2)


def _f(x):
    try:
        return round(float(x), 4)
    except (TypeError, ValueError):
        return None


cam = drivers.fleet()["CAM4"]._cam()
for p in P:
    cam.set_config({"VideoInExposure[0][%d].%s" % (p, k): v for k, v in EXPOSURE.items()})
    cam.set_config({"VideoInColor[0][%d].%s" % (p, k): v for k, v in COLOR.items()})
cam.set_config({"Lighting[0][0].Mode": "Off"})   # only profile 0 is exposed here

exp, col = cam.get_config("VideoInExposure"), cam.get_config("VideoInColor")
lig = cam.get_config("Lighting")
bad = 0
for p in P:
    for name, tbl, want in (("VideoInExposure", exp, EXPOSURE),
                            ("VideoInColor", col, COLOR)):
        for k, v in want.items():
            got = tbl.get("%s[0][%d].%s" % (name, p, k))
            ok = got is not None and (got.strip() == v or
                                      (_f(got) is not None and _f(got) == _f(v)))
            if not ok:
                print("  !! [%d] %s.%s: wanted %s got %r" % (p, name, k, v, got))
                bad += 1
if lig.get("Lighting[0][0].Mode") != "Off":
    print("  !! Lighting[0][0].Mode = %r" % lig.get("Lighting[0][0].Mode"))
    bad += 1
print("LOCK READ-BACK CLEAN" if not bad else "MISMATCHES: %d" % bad)
cam.close()
