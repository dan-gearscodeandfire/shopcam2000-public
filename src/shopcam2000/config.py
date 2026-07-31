"""Configuration loading.

Reads ``config.toml`` from the repository root, with environment variables
taking precedence so the Controller can be run as a service or in a container
without a file on disk.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# 🔴 The working directory, NOT the package directory.
#
# This used to be `Path(__file__).parent.parent`, which is correct for a repo you
# cloned and fatal for a package you pip-installed: it would put config.toml,
# var/state.json and your hooks inside site-packages, where a reinstall deletes
# them and a virtualenv rebuild loses your whole arming set.
#
# So: the directory you run `shopcam` from, or $SHOPCAM_HOME if you would rather
# say it explicitly (a service manager should).
ROOT = Path(os.environ.get("SHOPCAM_HOME") or Path.cwd()).resolve()
CONFIG_PATH = ROOT / "config.toml"
EXAMPLE_PATH = Path(__file__).resolve().parent / "config.example.toml"
STATE_DIR = ROOT / "var"

ENV_PREFIX = "SHOPCAM_"


class ConfigError(RuntimeError):
    pass


@dataclass
class BlueIrisConfig:
    host: str = "127.0.0.1"
    port: int = 81
    user: str = ""
    password: str = ""
    timeout: float = 10.0


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8787


@dataclass
class RecordingConfig:
    default_armed: list[str] = field(default_factory=list)
    always_hide: list[str] = field(default_factory=list)
    # Sources that are microphones. Blue Iris has no concept of an audio-only
    # camera, so a lav mic reaching it through an encoder arrives as a camera
    # whose picture is a waveform - and the Controller would otherwise draw it
    # as one more angle. Named here, it gets an AUDIO badge and its encoder slot
    # says ALWAYS instead of showing a switch that does not exist.
    audio_only: list[str] = field(default_factory=list)
    # Longest pre-roll any camera can contribute, in seconds. Only used to tell
    # "this clip starts early because it has pre-roll" from "this take was
    # appended into an older file" - see Controller._collect_clips.
    max_preroll_seconds: int = 60
    # Blue Iris post-roll ("Break time"), fleet-wide, in seconds. Recording runs
    # for this long after a trigger, and the pre-trigger buffer only starts
    # refilling once it stops - so this is also the width of the window in which
    # a second press extends the first clip instead of starting a new one.
    break_time_seconds: float = 10.0

    # After a TWAB press, go back and DECODE what it wrote.
    #
    # Blue Iris listing a clip is not proof the clip has video in it: measured
    # 2026-07-28, 1 file in 99 was 5.56 MB with no H.264 start code, inside a press
    # that returned 200 with no failures. Every layer trusted the one below and
    # none of them opened the file. This is the layer that opens the file.
    #
    # It runs well AFTER the press has been answered - the firmware gives up in a
    # few seconds and the clip does not even exist until break time has elapsed -
    # so it can never delay or block a press.
    verify_clips: bool = True
    verify_delay_seconds: float = 16.0
    # Empty = look beside Blue Iris's own install. Verification is skipped, loudly,
    # if these are not reachable - e.g. a Controller running on a different host
    # from Blue Iris, which the clip-proxy route already anticipates.
    # Empty = search PATH, then look beside Blue Iris's own install.
    ffprobe_path: str = ""
    clip_dir: str = r"C:\BlueIris\New"   # Blue Iris's default; yours may differ.

    # After a press or a take, COPY its clips into one folder named for when it
    # happened - see shopcam2000/sorting.py.
    #
    # Blue Iris writes every camera into one flat directory, so nine angles of the
    # same moment are nine files among hundreds. This is the difference between
    # having footage and being able to find it.
    #
    # Copy, never move: the originals stay in Blue Iris's clip database so the
    # timeline and the /api/clip proxy keep working. A nine-camera press is
    # ~460 MB, so a sorted copy costs another ~460 MB - hence the floor below.
    sort_clips: bool = True
    # Empty = create the event folders inside clip_dir, one level deeper than the
    # flat pile. ⚠️ C:\BlueIris\New is a Blue-Iris-managed folder (registry:
    # action=2 move-to-Stored, archdays=7, 300 GB cap). Blue Iris archives from
    # its clip database rather than by walking the tree, so copies it has no
    # record of should be untouched - but that is reasoning, not a measurement.
    # Set this to C:\BlueIris\Sorted to have no exposure to the question.
    sort_dir: str = ""
    # Refuse to file rather than fill the disk that is also recording. All-9
    # recording burns 21.7 GB/hr (measured 2026-07-28), so headroom vanishes
    # faster than an operator notices, and a full disk stops Blue Iris writing.
    sort_min_free_gb: float = 25.0


@dataclass
class BridgesConfig:
    """The ffmpeg encoders the Controller may switch off. See bridges.py.

    Four NVENC sessions and four x264 substreams run 24/7 whether or not anyone
    is filming, and that is a power bill. The Controller does not kill them - it
    writes ``state_path`` and the supervisor starts and stops them to match.
    """

    enabled: bool = True
    state_path: str = ""      # empty = <SHOPCAM_HOME>/var/bridges.state.json
    # 🔴 OFF until switched on, including after a reboot. The failure this
    # feature exists to stop is "I forgot to turn it off", so forgetting must be
    # the cheap direction. Turning a bridge on is one tap and takes a few
    # seconds; leaving four running for a week is money.
    default_on: bool = False
    # "<rtsp path / run_<id>.cmd name>:<Blue Iris camera name>". One string per
    # bridge so this survives a SHOPCAM_BRIDGES_BRIDGES env override too.
    # Empty by default: most people's cameras are IP cameras that reach the
    # recorder on their own and have no encoder here to switch.
    bridges: list[str] = field(default_factory=list)


@dataclass
class UiConfig:
    theme: str = "cogitator"
    thumbnail_interval: int = 600
    hide_unavailable: bool = False
    audio_matters: bool = True
    stop_hold_ms: int = 1000
    # Percent, 75-150. The stylesheet sizes the entire interface off one root
    # font size that already tracks the viewport; this multiplies it. Auto-fit
    # always disagrees with somebody about arm's length, and the somebody is
    # standing in the shop, so they get a knob.
    ui_scale: int = 100
    # Hold a screen wake lock while the page is visible. 🔴 Silently impossible
    # on an insecure origin (plain http:// to anything but localhost) - the API
    # is not merely refused there, it does not exist. Settings > About says so.
    keep_awake: bool = True


@dataclass
class PollingConfig:
    while_recording: float = 1.0
    idle: float = 4.0
    offline_backoff: float = 20.0


@dataclass
class Config:
    blue_iris: BlueIrisConfig = field(default_factory=BlueIrisConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    recording: RecordingConfig = field(default_factory=RecordingConfig)
    bridges: BridgesConfig = field(default_factory=BridgesConfig)
    ui: UiConfig = field(default_factory=UiConfig)
    polling: PollingConfig = field(default_factory=PollingConfig)


def _coerce(value: str, template: object) -> object:
    """Turn an environment string into the type the dataclass default implies."""
    if isinstance(template, bool):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(template, int):
        return int(value)
    if isinstance(template, float):
        return float(value)
    if isinstance(template, list):
        return [part.strip() for part in value.split(",") if part.strip()]
    return value


def _apply(section: object, values: dict, section_name: str) -> None:
    for key, current in vars(section).items():
        env_key = f"{ENV_PREFIX}{section_name}_{key}".upper()
        if env_key in os.environ:
            setattr(section, key, _coerce(os.environ[env_key], current))
        elif key in values:
            setattr(section, key, values[key])


def load(path: Path | None = None) -> Config:
    """Load configuration, or explain precisely what is missing."""
    path = path or CONFIG_PATH
    raw: dict = {}

    if path.exists():
        # Read as utf-8-sig, not bytes: Notepad and PowerShell's Set-Content both
        # write UTF-8 *with a BOM* on Windows, and tomllib rejects the BOM with a
        # baffling "Invalid statement at line 1, column 1".
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ConfigError(f"{path.name} is not valid UTF-8: {exc}") from exc
        try:
            raw = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{path.name} is not valid TOML: {exc}") from exc
    elif not any(key.startswith(ENV_PREFIX) for key in os.environ):
        raise ConfigError(
            f"No configuration found.\n\n"
            f"  Copy {EXAMPLE_PATH.name} to {path.name} and fill in your Blue Iris\n"
            f"  host and credentials:\n\n"
            f"      copy {EXAMPLE_PATH.name} {path.name}\n\n"
            f"  config.toml is gitignored, so your password stays local."
        )

    config = Config()
    for name in ("blue_iris", "server", "recording", "bridges", "ui", "polling"):
        _apply(getattr(config, name), raw.get(name, {}), name)

    if not config.blue_iris.user or config.blue_iris.user == "changeme":
        raise ConfigError(
            "blue_iris.user is not set. Edit config.toml - the Controller needs a "
            "Blue Iris account with the 'clip creation' permission in order to start "
            "and stop manual recording."
        )
    if config.ui.theme not in {"cogitator", "clean"}:
        raise ConfigError(
            f"ui.theme must be 'cogitator' or 'clean', not {config.ui.theme!r}"
        )

    STATE_DIR.mkdir(exist_ok=True)
    return config
