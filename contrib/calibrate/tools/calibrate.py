"""The ONE camera-calibration entry point. Defaults to the whole fleet.

    python tools/calibrate.py check                      # all cameras vs reference
    python tools/calibrate.py check --camera CAM8
    python tools/calibrate.py show  --camera CAM7        # what is TRUE about it
    python tools/calibrate.py sweep --camera CAM8 --knob Exposure --values -11..-5
    python tools/calibrate.py score --manifest <f> --clip <f> --roi white=x0,y0,x1,y1
    python tools/calibrate.py reseed --camera CAM8       # adopt live as last-known-good

Replaces ~60 per-camera one-off scripts. They all re-implemented the same five
steps and each one re-earned the same bugs, which is why a single knob change
used to cost a session.

EVERY CAMERA IS IDIOSYNCRATIC, SO NOTHING HERE IS HARDCODED PER CAMERA.
Ranges, ceilings, settle times, dead knobs and last-known-good all come from
`reference/CAM*.md`, which is the git-versioned source of truth. Add a camera by
writing its reference file, not by editing this.

THE LESSONS THIS TOOL ENFORCES, ALL OF THEM PAID FOR
-----------------------------------------------------
* Measure the clip Blue Iris SAVED, never the camera and never a live snapshot.
  The clip is what the edit cuts, and Blue Iris re-encodes the USB cameras as
  BT.601 while the IP cameras tag BT.709.
* A read-back is the only evidence a write landed. Setters lie -- procamp prints
  the value it REPLACED, and Foscam's CGI zeroes a knob and returns success.
* Clip ceilings are not 255 (CAM7 224, CAM8 241). A pegged patch reads sd 0.00
  and R/G = B/G = 1.0000: a flawless FAKE neutral white balance.
* UP-then-DOWN is evidence; a longer settle is an assumption.
* A metric that cannot SEE a knob returns a confident wrong answer. Grey cannot
  detect a hue rotation, by definition.
* Perceived cast is set by WHICH CHANNEL IS HIGHEST, not total deviation.
* A colour ruling is only valid for the LIGHTING IT WAS MEASURED IN.
* Check crush BEFORE reaching for a tone curve: gamma transformed CAM7 at 22.5%
  black and did nothing on CAM4 at 0.04%.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import subprocess
import sys
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from calibrate import drivers, reference  # noqa: E402
from calibrate.site_paths import DURABLE  # noqa: E402  (a copy outside the repo)

API = "http://127.0.0.1:8787"
CAL = ROOT / "var" / "calibrate"

#: Which metric can actually SEE which knob. Enforced, not advisory.
NEUTRAL, TONE, CHROMA, HUEANGLE = "neutral", "tone", "chroma", "hueangle"
KNOB_METRIC = {
    "WhiteBalance": NEUTRAL, "Whitebalance": NEUTRAL,
    "GainRed": NEUTRAL, "GainBlue": NEUTRAL,
    "Exposure": TONE, "Compensation": TONE, "Gamma": TONE, "Contrast": TONE,
    "contrast": TONE, "Brightness": TONE, "brightness": TONE,
    "Gain": TONE, "WideDynamicRange": TONE,
    "Saturation": CHROMA, "saturation": CHROMA, "ColorSaturation": CHROMA,
    "Hue": HUEANGLE, "hue": HUEANGLE,
    "Sharpness": "acuity", "sharpness": "acuity",
}
IMPLEMENTED_METRICS = {TONE, NEUTRAL}


def api(path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        API + path, data=data, headers={"Content-Type": "application/json"},
        method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


# ------------------------------------------------------------------- check
def read_fleet_rich() -> dict:
    """Read every camera through snapshot_fleet_state.py, the RICH path.

    Deliberately NOT drivers.snapshot(). The driver snapshot omits exactly the
    knobs that matter most -- CAM8's Exposure lives on camctrl, not procamp, and
    the Dahua per-profile values are absent entirely -- so using it as a diff
    target would silently stop watching the settings this fleet actually changes.

    It also has to be the SAME reader that wrote the reference block. A diff
    across a schema change is not a diff of the data: on 2026-08-01 a snapshot
    format change reported "78 differences of 79 keys" and none of them were real.
    """
    out = CAL / "_check_live.json"
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "snapshot_fleet_state.py"),
                        str(out)], cwd=str(ROOT), capture_output=True, text=True)
    if not out.exists():
        raise RuntimeError(f"snapshot_fleet_state failed: "
                           f"{(r.stderr or r.stdout)[-400:]}")
    d = json.loads(out.read_text())
    return d.get("cameras", d)


def cmd_check(a) -> int:
    cams = [a.camera.upper()] if a.camera else reference.all_cameras()
    live_all = read_fleet_rich()
    worst = 0
    for cam in cams:
        ref = reference.load(cam)
        if cam not in live_all:
            print(f"{cam:6s} no control plane ({ref.transport}) - nothing to check")
            continue
        live = live_all[cam]
        if isinstance(live, dict) and "ERROR" in live:
            # Not every failure is the network. On 2026-08-08 a missing httpx in
            # the interpreter that happened to be on PATH printed
            # "CAM4 UNREACHABLE: ModuleNotFoundError", which reads as a camera
            # fault and sends you to the rig instead of to `.venv`. Name the
            # class of failure, do not translate every exception into "the
            # camera is down".
            err = str(live["ERROR"])
            kind = ("LOCAL ENV BROKEN (this interpreter, not the camera - "
                    "use .venv\\Scripts\\python.exe)"
                    if err.startswith(("ModuleNotFoundError", "ImportError",
                                       "FileNotFoundError"))
                    else "UNREACHABLE")
            print(f"{cam:6s} {kind}: {err}")
            worst = max(worst, 2)
            continue
        a_flat, b_flat = reference.flatten(ref.lkg), reference.flatten(live)
        keys = sorted(set(a_flat) | set(b_flat))
        diffs = [(k, a_flat.get(k, "<absent>"), b_flat.get(k, "<absent>"))
                 for k in keys if a_flat.get(k, "<absent>") != b_flat.get(k, "<absent>")]
        if not diffs:
            print(f"{cam:6s} IDENTICAL to reference ({len(keys)} keys)")
            continue
        worst = max(worst, 1)
        print(f"{cam:6s} {len(diffs)} DIFFERENT of {len(keys)} keys")
        for k, want, got in diffs[:20]:
            print(f"        {k:44s} reference={want!r}  live={got!r}")
        if len(diffs) > 20:
            print(f"        ... and {len(diffs)-20} more")
        print(f"        Every one needs an explanation: an intended write, or "
              f"drift. If intended, `reseed --camera {cam}` and append to the "
              f"history in {ref.path.name}.")
    return worst


# -------------------------------------------------------------------- show
def cmd_show(a) -> int:
    cams = [a.camera.upper()] if a.camera else reference.all_cameras()
    for cam in cams:
        print(reference.load(cam).text)
        print("\n" + "=" * 78 + "\n")
    return 0


# ------------------------------------------------------------------ reseed
def cmd_reseed(a) -> int:
    """Adopt live as last-known-good, read by the SAME reader `check` diffs with.

    2026-08-08: this used `drivers.fleet()[cam].snapshot()` while `check` used
    read_fleet_rich(), and the docstring on read_fleet_rich already said why the
    driver snapshot is unfit as a diff target. So reseed wrote exactly the thing
    check cannot use. On CAM4 -- whose driver read returns no colour block at all
    -- it replaced a 266-key reference with a 37-key one: 294 deletions, and the
    next `check` reported 310/310 different, every key `reference='<absent>'`.
    It was recoverable only because reference/ is git-tracked.

    A writer and its reader MUST be the same code path. If they are not, the
    tool's own output is what breaks it.
    """
    cam = a.camera.upper()
    ref = reference.load(cam)
    live_all = read_fleet_rich()
    if cam not in live_all:
        print(f"{cam} has no control plane; nothing to reseed.")
        return 2
    live = live_all[cam]
    if isinstance(live, dict) and "ERROR" in live:
        print(f"{cam} read FAILED: {live['ERROR']}\n"
              f"Refusing to reseed from a failed read -- that would write the "
              f"failure in as the last-known-good.")
        return 2

    # Round-trip guard: reseed is only correct if `check` is clean immediately
    # afterwards. Prove it here rather than trusting it, because the failure mode
    # above looked exactly like success at the moment of writing.
    a_flat = reference.flatten(live)
    new_block = json.dumps(live, indent=1)
    text = re.sub(r"(```json\s*\n).*?(\n```)",
                  lambda m: m.group(1) + new_block + m.group(2),
                  ref.text, count=1, flags=re.S)
    before_keys = len(reference.flatten(ref.lkg))
    ref.path.write_text(text, encoding="utf-8")

    # Re-load from disk and diff exactly as `check` will. If this is not clean,
    # the reseed corrupted the reference and says so instead of reporting success.
    fresh = reference.load(cam)
    b_flat = reference.flatten(fresh.lkg)
    keys = sorted(set(a_flat) | set(b_flat))
    bad = [k for k in keys
           if a_flat.get(k, "<absent>") != b_flat.get(k, "<absent>")]
    if bad:
        print(f"RESEED IS BAD -- wrote {ref.path.name} but a re-read disagrees on "
              f"{len(bad)} of {len(keys)} keys, e.g. {bad[:4]}.\n"
              f"Recover with:  git checkout -- {ref.path.relative_to(ROOT)}")
        return 1
    print(f"reseeded {ref.path.name} from live: {before_keys} keys -> {len(b_flat)}, "
          f"round-trip verified clean.")
    if len(b_flat) < before_keys * 0.9:
        print(f"WARNING: the new block is much SMALLER than the old one "
              f"({before_keys} -> {len(b_flat)}). That is what a narrowed reader "
              f"looks like. Inspect `git diff` before committing.")
    print("NOW APPEND to that file's Validation history saying WHY, and what "
          "the user saw. A value with no history is a value nobody has agreed to.")
    return 0


# ------------------------------------------------------------------- sweep
def parse_values(spec: str) -> list[int]:
    if ".." in spec:
        lo, hi = (int(x) for x in spec.split(".."))
        step = 1 if hi >= lo else -1
        return list(range(lo, hi + step, step))
    return [int(x) for x in spec.split(",") if x.strip()]


def cmd_sweep(a) -> int:
    cam = a.camera.upper()
    ref = reference.load(cam)
    settle = a.settle if a.settle is not None else ref.settle_s
    metric = KNOB_METRIC.get(a.knob)
    if metric is None:
        print(f"No metric is registered as able to SEE {a.knob!r}. Add one to "
              f"KNOB_METRIC with a note on why it can see it -- do not score it "
              f"with whatever is handy.")
        return 2

    values = parse_values(a.values)
    for v in values:
        msg = ref.check_value(a.knob, v)
        if msg and msg.startswith("WARN"):
            print(msg)
        elif msg:
            print(f"REFUSED: {msg}")
            return 2

    print(f"{cam}.{a.knob}  metric={metric.upper()}  settle={settle}s")
    print(f"ceiling: {ref.ceiling if ref.ceiling is not None else 'UNMEASURED - a patch near the top cannot be trusted'}")
    if metric not in IMPLEMENTED_METRICS:
        print(f"WARNING: {metric.upper()} scoring is NOT implemented. The sweep will "
              f"record honestly but `score` will refuse it rather than print "
              f"numbers blind to {a.knob}. Proceed only if you will judge by eye.")

    passes = [("up", values)] + ([] if a.no_updown else
                                 [("down", list(reversed(values))[1:])])
    total = sum(len(v) for _, v in passes)
    print(f"{total} points x {settle:.0f}s = ~{total*settle/60:.1f} min undisturbed\n")

    drv = drivers.fleet()[cam]
    stamp = time.strftime("%Y-%m-%d")
    roll = CAL / f"rollback-{cam}-{a.knob}-{stamp}.json"
    snap = drv.snapshot()
    CAL.mkdir(parents=True, exist_ok=True)
    roll.write_text(json.dumps(snap, indent=1))
    dest = DURABLE / stamp
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(roll, dest / roll.name)
    print(f"rollback -> {roll.name} (+ durable copy)")

    prev_armed = list(api("/api/state").get("armed") or [])
    man = {"camera": cam, "knob": a.knob, "metric": metric, "settle": settle,
           "ceiling": ref.ceiling, "rollback": str(roll), "points": []}
    stopped = False
    try:
        api("/api/arm/bulk", {"cameras": [cam]})
        rec = api("/api/record", {"action": "start"})
        if not rec.get("ok"):
            raise RuntimeError(f"recording did not start: {rec}")
        t0 = time.time()
        time.sleep(12.0)
        for phase, vals in passes:
            for v in vals:
                got, agreed = drv.verified_set(a.knob, v, allow_locked=a.allow_locked)
                if not agreed:
                    raise RuntimeError(f"{cam}.{a.knob}: asked {v}, device holds "
                                       f"{got}. A knob that will not take a value "
                                       f"is a finding - stopping.")
                t_set = time.time() - t0
                time.sleep(settle)
                man["points"].append({"phase": phase, "asked": v, "read": got,
                                      "t_set": round(t_set, 2),
                                      "t_end": round(time.time() - t0, 2)})
                print(f"  {phase:4s} {a.knob}={v:<7} confirmed {got}")
        api("/api/record", {"action": "stop"})
        stopped = True
    except BaseException as exc:
        print(f"\nABORTED: {type(exc).__name__}: {exc}")
        if not stopped:
            try:
                api("/api/record", {"action": "stop"})
            except Exception as e:
                print(f"  stop FAILED: {e}")
        try:
            api("/api/arm/bulk", {"cameras": prev_armed})
        except Exception as e:
            print(f"  arm restore FAILED: {e}")
        try:
            print(f"  restored, re-read: {drv.restore(snap, allow_locked=a.allow_locked)}")
        except Exception as e:
            print(f"  RESTORE FAILED - {cam} IS ON A PROBE VALUE: {e}")
        return 1

    api("/api/arm/bulk", {"cameras": prev_armed})
    out = CAL / f"sweep-{cam}-{a.knob}-{stamp}.json"
    out.write_text(json.dumps(man, indent=1))
    print(f"\nmanifest -> {out.name}")
    print(f"{cam}.{a.knob} left on {man['points'][-1]['read']} - a PROBE value.")
    return 0


# ------------------------------------------------------------------- score
def cmd_score(a) -> int:
    import numpy as np
    from calibrate.decode import DECODE_VF, FFMPEG, FFPROBE

    man = json.loads((CAL / a.manifest if not pathlib.Path(a.manifest).is_absolute()
                      else pathlib.Path(a.manifest)).read_text())
    metric, ceiling = man.get("metric", TONE), man.get("ceiling")
    if metric not in IMPLEMENTED_METRICS:
        print(f"REFUSING to score a {metric.upper()} sweep.\n"
              f"{man['camera']}.{man['knob']} needs {metric.upper()}, which is not "
              f"implemented. What this tool measures (luma, and R/G / B/G on "
              f"greys) is BLIND to it - a grey patch cannot see a hue rotation "
              f"or a saturation change, by definition. Index the chart's COLOUR "
              f"patches (tools/chart_rectify.py) first. UNSCORED, not passed.")
        return 3

    clip = pathlib.Path(a.clip)
    if not clip.exists():
        print(f"clip not found: {clip}\nTake folders get renamed by hand to "
              f"protect them from the recycler - re-list the directory.")
        return 2
    wh = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0",
                         "-show_entries", "stream=width,height", "-of",
                         "csv=p=0:s=x", str(clip)], capture_output=True, text=True)
    w, h = (int(x) for x in wh.stdout.strip().split("x"))
    dur = float(subprocess.run([FFPROBE, "-v", "error", "-show_entries",
                                "format=duration", "-of", "default=nw=1:nk=1",
                                str(clip)], capture_output=True,
                               text=True).stdout.strip())
    preroll = dur - man["points"][-1]["t_end"] - 1.5
    print(f"{clip.name} {w}x{h} {dur:.1f}s  pre-roll derived {preroll:.1f}s")
    print(f"{man['camera']}.{man['knob']} metric={metric} "
          f"ceiling={ceiling if ceiling is not None else 'UNMEASURED'}\n")

    rois = {}
    for spec in a.roi:
        nm, nums = spec.split("=", 1)
        rois[nm] = tuple(float(x) for x in nums.split(","))

    for nm, box in rois.items():
        print(f"-- ROI {nm} --")
        print(f"{'pass':>5s} {'value':>7s} {'luma':>8s} {'sd':>6s} {'max':>6s} "
              f"{'R/G':>7s} {'B/G':>7s} {'top':>6s}  gate")
        rows, sds = [], []
        for p in man["points"]:
            t = preroll + p["t_end"] - a.lead
            raw = subprocess.run(
                [FFMPEG, "-v", "error", "-ss", f"{t:.2f}", "-i", str(clip),
                 "-frames:v", "1", "-vf", DECODE_VF, "-f", "rawvideo",
                 "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
            if len(raw) != w * h * 3:
                print(f"{p['phase']:>5s} {p['read']:>7} -- no frame")
                continue
            img = np.frombuffer(raw, np.uint8).reshape(h, w, 3).astype(np.float32)
            x0, y0, x1, y1 = box
            b = img[int(y0*h):int(y1*h), int(x0*w):int(x1*w)]
            y = 0.299*b[..., 0] + 0.587*b[..., 1] + 0.114*b[..., 2]
            r, g, bl = b[..., 0].mean(), b[..., 1].mean(), b[..., 2].mean()
            m = {"luma": y.mean(), "sd": y.std(), "max": y.max(),
                 "rg": r/g if g else 0, "bg": bl/g if g else 0}
            sds.append(m["sd"])
            rows.append((p["phase"], p["read"], m))
        med = float(np.median(sds)) if sds else 0.0
        for phase, val, m in rows:
            gate = "ok"
            if ceiling is not None and m["max"] >= ceiling - 0.5:
                gate = "PEGGED - VOID (fake neutral)"
            elif m["sd"] < 0.3:
                gate = "FLAT - VOID"
            elif med and m["sd"] > max(4*med, med + 8):
                gate = "RAGGED - box moved?"
            top = ("GREEN" if m["rg"] < 1 and m["bg"] < 1
                   else "blue" if m["bg"] >= m["rg"] else "red")
            print(f"{phase:>5s} {val:>7} {m['luma']:8.2f} {m['sd']:6.2f} "
                  f"{m['max']:6.1f} {m['rg']:7.4f} {m['bg']:7.4f} {top:>6s}  {gate}")

        live = [(p, v, m) for p, v, m in rows
                if not (ceiling is not None and m["max"] >= ceiling - 0.5)]
        ups = {v: m for p, v, m in live if p == "up"}
        dns = {v: m for p, v, m in live if p == "down"}
        if metric == TONE and len(ups) > 2:
            seq = [ups[v]["luma"] for v in sorted(ups)]
            mono = (all(x < y for x, y in zip(seq, seq[1:])) or
                    all(x > y for x, y in zip(seq, seq[1:])))
            print("\n  CONTROL monotonic: " + " -> ".join(f"{v:.1f}" for v in seq)
                  + ("   OK" if mono else
                     "   NOT MONOTONIC - samples are off the plateaus, run is VOID"))
        if ups and dns:
            print("  CONTROL up vs down (live points only):")
            for v in sorted(set(ups) & set(dns)):
                print(f"    {v:>7}: luma {ups[v]['luma']-dns[v]['luma']:+6.2f}   "
                      f"R/G {ups[v]['rg']-dns[v]['rg']:+.4f}")
        if metric == NEUTRAL and live:
            print("\n  Rule 1 - keep the residual pointing at BLUE, not green.")
            print("  (The eye hunts green and forgives blue. Minimising total")
            print("   deviation picked CAM8's worst-looking setting TWICE.)")
            for p, v, m in live:
                if p != "up":
                    continue
                verdict = "eligible" if m["bg"] > 1.0 else "GREEN ON TOP - reject"
                print(f"    {v:>7}  R/G {m['rg']:.4f}  B/G {m['bg']:.4f}   {verdict}")
        print()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Camera calibration. Defaults to the whole fleet.",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="diff every camera against its reference")
    c.add_argument("--camera", help="default: ALL cameras")

    s = sub.add_parser("show", help="print what is TRUE about a camera")
    s.add_argument("--camera", help="default: ALL cameras")

    r = sub.add_parser("reseed", help="adopt live state as last-known-good")
    r.add_argument("--camera", required=True)

    w = sub.add_parser("sweep", help="sweep one knob, up then down")
    w.add_argument("--camera", required=True)
    w.add_argument("--knob", required=True)
    w.add_argument("--values", required=True, help="'-11..-5' or '0,5,10,15'")
    w.add_argument("--settle", type=float)
    w.add_argument("--no-updown", action="store_true")
    w.add_argument("--allow-locked", action="store_true")

    o = sub.add_parser("score", help="score a sweep from the saved clip")
    o.add_argument("--manifest", required=True)
    o.add_argument("--clip", required=True)
    o.add_argument("--roi", action="append", required=True,
                   help="name=x0,y0,x1,y1 NORMALISED 0..1")
    o.add_argument("--lead", type=float, default=8.0)

    a = ap.parse_args()
    return {"check": cmd_check, "show": cmd_show, "reseed": cmd_reseed,
            "sweep": cmd_sweep, "score": cmd_score}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
