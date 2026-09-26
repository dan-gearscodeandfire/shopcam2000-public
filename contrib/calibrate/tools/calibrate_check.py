"""Measure the fleet from what Blue Iris saved. Changes nothing.

    python tools/calibrate_check.py                    # measure the newest clip set
    python tools/calibrate_check.py --press            # take a fresh sample first
    python tools/calibrate_check.py --press --repeat 2 # two samples -> real noise floor
    python tools/calibrate_check.py --overlay          # also render the ROI overlays

This is the dry run, and it is also the only honest way to find out whether a
correction is needed at all: three separate sessions here were spent chasing
differences that later turned out to be inside the noise, and one of them wrote
a value to a camera. So the report refuses to call anything a difference until
it clears the measured spread.

``--repeat`` is the strongest version of that guard. Frame-to-frame spread
within one clip understates the real noise, because everything slow -- AE
hunting, a cloud, someone's LED strip -- is common to all frames of one press.
Two presses a minute apart expose that drift, and the between-press difference
on an untouched camera IS the noise floor for the session.

⚠️ Presses are serialised behind the Controller and each one drains the pre-roll
buffer, so this waits ~80 s between them. That is the pre-roll recovery law
(full recovery 71 s), not conservatism.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys
import time
import tomllib
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from calibrate import bi_clips, decode, fleet_sample, rois  # noqa: E402

VAR = ROOT / "var" / "calibrate"
ROI_FILE = VAR / "rois.json"
BASELINE_TAGS = VAR / "decode-baseline.json"

#: Seconds to wait after a press before looking for the clips. The Controller
#: verifies its own clips ~16 s after a press, so they exist by then; the extra
#: margin covers a slow flush on the biggest cameras.
CLIP_APPEAR_S = 25.0

#: Gap between presses. Full pre-roll recovery is 71 s.
PRESS_GAP_S = 80.0


def _controller_base() -> str:
    from calibrate.site_paths import load_config
    cfg = load_config()
    return f"http://127.0.0.1:{cfg['server']['port']}"


def press(base: str, reason: str) -> dict:
    """Fire the That Was Awesome button and return the Controller's receipt.

    The receipt is the record of how much lead-in each camera actually caught.
    It has been wrong before -- three simultaneous presses once produced three
    "full 60 s buffer" receipts for one merged clip -- so it is printed rather
    than trusted, and the clips are measured regardless of what it says.
    """
    req = urllib.request.Request(
        base + "/api/twab",
        data=json.dumps({"source": f"calibrate_check: {reason}"}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as f:
        return json.load(f)


def summarise_receipt(receipt: dict) -> str:
    if not isinstance(receipt, dict):
        return f"receipt: {receipt!r}"
    cams = receipt.get("cameras") or receipt.get("results") or []
    if isinstance(cams, dict):
        cams = [{"camera": k, **(v if isinstance(v, dict) else {"value": v})}
                for k, v in cams.items()]
    if not cams:
        return f"receipt: {json.dumps(receipt)[:300]}"
    bits = []
    for c in cams:
        name = c.get("camera") or c.get("name") or "?"
        pre = c.get("preroll") or c.get("preroll_seconds") or c.get("lead_in")
        bits.append(f"{name}:{pre}" if pre is not None else str(name))
    return "receipt pre-roll -> " + "  ".join(bits)


def take_one(label: str, work_dir: pathlib.Path, regions, frames: int,
             baseline_tags: dict | None, max_age_s: float | None) -> fleet_sample.FleetSample:
    clip_set = bi_clips.newest_usable_set(work_dir=work_dir, max_age_s=max_age_s)
    print(f"\n{clip_set.summary()}")
    if clip_set.failures:
        print("\n  !! unusable in this set:")
        for cam, why in clip_set.failures.items():
            print(f"     {cam}: {why}")
    # Per-camera frame size, off the clips themselves. The fleet is no longer
    # uniformly 1080p (CAM7 is 2560x1440) and a fleet-wide default would put
    # its boxes in the wrong part of the picture, invisibly.
    sizes = rois.clip_sizes(clip_set)
    for cam, (w, h) in sorted(sizes.items()):
        if (w, h) != (1920, 1080):
            print(f"  note: {cam} is {w}x{h}; ROIs scaled to it")
    boxes = rois.as_boxes(regions, sizes)
    sample = fleet_sample.take(clip_set, work_dir=work_dir, rois=boxes,
                               frames=frames, baseline_tags=baseline_tags)
    out = sample.save(work_dir / "samples" / f"{clip_set.label}.json")
    print(f"\n  sample saved -> {out}")
    return sample


def compare_samples(a: fleet_sample.FleetSample, b: fleet_sample.FleetSample,
                    metric: str) -> str:
    """Between-press drift on cameras nothing was written to = the noise floor.

    Whatever shows up here is what the room and the sensors do on their own.
    Any correction smaller than this is not a correction.
    """
    lines = ["", "BETWEEN-PRESS DRIFT (nothing was written between these two "
             "samples, so this IS the session's noise floor)",
             f"  {a.stamp} -> {b.stamp}", "",
             f"{'cam':5s} {'d R/G':>9s} {'d B/G':>9s} {'d luma':>9s}   metric {metric}"]
    worst = 0.0
    for cam in bi_clips.SCENE_CAMERAS:
        ra = (a.cameras.get(cam).readings.get(metric)
              if a.cameras.get(cam) else None)
        rb = (b.cameras.get(cam).readings.get(metric)
              if b.cameras.get(cam) else None)
        if not ra or not rb:
            lines.append(f"{cam:5s} {'-':>9s} {'-':>9s} {'-':>9s}   not measured in both")
            continue
        d_rg = (rb.r_g / ra.r_g - 1) * 100
        d_bg = (rb.b_g / ra.b_g - 1) * 100
        worst = max(worst, abs(d_rg), abs(d_bg))
        lines.append(f"{cam:5s} {d_rg:+8.2f}pp {d_bg:+8.2f}pp {rb.luma - ra.luma:+8.2f}")
    lines.append("")
    lines.append(f"  worst channel-ratio drift with nothing touched: {worst:.2f}pp "
                 f"-- treat that as the floor, not {fleet_sample.NOISE_FLOOR_PP}pp, "
                 f"if it is larger")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--press", action="store_true",
                    help="fire the TWAB button to capture a fresh sample")
    ap.add_argument("--repeat", type=int, default=1,
                    help="number of presses; >1 measures the real noise floor")
    ap.add_argument("--frames", type=int, default=fleet_sample.FRAMES_PER_CLIP)
    ap.add_argument("--metric", default="whole_frame",
                    help="whole_frame, or roi:<name> once ROIs are authored")
    ap.add_argument("--overlay", action="store_true",
                    help="render ROI overlays so the boxes get looked at")
    ap.add_argument("--max-age-min", type=float, default=None,
                    help="refuse a clip set older than this (guards against "
                         "measuring stale footage when a press failed)")
    ap.add_argument("--reason", default="calibration check")
    args = ap.parse_args()

    VAR.mkdir(parents=True, exist_ok=True)
    regions = rois.load(ROI_FILE)
    baseline = json.loads(BASELINE_TAGS.read_text()) if BASELINE_TAGS.exists() else None
    max_age = args.max_age_min * 60 if args.max_age_min else None

    samples: list[fleet_sample.FleetSample] = []
    for i in range(max(1, args.repeat)):
        if args.press:
            if i:
                print(f"\nwaiting {PRESS_GAP_S:.0f}s for pre-roll recovery "
                      f"before press {i + 1}...")
                time.sleep(PRESS_GAP_S)
            print(f"\npress {i + 1}/{args.repeat} ...")
            try:
                print("  " + summarise_receipt(press(_controller_base(), args.reason)))
            except urllib.error.URLError as e:
                print(f"  !! press failed: {e} -- is the Controller running?")
                sys.exit(2)
            time.sleep(CLIP_APPEAR_S)
        samples.append(take_one(f"sample{i}", VAR, regions, args.frames,
                                baseline, max_age))

    print()
    print("=" * 78)
    print(fleet_sample.divergence_report(samples[-1], metric=args.metric))

    print(fleet_sample.shared_surface_report(samples[-1]))

    if len(samples) > 1:
        print(compare_samples(samples[0], samples[-1], args.metric))

    if args.overlay:
        print("\nROI OVERLAYS (look at these before believing any roi: number)")
        last = samples[-1]
        clip_set = bi_clips.newest_usable_set(work_dir=VAR, do_settle=False)
        for cam in bi_clips.SCENE_CAMERAS:
            clip = clip_set.clips.get(cam)
            if not clip or not clip.ok or cam not in regions:
                continue
            im = decode.extract_frame(clip.path, clip.sample_times(1)[0],
                                      VAR / "overlay" / f"raw-{cam}.png")
            out = rois.draw_overlay(im, regions[cam],
                                   VAR / "overlay" / f"{cam}.jpg", scale=0.667)
            print(f"\n  {cam} -> {out}")
            for row in rois.check_regions(im, regions[cam]):
                extra = (f" R/G {row['r_g']:.3f} B/G {row['b_g']:.3f}"
                         if "r_g" in row else "")
                print(f"    {row['name']:12s} luma {row.get('luma', 0):6.1f} "
                      f"flat {row.get('flatness', 0):5.1f}{extra}  {row['verdict']}")

    # Record the decode signatures so a later run can detect a stream change.
    if not BASELINE_TAGS.exists():
        BASELINE_TAGS.write_text(json.dumps(
            {"captured": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
             **samples[-1].decode_audit["signatures"]}, indent=2))
        print(f"\ndecode baseline written -> {BASELINE_TAGS}")


if __name__ == "__main__":
    main()
