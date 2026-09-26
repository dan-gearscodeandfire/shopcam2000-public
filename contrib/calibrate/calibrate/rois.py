"""Named sample regions, and the overlay that proves each one is on what it claims.

Every measurement disaster on this rig has the same shape: a plausible number
from a box that was not where anybody thought. A "forehead" box that had slid
onto hair read luma 89.5. A reused box landed on an ear and a wood shelf and
nearly inverted a verdict. A grey box slid onto the colour patches and produced
an entire fake white-balance sweep at a believable flatness. None of those were
caught by a number; all of them were caught by looking at a picture.

So a region here is never just four coordinates. It carries what surface it is
supposed to be sitting on, which other cameras see that same physical surface,
and which clip set it was authored against -- and there is a renderer whose only
job is to draw the boxes so a human (or an agent with eyes) confirms them before
any value is quoted.

COORDINATES ARE NORMALISED (0..1)
--------------------------------
Fractions of frame width/height, not pixels, so a region survives a resolution
change and can be authored on a downscaled preview. They do NOT survive a
framing change: CAM1's gimbal is moved freely by the user and CAM6 is a roamer
with no fixed position at all, so both need re-authoring whenever they move.
That is why ``authored_on`` exists -- a region measured against a clip set older
than the last framing change is stale, and stale is indistinguishable from
correct if nobody looks.

WHAT THESE ARE GOOD FOR, AND WHAT THEY ARE NOT
----------------------------------------------
Good for: tracking ONE camera across a write, and for a cheap "has anything
drifted?" sweep between full passes. That is same-camera work and it is sound.

Not good for: declaring two cameras matched. These boxes sit on different
surfaces at wildly different viewing angles -- CAM8 sees the keyboard flat-on
from above while CAM1 sees it across the room at a grazing angle -- and
cross-camera chroma is only comparable at comparable viewing angles. For a real
match the fleet needs ONE portable neutral shown to each camera; ``shared_with``
records where a genuine common surface happens to exist so those pairs can be
cross-checked, not so a whole fleet can be matched on them.
"""
from __future__ import annotations

import dataclasses
import json
import pathlib

import numpy as np
from PIL import Image, ImageDraw

from . import chartmath


@dataclasses.dataclass
class Region:
    name: str
    box: tuple[float, float, float, float]   # x0, y0, x1, y1 as fractions
    surface: str                              # what it is sitting on
    shared_with: tuple[str, ...] = ()         # cameras seeing the SAME object
    authored_on: str = ""                     # clip set label
    note: str = ""

    def pixels(self, width: int, height: int) -> tuple[int, int, int, int]:
        x0, y0, x1, y1 = self.box
        return (int(round(x0 * width)), int(round(y0 * height)),
                int(round(x1 * width)), int(round(y1 * height)))

    def as_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["box"] = list(self.box)
        d["shared_with"] = list(self.shared_with)
        return d


