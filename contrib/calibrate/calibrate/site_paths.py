"""Where this toolkit finds the things that differ from one machine to the next.

On the reference rig these were literal paths written into each tool. Here each
one is an environment variable with a generic default, so nothing in the
toolkit names a particular machine or user. Set only the ones you need.

    SHOPCAM_HOME            folder holding config.toml - the same rule the
                            Controller uses. Default: the current directory,
                            then this toolkit's own folder.
    SHOPCAM_FFMPEG          ffmpeg / ffprobe. Default: PATH, then the copy
    SHOPCAM_FFPROBE         that ships beside Blue Iris.
    SHOPCAM_CLIP_DIR        Blue Iris's clip folder. Default C:\\BlueIris\\New
    SHOPCAM_PROCAMP_PY      a Python with comtypes + pygrabber, used to drive
                            USB (UVC) cameras. Default: the one running this.
    SHOPCAM_CALIBRATE_DURABLE
                            where a second copy of every sweep rollback goes,
                            outside the repo. Default ~/shopcam2000-calibrate-rollbacks
"""
from __future__ import annotations

import os
import shutil
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # contrib/calibrate

_BLUE_IRIS_GUESSES = (
    r"C:\Program Files\Blue Iris 5",
    r"C:\Program Files\Blue Iris 6",
    r"C:\Program Files (x86)\Blue Iris 5",
)


def _find_tool(name: str) -> str:
    """Env override, then PATH, then beside Blue Iris. Falls back to the bare
    name so the error, if any, comes from the OS naming the missing program."""
    env = os.environ.get(f"SHOPCAM_{name.upper()}")
    if env:
        return env
    found = shutil.which(name)
    if found:
        return found
    exe = name + (".exe" if os.name == "nt" else "")
    for base in _BLUE_IRIS_GUESSES:
        candidate = Path(base) / exe
        if candidate.exists():
            return str(candidate)
    return name


FFMPEG = _find_tool("ffmpeg")
FFPROBE = _find_tool("ffprobe")

CLIP_DIR = Path(os.environ.get("SHOPCAM_CLIP_DIR", r"C:\BlueIris\New"))

#: `procamp.py` lives beside this file in `remote/` and is run as a subprocess,
#: because DirectShow COM wants its own process and its own interpreter.
PROCAMP_DIR = ROOT / "calibrate" / "remote"
PROCAMP_PY = Path(os.environ.get("SHOPCAM_PROCAMP_PY", sys.executable))

DURABLE = Path(os.environ.get("SHOPCAM_CALIBRATE_DURABLE",
                              Path.home() / "shopcam2000-calibrate-rollbacks"))


def config_path() -> Path:
    """config.toml, found the way the Controller finds it."""
    home = os.environ.get("SHOPCAM_HOME")
    candidates = [Path(home) / "config.toml"] if home else []
    candidates += [Path.cwd() / "config.toml", ROOT / "config.toml"]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]


def load_config() -> dict:
    """The parsed config, or a plain-English exit saying what to do."""
    p = config_path()
    if not p.exists():
        raise SystemExit(
            f"No config.toml found (looked for {p}).\n"
            f"Run `shopcam init`, or copy src/shopcam2000/config.example.toml to "
            f"config.toml, fill in the [cameras] section, and run from that "
            f"folder (or set SHOPCAM_HOME to it).")
    return tomllib.loads(p.read_text(encoding="utf-8-sig"))
