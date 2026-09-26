"""Un-stick a camera that reports its imaging config but is not applying it.

After a power cycle CAM7 and CAM4 come back REPORTING their stored config while
not APPLYING it, so `calibrate.py check` reads IDENTICAL and the picture is dark
and crushed. A same-value rewrite is DISCARDED by the firmware -- which is why
the 2026-08-22 "reapply all 105 reference values" test measured a no-op and
wrongly retired the correct hypothesis.

The fix is to write a DIFFERENT value and then write the original back. The
value is irrelevant; the WRITE is what makes the ISP reload the curve.

usage: unstick.py            -> CAM7 and CAM4 (the known-affected pair)
       unstick.py CAM7       -> one camera
       unstick.py CAM2 CAM3  -> several

Restores exactly what it read, and verifies, so it cannot change a calibration
even if run on a healthy camera.

PROVEN on the Dahua pair, 2026-08-31 (`check` IDENTICAL before AND after both):
  CAM7 luma 37.27 -> 93.21  (crush 59.09% -> 0.21%)
  CAM4 luma 64.17 -> 92.89  (crush 37.04% -> 0.90%)

NOT YET PROVEN AS A CURE ON ANY OTHER CAMERA -- no non-Dahua camera has ever
been observed stuck. The generic path below performs the same nudge-and-restore
through each driver's own control plane; that it RUNS is verified, that it CURES
is not. Treat a non-Dahua run as a probe and record the result.
"""
import sys
import time

from calibrate import drivers
from calibrate.site_paths import CLIP_DIR

# The self-adapting Dahua pair. VideoInMode.Mode accepts ONLY 0, so the camera
# picks its profile by light level forever -- every knob must be written to all
# three profiles or the fix reverts whenever the room changes.
DAHUA = {"CAM4", "CAM7"}
TABLE, FIELD, PROFILES = "VideoInColor", "Gamma", (0, 1, 2)

# Preference order for the generic path: a tone knob first, since that is where
# the fault shows. Whatever the camera actually reports wins.
GENERIC_KNOBS = ("Gamma", "Brightness", "Contrast", "Saturation")


def nudged(v):
    """A DIFFERENT value. Which one does not matter; that it differs does."""
    return v - 2 if v >= 2 else v + 2


def key(p):
    return "%s[0][%d].%s" % (TABLE, p, FIELD)


def unstick_dahua(name):
    """Native path: nudge and restore Gamma on ALL THREE profiles."""
    cam = drivers.fleet()[name]._cam()
    try:
        cfg = cam.get_config(TABLE)
        orig = {p: str(cfg.get(key(p), "")).strip() for p in PROFILES}
        if not all(orig.values()):
            print("  %s !! could not read %s on all three profiles: %r"
                  % (name, FIELD, orig))
            return False
        print("  %s stored %s = %s (3 profiles)" % (name, FIELD, orig))
        try:
            for p in PROFILES:
                cam.set_config({key(p): str(nudged(int(orig[p])))})
            time.sleep(3.0)
        finally:
            # Restore even if the nudge half failed -- never leave it nudged.
            for p in PROFILES:
                cam.set_config({key(p): orig[p]})
        time.sleep(2.0)

        back = cam.get_config(TABLE)
        bad = [p for p in PROFILES
               if str(back.get(key(p), "")).strip() != orig[p]]
    finally:
        cam.close()

    if bad:
        print("  %s !! RESTORE FAILED on profiles %r -- READ THE CAMERA before "
              "describing its state" % (name, bad))
        return False
    print("  %s restored %s = %s: CLEAN" % (name, FIELD, orig))
    return True


def unstick_generic(name):
    """Same nudge-and-restore through whatever plane this camera exposes."""
    drv = drivers.fleet()[name]
    try:
        snap = drv.read()
    except Exception as e:
        print("  %s !! read failed (%s: %s) -- camera unreachable or parked"
              % (name, type(e).__name__, e))
        return False

    # A knob that READS is not necessarily a knob that WRITES -- CAM8 reports
    # Gamma=100 and its setter has never heard of it. The writer is the
    # authority, so try candidates until one actually accepts the nudge.
    knob = orig = None
    for cand in GENERIC_KNOBS:
        if cand not in snap:
            continue
        try:
            want = int(float(snap[cand]))
        except (TypeError, ValueError):
            continue
        try:
            _, ok = drv.verified_set(cand, nudged(want))
        except Exception as e:
            print("  %s    %s not writable (%s), trying the next knob"
                  % (name, cand, type(e).__name__))
            continue
        if not ok:
            print("  %s    %s did not land, trying the next knob" % (name, cand))
            continue
        knob, orig = cand, want
        break

    if knob is None:
        print("  %s !! no writable tone knob among %r; reads %s"
              % (name, GENERIC_KNOBS, ", ".join(sorted(snap))))
        return False

    print("  %s stored %s = %s  [generic plane: %s -- probe, not a proven cure]"
          % (name, knob, orig, type(drv).__name__))
    try:
        time.sleep(3.0)
    finally:
        # Only ever restores a knob the nudge actually landed on.
        drv.verified_set(knob, orig)
    time.sleep(2.0)

    back = drv.read()
    if int(float(back.get(knob, -999))) != orig:
        print("  %s !! RESTORE FAILED: %s reads %r, wanted %s -- READ THE "
              "CAMERA" % (name, knob, back.get(knob), orig))
        return False
    print("  %s restored %s = %s: CLEAN" % (name, knob, orig))
    return True


def main():
    args = [a.upper() for a in sys.argv[1:] if not a.startswith("-")]
    names = args or sorted(DAHUA)

    fleet = drivers.fleet()
    unknown = [n for n in names if n not in fleet]
    if unknown:
        print("  !! not in the fleet: %s (have: %s)"
              % (", ".join(unknown), ", ".join(sorted(fleet))))
        return 2

    ok = True
    for name in names:
        ok &= (unstick_dahua if name in DAHUA else unstick_generic)(name)

    print()
    print("  now VERIFY ON A CLIP (a live grab is never a verdict here):")
    print("    .venv\\Scripts\\python.exe tools\\calibrate_check.py --press "
          "--max-age-min 10 --reason \"unstick verify\"")
    for name in names:
        # Only CAM4 and CAM7 have a head-to-tail verifier; the rest use the
        # general single-clip measurement.
        tool = ("verify_%s_clip.py" % name.lower()) if name in DAHUA \
            else "measure_one.py"
        print("    .venv\\Scripts\\python.exe %s %s"
              % (tool, CLIP_DIR / ("%s.<newest>Z.mp4" % name)))
    print("  Compare against the fingerprint in reference/CAMn.md, then append "
          "to its Validation history.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