#: Seed regions, authored 2026-07-29 against clip set 20260729_034628Z -- the
#: most recent set with the shop lit. Every one of these is a CANDIDATE until
#: the overlay has been looked at; `check_regions` prints what to look for.
#:
#: ⚠️ Authored against footage that also appears to carry the coloured wall
#: wash, so these boxes are geometry only. Their VALUES mean nothing until
#: re-measured under the shooting lighting with the accents in a known state.
SEED: dict[str, list[Region]] = {
    "CAM1": [
        Region("cabinet", (0.330, 0.660, 0.400, 0.750), "painted white cabinet door",
               ("CAM2", "CAM4", "CAM7"), "20260729_034628Z",
               "large flat paint; avoid the handle and the raised panel edge"),
        Region("keyboard", (0.648, 0.535, 0.706, 0.566), "white Apple keyboard",
               ("CAM2", "CAM6", "CAM8"), "20260729_034628Z",
               "confirmed on the keyboard by overlay; high flatness is key "
               "texture, not misplacement. Grazing angle -- weakest CAM1 box"),
        # A 'bench' box was authored here and DROPPED after the overlay showed it
        # sitting across tools and paper rather than clean plywood. CAM1 has a
        # good cabinet box and does not need a second, worse one.
    ],
    "CAM2": [
        # Moved 2026-07-29 after the overlay: the original top-left box
        # (0.047,0.014,0.188,0.097) straddled the shadow gap between two doors
        # AND a red cable -- flatness 48.8, 8.6% crushed. This run of doors is
        # clean.
        Region("cabinet", (0.640, 0.010, 0.780, 0.080), "painted white cabinet door",
               ("CAM1", "CAM4", "CAM7"), "20260729_034628Z",
               "re-placed after overlay review"),
        Region("keyboard", (0.324, 0.164, 0.391, 0.215), "white Apple keyboard",
               ("CAM1", "CAM6", "CAM8"), "20260729_034628Z", ""),
    ],
    "CAM3": [
        Region("macbook", (0.898, 0.278, 0.996, 0.444), "aluminium MacBook lid",
               ("CAM1", "CAM5"), "20260729_034628Z",
               "anodised aluminium, near-neutral but slightly cool"),
    ],
    "CAM4": [
        Region("cabinet", (0.789, 0.417, 0.852, 0.542), "painted white cabinet door",
               ("CAM1", "CAM2", "CAM7"), "20260729_034628Z", ""),
        Region("boxfan", (0.789, 0.202, 0.860, 0.355), "grey box fan housing",
               ("CAM1", "CAM2"), "20260729_034628Z", "mid-grey plastic"),
    ],
    "CAM5": [
        Region("whitewall", (0.437, 0.125, 0.484, 0.194), "white painted wall panel",
               (), "20260729_034628Z",
               "no confirmed shared surface with the reference -- CAM5 needs the "
               "portable neutral to be matched honestly"),
    ],
    "CAM6": [
        Region("keyboard", (0.250, 0.486, 0.375, 0.722), "white Apple keyboard",
               ("CAM1", "CAM2", "CAM8"), "20260729_034628Z",
               "CAM6 is the roamer -- this box is stale the moment it is moved"),
    ],
    "CAM7": [
        Region("garagedoor", (0.625, 0.056, 0.680, 0.208), "white garage door panel",
               (), "20260729_034628Z",
               "avoid the blue tape cross to the right; this side of the room "
               "takes the wall wash, so accents must be OFF to use it"),
    ],
    "CAM8": [
        Region("keyboard", (0.234, 0.792, 0.625, 0.972), "white Apple keyboard",
               ("CAM1", "CAM2", "CAM6"), "20260729_034628Z",
               "large and flat-on -- the strongest neutral in the fleet"),
        Region("bench", (0.300, 0.560, 0.520, 0.700), "plywood bench top",
               ("CAM1", "CAM2", "CAM4", "CAM6"), "20260729_034628Z",
               "NOT neutral -- warm wood. Same-camera tracking only"),
    ],
}

BOX_COLOURS = ((255, 64, 64), (64, 255, 64), (64, 160, 255), (255, 220, 0))


def load(path: pathlib.Path) -> dict[str, list[Region]]:
    """Regions from disk, falling back to the seed when none are authored yet."""
    if not path.exists():
        return {c: list(rs) for c, rs in SEED.items()}
    raw = json.loads(path.read_text())
    return {cam: [Region(name=r["name"], box=tuple(r["box"]), surface=r["surface"],
                         shared_with=tuple(r.get("shared_with", ())),
                         authored_on=r.get("authored_on", ""),
                         note=r.get("note", ""))
                  for r in rs]
            for cam, rs in raw.items()}


def save(regions: dict[str, list[Region]], path: pathlib.Path) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({c: [r.as_dict() for r in rs]
                                for c, rs in regions.items()}, indent=2))
    return path


def as_boxes(regions: dict[str, list[Region]],
             sizes: dict[str, tuple[int, int]] | None = None,
             width: int = 1920,
             height: int = 1080) -> dict[str, dict[str, tuple[int, int, int, int]]]:
    """Pixel boxes in the shape ``fleet_sample.take`` expects.

    ``sizes`` maps camera -> (width, height) and MUST be supplied for a fleet
    that is not uniformly 1080p. Regions are stored normalised precisely so they
    can follow the frame; the fleet-wide default is only a fallback for callers
    that genuinely have no clip to measure.

    ..  warning::
        CAM7 records 2560x1440. Converting its regions at 1920x1080 puts every
        box in the upper-left ~56% of the frame, and nothing downstream can tell
        - ``draw_overlay`` uses the real frame shape, so the picture looks right
        while the numbers come from somewhere else.
    """
    sizes = sizes or {}
    out = {}
    for cam, rs in regions.items():
        w, h = sizes.get(cam, (width, height))
        out[cam] = {r.name: r.pixels(w, h) for r in rs}
    return out


