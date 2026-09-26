"""Prove `fleet_diff.py` can actually SEE a change, before you trust it saying there is none.

WHY THIS EXISTS
---------------
On 2026-08-02 `fleet_diff.py --live` was run for the first time since the
snapshot schema changed, and reported 0 changed keys across all 8 cameras.

That number is worthless on its own. It is byte-for-byte the same output the
tool would print if `--live` never reached the cameras, or diffed a file against
itself. The sibling of "suspect the instrument when it reports catastrophe" is
**suspect the instrument when it reports all-clear.** The whole reason this
project has a drift detector is that a broken one is indistinguishable from a
working one until you make it go red on purpose.

So: plant known faults in a COPY of the baseline, diff live against that, and
require the tool to find exactly them. **The fault goes in the reference, never
in the hardware** -- no camera is touched, so this is safe to run against a
calibrated fleet at any time.

WHAT IT PLANTS
--------------
Four faults, each guarding a specific way the tool has failed or could fail:

  1. a value change on a ProcAmp camera   -- the ordinary drift case
  2. a mode flip on a ProcAmp camera      -- the reason mode/range was recorded
                                             at all (CAM8's WB is AUTO by design)
  3. a value change on an HTTP camera     -- guards the MIXED-SCHEMA trap: the
                                             ProcAmp cams store {value,mode,range}
                                             but CAM2/3/4/6/7 store bare strings,
                                             and a leaf-detector that only knows
                                             the wrapped form skips five of eight
  4. a deleted subtree                    -- must be reported as COVERAGE, not
                                             drift; conflating the two is what
                                             produced "316 differences" in an earlier version

Usage:
    .venv\\Scripts\\python.exe tools\\fleet_diff_selftest.py
    .venv\\Scripts\\python.exe tools\\fleet_diff_selftest.py baselines\\BASELINE-2026-08-07-pm.json

Exit code 0 = the detector demonstrably works. Non-zero = do not trust its
all-clear until you know why.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_BASELINE = ROOT / "baselines" / "BASELINE-2026-08-07-pm.json"
CONTROL = ROOT / "var" / "calibrate" / "SELFTEST-planted-faults.json"
FLEET_DIFF = ROOT / "tools" / "fleet_diff.py"

# fleet_diff --live writes its fresh snapshot here -- i.e. straight onto the file
# whose name asserts it is good. Back it up and put it back.
LIVE_OUT = ROOT / "var" / "calibrate" / "FLEET-LAST-KNOWN-GOOD.json"


def leaves(node, path=()):
    """Yield (container, key, path, is_wrapped) for every scalar leaf.

    Handles BOTH snapshot schemas: the ProcAmp cameras wrap values as
    {'value':50,'mode':'manual','range':[...]}, the HTTP cameras store '50'.
    """
    if isinstance(node, dict):
        if "value" in node and not isinstance(node.get("value"), (dict, list)):
            yield node, path, True
            return
        for k, v in list(node.items()):
            if isinstance(v, (dict, list)):
                yield from leaves(v, path + (k,))
            else:
                yield node, path + (k,), False


def plant(baseline: pathlib.Path) -> tuple[list[dict], str]:
    data = json.loads(baseline.read_text(encoding="utf-8"))
    cams = data["cameras"]
    planted: list[dict] = []

    def numeric_leaves(cam, wrapped):
        out = []
        for container, path, is_wrapped in leaves(cams[cam]):
            if is_wrapped != wrapped:
                continue
            raw = container["value"] if is_wrapped else container[path[-1]]
            try:
                float(raw)
            except (TypeError, ValueError):
                continue
            out.append((container, path, is_wrapped))
        return out

    wrapped_cams = [c for c in sorted(cams) if numeric_leaves(c, True)]
    bare_cams = [c for c in sorted(cams) if numeric_leaves(c, False)]
    if not wrapped_cams or not bare_cams:
        sys.exit("baseline does not contain both snapshot schemas -- cannot test the mixed-schema path")

    # 1. value change on a ProcAmp camera
    cam = wrapped_cams[0]
    container, path, _ = numeric_leaves(cam, True)[0]
    old = container["value"]
    container["value"] = (int(old) + 7) if isinstance(old, int) else f"{float(old) + 7}"
    planted.append({"cam": cam, "key": ".".join(path) + ".value",
                    "what": f"value {old} -> {container['value']}", "coverage": False})

    # 2. mode flip on a ProcAmp camera
    for c in wrapped_cams:
        hit = [(n, p) for n, p, w in leaves(cams[c]) if w and n.get("mode")]
        if hit:
            node, path = hit[0]
            planted.append({"cam": c, "key": ".".join(path) + ".mode",
                            "what": f"mode {node['mode']} -> SELFTEST_MODE", "coverage": False})
            node["mode"] = "SELFTEST_MODE"
            break

    # 3. value change on a bare-string (HTTP) camera -- the mixed-schema guard
    cam = bare_cams[0]
    container, path, _ = numeric_leaves(cam, False)[0]
    key = path[-1]
    old = container[key]
    container[key] = str(int(float(old)) + 7)
    planted.append({"cam": cam, "key": ".".join(path),
                    "what": f"value {old} -> {container[key]}", "coverage": False})

    # 4. delete a subtree -- must land in COVERAGE, not drift.
    #    It must NOT be a subtree already holding a planted fault: deleting the
    #    branch those live on removes them from the comparison entirely and the
    #    selftest then fails itself rather than the tool.
    occupied = {(p["cam"], p["key"].split(".")[0]) for p in planted}
    for c in sorted(cams):
        subtrees = [k for k, v in cams[c].items()
                    if isinstance(v, dict) and v and k != "device"
                    and (c, k) not in occupied]
        if subtrees:
            victim = subtrees[0]
            del cams[c][victim]
            planted.append({"cam": c, "key": victim, "what": "subtree deleted", "coverage": True})
            break
    else:
        sys.exit("no free subtree to delete -- cannot test the coverage path")

    CONTROL.parent.mkdir(parents=True, exist_ok=True)
    CONTROL.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return planted, str(CONTROL)


def parse_blocks(out: str) -> dict[str, dict]:
    """Split fleet_diff output into per-camera {'changed': [...], 'coverage': [...]}"""
    blocks: dict[str, dict] = {}
    cur = None
    for line in out.splitlines():
        stripped = line.strip()
        if stripped[:3].upper() == "CAM" and ":" in stripped:
            cur = stripped.split(":", 1)[0].strip()
            blocks[cur] = {"changed": [], "coverage": []}
            continue
        if cur is None or not stripped:
            continue
        if stripped.startswith("+") or stripped.startswith("-") and "(new)" in stripped:
            blocks[cur]["coverage"].append(stripped.lstrip("+- ").strip())
        elif "->" in stripped:
            blocks[cur]["changed"].append(stripped)
    return blocks


def main() -> int:
    baseline = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_BASELINE
    if not baseline.exists():
        sys.exit(f"no such baseline: {baseline}")

    planted, control = plant(baseline)
    print(f"planted {len(planted)} fault(s) into a COPY of {baseline.name}:")
    for p in planted:
        tag = "coverage" if p["coverage"] else "drift"
        print(f"  [{tag:8}] {p['cam']}.{p['key']}  -- {p['what']}")
    print()

    backup = LIVE_OUT.with_suffix(".selftest-backup.json") if LIVE_OUT.exists() else None
    if backup:
        shutil.copy2(LIVE_OUT, backup)

    try:
        proc = subprocess.run(
            [sys.executable, str(FLEET_DIFF), control, "--live", "--show-coverage"],
            capture_output=True, text=True,
        )
        out = proc.stdout + proc.stderr
    finally:
        if backup:
            shutil.copy2(backup, LIVE_OUT)
            backup.unlink()
        CONTROL.unlink(missing_ok=True)

    print(out)
    blocks = parse_blocks(out)

    failures = []
    for p in planted:
        block = blocks.get(p["cam"])
        if block is None:
            failures.append(f"{p['cam']} absent from the diff entirely")
            continue
        bucket = "coverage" if p["coverage"] else "changed"
        if not any(p["key"] in line for line in block[bucket]):
            failures.append(
                f"{p['cam']}.{p['key']} was planted as {bucket} but the tool did not report it there"
            )
        wrong = "changed" if p["coverage"] else "coverage"
        if any(p["key"] in line for line in block[wrong]):
            failures.append(f"{p['cam']}.{p['key']} was misclassified as {wrong}")

    print("=" * 64)
    if failures:
        print("SELFTEST FAILED -- the drift detector did NOT find what was planted:")
        for f in failures:
            print("  " + f)
        print("\nDo not trust an all-clear from fleet_diff until this passes.")
        return 1

    print(f"SELFTEST PASSED -- all {len(planted)} planted fault(s) found and correctly classified.")
    unplanted = {
        cam: [c for c in b["changed"]
              if not any(p["cam"] == cam and p["key"] in c for p in planted)]
        for cam, b in blocks.items()
    }
    extra = {c: v for c, v in unplanted.items() if v}
    if extra:
        print("\nNOTE: the tool also reported changes nobody planted. That is REAL DRIFT:")
        for cam, lines in extra.items():
            for line in lines:
                print(f"  {cam}: {line}")
    else:
        print("No unplanted changes -- the live fleet matches the baseline.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
