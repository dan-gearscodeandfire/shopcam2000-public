"""Load the per-camera reference files. THEY are the source of truth, not this.

`reference/CAM*.md` holds everything a later agent needs about one camera: its
transport, its REAL parameter ranges and how each one is known, its clip ceiling,
its dead knobs, the things measured about it, and its last-known-good values as a
fenced JSON block. This module only parses them.

WHY THE REFERENCE FILE AND NOT drivers.capabilities()
-----------------------------------------------------
Because `capabilities()` is partly fabricated, and that was found the hard way on
2026-08-07. It reports EVERY Dahua knob as range 0-100, which is false for CAM7's
Gamma (0-15, and 20 is rejected with HTTP 400); it returns None for every
DirectShow range except WhiteBalance; and it omits Gamma on the Dahuas and
Exposure on the ProcAmps entirely -- the two knobs that actually mattered. A
range check against it silently passes everything, which is worse than no check,
because it looks like one.

So ranges come from the reference file, where each one is tagged with HOW it is
known: `probed` (written until it refused), `device` (the driver reported it), or
`assumed`. An `assumed` range is a TASK, not a fact, and callers are expected to
say so out loud rather than quietly trusting it.
"""
from __future__ import annotations

import dataclasses
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
REF_DIR = ROOT / "reference"


@dataclasses.dataclass(frozen=True)
class Reference:
    camera: str
    path: pathlib.Path
    transport: str
    locked: bool
    ceiling: int | None
    settle_s: float
    ranges: dict[str, tuple[int, int, str]]   # knob -> (lo, hi, how_known)
    lkg: dict
    text: str

    def check_value(self, knob: str, value: int) -> str | None:
        """None if acceptable, else a message explaining the refusal or doubt."""
        if knob not in self.ranges:
            return (f"{self.camera} has no recorded range for {knob!r}. Add it to "
                    f"{self.path.name} with how it was learned -- do not guess.")
        lo, hi, how = self.ranges[knob]
        if not (lo <= value <= hi):
            return f"{value} is outside {self.camera}.{knob} range {lo}..{hi} ({how})"
        if how == "assumed":
            return (f"WARN {self.camera}.{knob} range {lo}..{hi} is ASSUMED, not "
                    f"verified. Probe it before trusting a sweep near the edges.")
        return None


_HEAD = re.compile(r"^- \*\*(?P<k>[^:*]+):\*\*\s*(?P<v>.+)$", re.M)
_ROW = re.compile(r"^\|\s*`(?P<knob>[^`]+)`\s*\|\s*(?P<lo>-?\d+)\s*\|\s*"
                  r"(?P<hi>-?\d+)\s*\|\s*(?P<how>.+?)\s*\|$", re.M)
_JSON = re.compile(r"```json\s*\n(?P<body>.*?)\n```", re.S)


def load(camera: str) -> Reference:
    path = REF_DIR / f"{camera.upper()}.md"
    if not path.exists():
        raise FileNotFoundError(
            f"No reference file for {camera}. Every camera needs one -- an "
            f"absent reference is a gap to fill by measuring, not a camera to "
            f"treat like its neighbour. Expected {path}")
    text = path.read_text(encoding="utf-8")

    head = {m.group("k").strip().lower(): m.group("v").strip()
            for m in _HEAD.finditer(text)}

    ceiling_raw = head.get("clip ceiling", "")
    cm = re.search(r"\*\*(\d+)\*\*", ceiling_raw)
    ceiling = int(cm.group(1)) if cm else None

    sm = re.search(r"([\d.]+)", head.get("settle after a write", "0"))
    settle = float(sm.group(1)) if sm else 0.0

    ranges: dict[str, tuple[int, int, str]] = {}
    for m in _ROW.finditer(text):
        how = "assumed" if "assumed" in m.group("how").lower() else \
              "probed" if "probed" in m.group("how").lower() else "device"
        ranges[m.group("knob")] = (int(m.group("lo")), int(m.group("hi")), how)

    jm = _JSON.search(text)
    lkg = json.loads(jm.group("body")) if jm else {}

    return Reference(
        camera=camera.upper(), path=path,
        transport=head.get("transport", "unknown"),
        locked=head.get("locked", "no").lower().startswith(("🔴", "yes")) or
               "YES" in head.get("locked", ""),
        ceiling=ceiling, settle_s=settle, ranges=ranges, lkg=lkg, text=text)


def all_cameras() -> list[str]:
    return sorted(p.stem for p in REF_DIR.glob("CAM*.md"))


def load_all() -> dict[str, Reference]:
    return {c: load(c) for c in all_cameras()}


def flatten(d, prefix="") -> dict[str, object]:
    """Flatten a nested snapshot to dotted keys, so two of them can be diffed.

    Absence is made a VALUE rather than a missing key: a knob that vanished from
    a snapshot must show up as a difference, not as silence. A detector that
    reports nothing looks exactly like one that is not looking.
    """
    out: dict[str, object] = {}
    if isinstance(d, dict):
        for k, v in d.items():
            out.update(flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(d, list):
        for i, v in enumerate(d):
            out.update(flatten(v, f"{prefix}[{i}]"))
    else:
        out[prefix] = d
    return out