def clip_sizes(clip_set) -> dict[str, tuple[int, int]]:
    """Each camera's real coded frame size, read off the clip itself.

    A read-back is a claim; the file is the evidence. Cameras whose clip cannot
    be probed are simply absent, so ``as_boxes`` falls back for them rather than
    silently inventing a size.
    """
    import subprocess

    from .site_paths import FFPROBE as ffprobe
    sizes: dict[str, tuple[int, int]] = {}
    for cam, clip in getattr(clip_set, "clips", {}).items():
        path = getattr(clip, "path", None)
        if not path:
            continue
        try:
            r = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0",
                                "-show_entries", "stream=width,height",
                                "-of", "csv=p=0:s=x", str(path)],
                               capture_output=True, text=True, timeout=60)
            w, h = r.stdout.strip().split("x")[:2]
            sizes[cam] = (int(w), int(h))
        except Exception:
            continue
    return sizes


def draw_overlay(frame: np.ndarray, regions: list[Region],
                 out: pathlib.Path, scale: float = 1.0) -> pathlib.Path:
    """Render the frame with every region boxed and labelled.

    This is not a nicety. It is the step that has caught every bad box, and it
    is cheap enough that there is no excuse for quoting a number without it.
    """
    h, w = frame.shape[:2]
    im = Image.fromarray(frame.astype(np.uint8))
    d = ImageDraw.Draw(im)
    for i, r in enumerate(regions):
        x0, y0, x1, y1 = r.pixels(w, h)
        colour = BOX_COLOURS[i % len(BOX_COLOURS)]
        d.rectangle([x0, y0, x1, y1], outline=colour, width=4)
        label = f"{r.name} ({r.surface})"
        ty = y0 - 18 if y0 > 20 else y1 + 4
        d.rectangle([x0, ty, x0 + 9 * len(label), ty + 16], fill=(0, 0, 0))
        d.text((x0 + 3, ty + 3), label, fill=colour)
    if scale != 1.0:
        im = im.resize((int(w * scale), int(h * scale)))
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out)
    return out


def check_regions(frame: np.ndarray, regions: list[Region]) -> list[dict]:
    """Per-region evidence that a box is usable, before it is believed.

    ``flatness`` is the mean per-channel standard deviation inside the box. A
    box on one flat surface reads low; a box straddling an edge or holding
    clutter reads high, and that is the signal that it is not on what it says.
    ``raw_sat`` says how far from neutral the surface renders on THIS camera --
    which is the thing being measured, so a high value is not automatically a
    bad box, only a reason to look.
    """
    h, w = frame.shape[:2]
    out = []
    for r in regions:
        x0, y0, x1, y1 = r.pixels(w, h)
        win = frame[y0:y1, x0:x1]
        row: dict = {"name": r.name, "surface": r.surface,
                     "px": f"{x1 - x0}x{y1 - y0}", "n_px": int(win.size // 3)}
        if win.size == 0:
            row["verdict"] = "EMPTY -- box is outside the frame"
            out.append(row)
            continue
        Y = 0.299 * win[..., 0] + 0.587 * win[..., 1] + 0.114 * win[..., 2]
        m = chartmath.grey_ratios(win, grey_only=True)
        row.update(luma=float(Y.mean()),
                   flatness=float(np.mean([win[..., c].std() for c in range(3)])),
                   crush=float((Y < 16).mean() * 100),
                   clip=float((Y >= 250).mean() * 100))
        if m is None:
            row["verdict"] = ("NO MEASUREMENT -- fewer than 50 px inside the "
                              "60..235 luma band; too dark, too small or clipping")
        else:
            row.update(r_g=m["rg"], b_g=m["bg"], raw_sat=m["raw_sat"], n_used=m["n"])
            problems = []
            if row["n_px"] < 2000:
                problems.append("small box (<2000 px): noisy")
            if row["flatness"] > 25:
                # Deliberately worded as an instruction to look, not a verdict.
                # A keyboard's key texture reads flatness ~30-50 while being
                # exactly the right surface; a box straddling a shadow gap reads
                # the same. Only the overlay separates the two.
                problems.append(f"flatness {row['flatness']:.1f} -- LOOK at the "
                                f"overlay: either surface texture (fine) or the "
                                f"box straddles an edge (not fine)")
            if row["crush"] > 5:
                problems.append(f"{row['crush']:.1f}% crushed")
            if row["clip"] > 1:
                problems.append(f"{row['clip']:.1f}% clipping -- ratios unreliable")
            row["verdict"] = "; ".join(problems) if problems else "usable"
        out.append(row)
    return out
