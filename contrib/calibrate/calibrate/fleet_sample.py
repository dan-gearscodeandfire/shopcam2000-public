"""One sample = one press = every camera measured in the same instant.

WHY A WHOLE-FLEET SAMPLE IS THE UNIT
------------------------------------
A press writes one clip per camera covering the same wall-clock window, so a
single sample carries all nine cameras *plus* the reference camera's own reading
as an in-sample control. That is the difference between a comparison that is
legal and one that is not: every prior cross-camera result that survived
scrutiny had the reference camera in the same frames, and the ones that did not
were thrown away.

It also inverts the servo. Sampling costs ~70 s (a press must not tread on the
previous press's pre-roll recovery), so servoing cameras one at a time would
cost 70 s x cameras x iterations. Measuring the whole fleet per sample means one
iteration corrects *every* camera, and the wall clock is set by the iteration
count alone.

WHAT IS MEASURED, AND WHAT EACH READING IS GOOD FOR
---------------------------------------------------
``whole_frame``  Channel MEANS over the entire frame. The pixel population is
    fixed, so shifts track a real change with sub-count resolution -- the only
    signal trustworthy enough to servo against. Absolute values are scene-
    coloured, so these compare a camera to ITSELF, never to another camera.

``roi``  A named box on a surface that holds still. Cross-camera comparison is
    only defensible between boxes holding the same kind of surface at a
    comparable viewing angle, which is why each ROI records what it is sitting
    on and gets looked at before it is trusted.

Every reading is taken on several frames and reported with its **frame-to-frame
spread**, because a working shop drifts a few points between frames and a
"correction" smaller than that spread is not a measurement. The spread is
carried in the data structure rather than in a comment, so the report can
refuse to call such a difference a difference.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import pathlib

import numpy as np

from . import bi_clips, chartmath, decode, metrics

#: Frames per camera per sample. Five is enough to see the spread without
#: turning one sample into a minute of decoding.
FRAMES_PER_CLIP = 5

#: Frame-to-frame noise on a working shop, in percentage points of a channel
#: ratio. Recorded from repeat measurement, not chosen: identical settings have
#: read 19.7% then 24.6% crush, and chart R/G reproduces to ~0.4pp at best.
NOISE_FLOOR_PP = 2.3


@dataclasses.dataclass
class Reading:
    """One metric measured across several frames of one clip."""
    name: str
    r_g: float
    b_g: float
    luma: float
    crush_pct: float
    clip_pct: float
    n_frames: int
    #: p90-p10 across frames, in the metric's own units. The honesty budget.
    spread_r_g_pp: float
    spread_b_g_pp: float
    spread_luma: float
    note: str = ""

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class CameraSample:
    camera: str
    clip: str
    duration: float
    tags: str
    readings: dict[str, Reading] = dataclasses.field(default_factory=dict)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.readings)


@dataclasses.dataclass
class FleetSample:
    stamp: str
    taken_at: str
    reference: str
    decode_audit: dict
    cameras: dict[str, CameraSample]

    def as_dict(self) -> dict:
        return {
            "stamp": self.stamp, "taken_at": self.taken_at,
            "reference": self.reference, "decode_audit": self.decode_audit,
            "cameras": {
                c: {"clip": s.clip, "duration": s.duration, "tags": s.tags,
                    "error": s.error,
                    "readings": {n: r.as_dict() for n, r in s.readings.items()}}
                for c, s in self.cameras.items()},
        }

    def save(self, path: pathlib.Path) -> pathlib.Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.as_dict(), indent=2))
        return path


def _aggregate(name: str, per_frame: list[dict], note: str = "") -> Reading | None:
    """Median across frames, plus the p90-p10 spread that bounds belief."""
    rows = [f for f in per_frame if f and np.isfinite(f.get("rg", np.nan))]
    if not rows:
        return None
    rg = np.array([f["rg"] for f in rows], dtype=float)
    bg = np.array([f["bg"] for f in rows], dtype=float)
    ly = np.array([f["luma"] for f in rows], dtype=float)
    spread = lambda a: float(np.percentile(a, 90) - np.percentile(a, 10))
    return Reading(
        name=name,
        r_g=float(np.median(rg)), b_g=float(np.median(bg)),
        luma=float(np.median(ly)),
        crush_pct=float(np.median([f.get("crush", np.nan) for f in rows])),
        clip_pct=float(np.median([f.get("clip", np.nan) for f in rows])),
        n_frames=len(rows),
        spread_r_g_pp=spread(rg) * 100,
        spread_b_g_pp=spread(bg) * 100,
        spread_luma=spread(ly),
        note=note,
    )


def _whole_frame(im: np.ndarray) -> dict:
    m = metrics.global_means(im)
    Y = 0.299 * im[..., 0] + 0.587 * im[..., 1] + 0.114 * im[..., 2]
    return dict(rg=m.r_g, bg=m.b_g, luma=m.luma,
                crush=float((Y < 16).mean() * 100), clip=m.clip_hi)


def _roi(im: np.ndarray, box: tuple[int, int, int, int]) -> dict | None:
    """A named box, measured with the cast-safe estimator and no sat filter.

    ``grey_only=True`` deliberately: a saturation mask rejects a cast camera's
    own greys and biases the measured cast toward zero -- it once reported
    R/G 0.92 where the truth was 0.78. The box is asserted to be on a neutral
    surface by whoever authored it, and looked at, rather than inferred from
    the pixels it contains.
    """
    x0, y0, x1, y1 = box
    win = im[y0:y1, x0:x1]
    if win.size == 0:
        return None
    out = chartmath.grey_ratios(win, grey_only=True)
    if out is None:
        return None
    Y = 0.299 * win[..., 0] + 0.587 * win[..., 1] + 0.114 * win[..., 2]
    out["crush"] = float((Y < 16).mean() * 100)
    return out


def take(clip_set: bi_clips.ClipSet,
         work_dir: pathlib.Path,
         rois: dict[str, dict[str, tuple[int, int, int, int]]] | None = None,
         reference: str = "CAM1",
         frames: int = FRAMES_PER_CLIP,
         baseline_tags: dict[str, str] | None = None,
         keep_frames: bool = True) -> FleetSample:
    """Measure every camera in ``clip_set`` under the fleet decode convention."""
    rois = rois or {}
    frame_dir = work_dir / "frames" / clip_set.label
    audit = decode.audit({c: k.path for c, k in clip_set.usable.items()},
                         baseline=baseline_tags)

    cameras: dict[str, CameraSample] = {}
    for cam in bi_clips.ALL_CAMERAS:
        clip = clip_set.clips.get(cam)
        if clip is None:
            cameras[cam] = CameraSample(cam, "-", 0.0, "-",
                                        error="no clip in this set")
            continue
        if not clip.ok:
            cameras[cam] = CameraSample(cam, clip.path.name, clip.duration or 0.0,
                                        clip.tags.key() if clip.tags else "-",
                                        error=clip.error)
            continue

        sample = CameraSample(cam, clip.path.name, clip.duration or 0.0,
                              clip.tags.key() if clip.tags else "-")
        whole: list[dict] = []
        per_roi: dict[str, list[dict]] = {n: [] for n in rois.get(cam, {})}
        try:
            for i, t in enumerate(clip.sample_times(frames)):
                out = frame_dir / f"{cam}-{i}-t{t:.0f}s.png"
                im = decode.extract_frame(clip.path, t, out)
                whole.append(_whole_frame(im))
                for name, box in rois.get(cam, {}).items():
                    r = _roi(im, box)
                    if r:
                        per_roi[name].append(r)
                if not keep_frames:
                    out.unlink(missing_ok=True)
        except Exception as e:                      # noqa: BLE001
            sample.error = f"{type(e).__name__}: {e}"[:200]

        wf = _aggregate("whole_frame", whole,
                        note="same-camera comparison only; scene-coloured")
        if wf:
            sample.readings["whole_frame"] = wf
        for name, rows in per_roi.items():
            r = _aggregate(f"roi:{name}", rows)
            if r:
                sample.readings[f"roi:{name}"] = r
            else:
                sample.error = (sample.error or "") + \
                    f" [roi {name} produced no measurement]"
        cameras[cam] = sample

    return FleetSample(
        stamp=clip_set.label,
        taken_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        reference=reference, decode_audit=audit, cameras=cameras)


# ---------------------------------------------------------------- reporting


def _pp(a: float, b: float) -> float:
    """Difference of two ratios in percentage points of the reference."""
    return (a / b - 1.0) * 100 if b else float("nan")


def divergence_report(sample: FleetSample, metric: str = "whole_frame") -> str:
    """Human-readable divergence against the reference camera.

    Deliberately refuses to call a difference smaller than the combined
    frame-to-frame spread a difference. That refusal is the whole point: three
    separate sessions were spent chasing deltas that later turned out to be
    inside the noise, and one of them wrote a value to a camera.
    """
    ref = sample.cameras.get(sample.reference)
    lines: list[str] = []
    a = sample.decode_audit
    lines.append(f"clip set {sample.stamp}   reference {sample.reference}   "
                 f"metric {metric}")
    lines.append("")
    lines.append("DECODE CONTRACT")
    lines.append(f"  filter: {a['decode_vf']}")
    if a["untagged_range"]:
        lines.append(f"  untagged range (decoded as full by assertion): "
                     f"{', '.join(a['untagged_range'])}")
    if a["untagged_unexpected"]:
        lines.append(f"  !! UNEXPECTED untagged camera(s): "
                     f"{', '.join(a['untagged_unexpected'])} -- a stream config "
                     f"changed; re-run the coded-range check before trusting "
                     f"anything below")
    if a["matrix_split"]:
        lines.append(f"  !! fleet spans {len(a['matrices_in_play'])} colour "
                     f"matrices: {', '.join(a['matrices_in_play'])} -- part of "
                     f"any B/G difference below is the decoder, not the camera")
    if a["tags_changed"]:
        for cam, ch in a["tags_changed"].items():
            lines.append(f"  !! {cam} tags CHANGED since the reference was "
                         f"captured: {ch['was']} -> {ch['now']}")
    lines.append(f"  edit note: {a['edit_note']}")
    lines.append("")

    if ref is None or metric not in ref.readings:
        lines.append(f"NO REFERENCE READING -- {sample.reference} has no "
                     f"'{metric}' measurement, so nothing can be matched to it.")
        for cam, s in sample.cameras.items():
            if s.error:
                lines.append(f"  {cam}: {s.error}")
        return "\n".join(lines)

    r = ref.readings[metric]
    lines.append(f"REFERENCE {sample.reference}: R/G {r.r_g:.4f}  B/G {r.b_g:.4f}  "
                 f"luma {r.luma:6.2f}  crush {r.crush_pct:5.2f}%  "
                 f"clip {r.clip_pct:.2f}%")
    lines.append(f"  own frame-to-frame spread: R/G {r.spread_r_g_pp:.2f}pp  "
                 f"B/G {r.spread_b_g_pp:.2f}pp  luma {r.spread_luma:.2f}")
    lines.append("")
    lines.append(f"{'cam':5s} {'R/G':>8s} {'vs ref':>9s} {'B/G':>8s} {'vs ref':>9s} "
                 f"{'luma':>7s} {'vs ref':>9s} {'crush':>7s}  verdict")

    for cam in bi_clips.SCENE_CAMERAS:
        s = sample.cameras.get(cam)
        if s is None or s.error and metric not in (s.readings or {}):
            lines.append(f"{cam:5s} {'-':>8s} {'-':>9s} {'-':>8s} {'-':>9s} "
                         f"{'-':>7s} {'-':>9s} {'-':>7s}  "
                         f"UNMEASURED: {s.error if s else 'absent'}")
            continue
        m = s.readings.get(metric)
        if m is None:
            lines.append(f"{cam:5s} no '{metric}' reading")
            continue
        if cam == sample.reference:
            lines.append(f"{cam:5s} {m.r_g:8.4f} {'reference':>9s} {m.b_g:8.4f} "
                         f"{'reference':>9s} {m.luma:7.2f} {'reference':>9s} "
                         f"{m.crush_pct:6.2f}%  the target")
            continue

        d_rg, d_bg = _pp(m.r_g, r.r_g), _pp(m.b_g, r.b_g)
        d_ly = m.luma - r.luma
        # Belief budget: this camera's spread plus the reference's.
        budget_rg = m.spread_r_g_pp + r.spread_r_g_pp
        budget_bg = m.spread_b_g_pp + r.spread_b_g_pp
        real = []
        if abs(d_rg) > max(budget_rg, NOISE_FLOOR_PP):
            real.append(f"R/G {d_rg:+.1f}pp")
        if abs(d_bg) > max(budget_bg, NOISE_FLOOR_PP):
            real.append(f"B/G {d_bg:+.1f}pp")
        verdict = ", ".join(real) if real else \
            f"inside noise (budget R/G {budget_rg:.1f}pp, B/G {budget_bg:.1f}pp)"
        lines.append(f"{cam:5s} {m.r_g:8.4f} {d_rg:+8.2f}pp {m.b_g:8.4f} "
                     f"{d_bg:+8.2f}pp {m.luma:7.2f} {d_ly:+8.2f}  "
                     f"{m.crush_pct:6.2f}%  {verdict}")

    if metric == "whole_frame":
        lines.append("")
        lines.append("!! whole_frame compares SCENE CONTENT, not colour response: "
                     "these cameras point at different parts of the room, so the "
                     "absolute gaps above are not corrections. Use whole_frame to "
                     "track ONE camera across a write; use a common reference "
                     "surface to match cameras to each other.")
    return "\n".join(lines)


def shared_surface_report(sample: FleetSample,
                          noise_floor_pp: float = NOISE_FLOOR_PP) -> str:
    """Cross-camera comparison restricted to cameras seeing the SAME object.

    This is the only cross-camera number in the system that is defensible, and
    it is deliberately narrow. Two cameras may be compared when a named region
    on both sits on one physical surface -- the same cabinet door, the same
    keyboard. Comparing a box on white paint against a box on plywood measures
    the paint and the plywood, not the cameras.

    Even here the comparison is only *approximate*, and the reason is on the
    record: cross-camera chroma is comparable only at comparable viewing angles.
    CAM8 sees the keyboard flat-on from 60 cm; CAM1 sees it across the room at a
    grazing angle through a different amount of specular veil. A grazing view
    once read red chroma 0.248 against a face-on 0.882 on the same physical
    card. So a gap here is EVIDENCE that a camera needs moving, not a number to
    servo blindly to zero -- and the printed angle warning is part of the
    output, not a footnote.
    """
    ref = sample.reference
    by_surface: dict[str, list[tuple[str, Reading]]] = {}
    for cam, s in sample.cameras.items():
        for name, reading in s.readings.items():
            if name.startswith("roi:"):
                by_surface.setdefault(name[4:], []).append((cam, reading))

    lines = ["", "SHARED-SURFACE MATCH (the only legal cross-camera comparison)",
             f"  reference {ref}; a surface is only listed when 2+ cameras see it"]
    if not by_surface:
        lines.append("  no ROI readings in this sample -- author regions first")
        return "\n".join(lines)

    any_shared = False
    for surface, entries in sorted(by_surface.items()):
        if len(entries) < 2:
            solo = entries[0][0]
            lines.append("")
            lines.append(f"  {surface}: only {solo} sees it -- cannot be used to "
                         f"match anything")
            continue
        ref_entry = next((e for e in entries if e[0] == ref), None)
        lines.append("")
        lines.append(f"  surface '{surface}' seen by "
                     f"{', '.join(c for c, _ in entries)}")
        if ref_entry is None:
            lines.append(f"    !! {ref} does not see it, so this surface cannot "
                         f"anchor a match to the reference. Useful only for "
                         f"comparing these cameras to each other.")
        any_shared = True
        base = ref_entry[1] if ref_entry else entries[0][1]
        base_cam = ref_entry[0] if ref_entry else entries[0][0]
        lines.append(f"    {'cam':6s} {'R/G':>8s} {'vs ' + base_cam:>11s} "
                     f"{'B/G':>8s} {'vs ' + base_cam:>11s} {'luma':>7s} {'spread':>8s}")
        for cam, r in sorted(entries):
            if cam == base_cam:
                lines.append(f"    {cam:6s} {r.r_g:8.4f} {'anchor':>11s} "
                             f"{r.b_g:8.4f} {'anchor':>11s} {r.luma:7.2f} "
                             f"{r.spread_r_g_pp:7.2f}pp")
                continue
            d_rg, d_bg = _pp(r.r_g, base.r_g), _pp(r.b_g, base.b_g)
            budget = r.spread_r_g_pp + base.spread_r_g_pp
            flag = "" if abs(d_rg) > max(budget, noise_floor_pp) else "  (in noise)"
            lines.append(f"    {cam:6s} {r.r_g:8.4f} {d_rg:+10.2f}pp "
                         f"{r.b_g:8.4f} {d_bg:+10.2f}pp {r.luma:7.2f} "
                         f"{r.spread_r_g_pp:7.2f}pp{flag}")

    if any_shared:
        lines.append("")
        lines.append("  !! VIEWING ANGLE: these boxes are on one physical surface "
                     "but not at one angle. A gap of a few pp may be specular "
                     "veil rather than white balance -- confirm on a second "
                     "shared surface before writing to a camera.")
    return "\n".join(lines)
