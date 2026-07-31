"""Finding the two external binaries this project cannot ship.

ffmpeg and ffprobe are not on PyPI and never will be - they are large, they are
platform-specific, and their licensing depends on which encoders yours was built
with. So they are a dependency you install, and the job here is to *find* them
and, when we cannot, to say so in a sentence that tells you what to do rather
than raising ``FileNotFoundError: 'ffprobe'``.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

# Where Blue Iris puts its own copy. Worth checking because if you are running
# this at all you very likely have Blue Iris, and if you have Blue Iris you
# already have a working ffmpeg on the box whether you knew it or not.
_BLUE_IRIS_GUESSES = (
    r"C:\Program Files\Blue Iris 5",
    r"C:\Program Files\Blue Iris 6",
    r"C:\Program Files (x86)\Blue Iris 5",
)

INSTALL_HINT = {
    "win32": "winget install Gyan.FFmpeg   (or scoop install ffmpeg)",
    "darwin": "brew install ffmpeg",
}


def install_hint() -> str:
    import sys
    return INSTALL_HINT.get(sys.platform, "apt install ffmpeg   (or your package manager)")


def _find(name: str, configured: str = "") -> str | None:
    """Configured path, then PATH, then beside Blue Iris. None if nowhere."""
    if configured:
        # An explicitly configured path that does not exist is a mistake worth
        # surfacing, not something to quietly paper over by falling back.
        return configured if Path(configured).exists() else None

    found = shutil.which(name)
    if found:
        return found

    exe = name + (".exe" if os.name == "nt" else "")
    for base in _BLUE_IRIS_GUESSES:
        candidate = Path(base) / exe
        if candidate.exists():
            return str(candidate)
    return None


def find_ffprobe(configured: str = "") -> str | None:
    return _find("ffprobe", configured)


def find_ffmpeg(configured: str = "") -> str | None:
    return _find("ffmpeg", configured)


def bridge_state_path(configured: str, state_dir: Path) -> Path:
    """Where the Controller writes what it WANTS the encoders to be doing.

    ⚠️ The supervisor must read the same file. If you point one of them
    somewhere else, the UI will happily show switches that do nothing at all -
    there is no handshake, by design, because the whole point is that the two
    processes do not have to be up at the same time.
    """
    return Path(configured) if configured else state_dir / "bridges.state.json"
