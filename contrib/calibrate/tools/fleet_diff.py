"""Diff EVERY imaging setting on EVERY camera against a known-good snapshot.

User instruction, 2026-08-01: *"check all available settings for a camera and
make sure they correspond with last known good working configuration, and do
that anytime a camera/image debugging problem comes up."*

This is the first step of any image debugging on this fleet, before measuring
anything. Spot-checking the two or three knobs you suspect is how a changed
setting hides: today CAM8's Exposure was found at -8 against a recorded -6 only
because every key was compared, not the ones under suspicion.

Compares two `snapshot_fleet_state.py` outputs key by key, recursively, and
reports every difference plus any key or camera that appears or disappears.

    python tools/fleet_diff.py OLD.json NEW.json
    python tools/fleet_diff.py OLD.json --live      # snapshot now, then diff

A DIFF ACROSS A SCHEMA CHANGE IS NOT A DIFF OF THE DATA
-------------------------------------------------------
`snapshot_fleet_state.py` used to store a bare value per key::

    "brightness": 45

and now stores the value together with its mode and range::

    "brightness": {"value": 45, "mode": "manual", "range": "0-100"}

That was a deliberate improvement -- CAM8's white balance is AUTO by design, and
an auto/manual flip has to be visible -- but it renames every leaf key. Diffing
a new snapshot against an older one therefore reported *78 of 79 keys differ* on
CAM5 when nothing whatsoever had changed.

A drift detector that manufactures drift is worse than no drift detector, so
this tool now **detects the schema mismatch and normalises both sides to plain
values** before comparing, and says loudly that it did. When both snapshots use
the same schema, mode and range are compared too -- that is the whole point of
recording them.

BEFORE YOU BELIEVE AN ALL-CLEAR, RUN THE SELFTEST
-------------------------------------------------
`0 CHANGED key(s) across the fleet` is exactly what this tool would print if
`--live` never reached a camera. Run `tools/fleet_diff_selftest.py` to plant
known faults in a COPY of the baseline and prove the detector goes red. It
touches no hardware.

TWO THINGS THAT WILL BITE YOU
-----------------------------
1. `--live` writes its fresh snapshot to LIVE_OUT below -- which is
   `FLEET-LAST-KNOWN-GOOD.json`, the file whose *name* asserts it is good. Run
   `--live` twice and "last known good" has quietly become "last read". Diff
   against a dated `BASELINE-*.json` and back LIVE_OUT up if it matters.
2. The snapshot is MIXED-SCHEMA. Only the DirectShow/ProcAmp cameras (CAM1,
   CAM5, CAM8) wrap leaves as {value, mode, range}; CAM2/3/4/6/7 store bare
   strings. The "both snapshots carry mode/range" banner is a per-FILE verdict
   and reads as fleet-wide when it is not. Anything you write that treats only
   {"value": ...} dicts as leaves will silently skip five of the eight cameras.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
LIVE_OUT = str(ROOT / "var" / "calibrate" / "FLEET-LAST-KNOWN-GOOD.json")

# A "wrapped" leaf is exactly what snapshot_fleet_state.py's `keep()` emits.
# Requiring the key set to be a SUBSET of these (not merely to contain "value")
# keeps a real config section that happens to have a "value" child from being
# collapsed by accident.
WRAPPER_KEYS = {"value", "mode", "range"}


def is_wrapped(o) -> bool:
    return isinstance(o, dict) and "value" in o and set(o) <= WRAPPER_KEYS


def has_wrapped(o) -> bool:
    """True if anywhere in this tree a leaf uses the {value,mode,range} form."""
    if is_wrapped(o):
        return True
    if isinstance(o, dict):
        return any(has_wrapped(v) for v in o.values())
    if isinstance(o, list):
        return any(has_wrapped(v) for v in o)
    return False


def unwrap(o):
    """Collapse every {value,mode,range} leaf to its bare value, recursively."""
    if is_wrapped(o):
        return o["value"]
    if isinstance(o, dict):
        return {k: unwrap(v) for k, v in o.items()}
    if isinstance(o, list):
        return [unwrap(v) for v in o]
    return o


def flatten(o, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    if isinstance(o, dict):
        for k, v in o.items():
            out.update(flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(o, list):
        for i, v in enumerate(o):
            out.update(flatten(v, f"{prefix}[{i}]"))
    else:
        out[prefix] = str(o)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("old")
    ap.add_argument("new", nargs="?", default="")
    ap.add_argument("--live", action="store_true",
                    help="re-run snapshot_fleet_state.py first and diff against that")
    ap.add_argument("--values-only", action="store_true",
                    help="compare bare values even when both snapshots carry "
                         "mode/range; use to silence an expected mode flip")
    ap.add_argument("--show-coverage", action="store_true",
                    help="list every key that appeared/vanished between the two "
                         "snapshots, not just a count")
    a = ap.parse_args()

    if a.live:
        print("re-reading every device live...")
        subprocess.run([sys.executable,
                        str(ROOT / "tools" / "snapshot_fleet_state.py")],
                       cwd=str(ROOT), check=True,
                       capture_output=True, text=True)
        new_path = LIVE_OUT
    else:
        new_path = a.new or LIVE_OUT

    old = json.loads(pathlib.Path(a.old).read_text())["cameras"]
    new = json.loads(pathlib.Path(new_path).read_text())["cameras"]
    print(f"OLD {a.old}\nNEW {new_path}\n")

    # Normalise BEFORE comparing, or a schema change reads as total drift.
    old_w, new_w = has_wrapped(old), has_wrapped(new)
    if old_w != new_w:
        bare, wrapped = ("OLD", "NEW") if new_w else ("NEW", "OLD")
        print("!! SCHEMA MISMATCH - this is not a data difference.")
        print(f"!! {wrapped} stores {{value,mode,range}} per key; {bare} stores bare values.")
        print("!! Comparing VALUES ONLY. Mode and range are NOT checked this run;")
        print("!! an auto/manual flip would be invisible here. Re-snapshot the")
        print("!! baseline to compare them.\n")
        old, new = unwrap(old), unwrap(new)
    elif a.values_only:
        print("-- --values-only: comparing bare values; mode/range not checked.\n")
        old, new = unwrap(old), unwrap(new)
    elif old_w:
        print("-- both snapshots carry mode/range; comparing those too.\n")

    only_old = sorted(set(old) - set(new))
    only_new = sorted(set(new) - set(old))
    if only_old:
        print(f"!! cameras MISSING from new snapshot: {only_old}")
    if only_new:
        print(f"!! cameras NEW in this snapshot: {only_new}")

    total_changed = total_coverage = 0
    for cam in sorted(set(old) & set(new)):
        fo, fn = flatten(old[cam]), flatten(new[cam])
        keys = sorted(set(fo) | set(fn))
        # A key whose VALUE moved is drift. A key that merely appeared or
        # vanished is the snapshot's coverage changing. Mixing them buries the
        # first under the second: comparing 07-26 to 08-02 put CAM4's five real
        # changes under 150 lines of newly-captured profile keys.
        changed = [(k, fo[k], fn[k]) for k in keys
                   if k in fo and k in fn and fo[k] != fn[k]]
        added = [k for k in keys if k not in fo]
        removed = [k for k in keys if k not in fn]

        if not changed and not added and not removed:
            print(f"{cam}: IDENTICAL ({len(fn)} keys checked)")
            continue

        total_changed += len(changed)
        total_coverage += len(added) + len(removed)

        if changed:
            print(f"{cam}: {len(changed)} CHANGED of {len(keys)} keys")
            for k, o, n in changed:
                print(f"      {k:44} {o!s:>14} -> {n}")
        else:
            print(f"{cam}: no value changes ({len(keys)} keys)")

        if added or removed:
            bits = []
            if added:
                bits.append(f"{len(added)} key(s) newly captured")
            if removed:
                bits.append(f"{len(removed)} key(s) no longer captured")
            print(f"    ({'; '.join(bits)} - snapshot coverage, not drift"
                  f"{'' if a.show_coverage else '; --show-coverage to list'})")
            if a.show_coverage:
                for k in added:
                    print(f"    + {k:44} (new) = {fn[k]}")
                for k in removed:
                    print(f"    - {k:44} was {fo[k]}, now ABSENT")

    print(f"\n{total_changed} CHANGED key(s) across the fleet.")
    print("Every one needs an explanation: an intended write, or drift to chase.")
    if total_coverage:
        print(f"{total_coverage} key(s) differ only in whether the snapshot "
              f"captured them. That is not drift.")


if __name__ == "__main__":
    main()
