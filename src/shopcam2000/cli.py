"""``shopcam`` - the command line.

Five verbs. `init` is the one to run first, `doctor` and the one to run when something
is wrong; everything it checks is something that fails silently.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from . import __version__


def _ask(prompt: str, default: str = "", secret: bool = False) -> str:
    """One question on the console. Empty answer = the default."""
    import getpass
    shown = f" [{default}]" if default and not secret else ""
    try:
        raw = (getpass.getpass if secret else input)(f"  {prompt}{shown}: ")
    except EOFError:
        raw = ""
    return raw.strip() or default


def _probe_blue_iris(host: str, port: int, user: str, password: str):
    """Log in and list cameras. Returns (cameras, error)."""
    import asyncio

    from .blueiris import BlueIrisClient, BlueIrisError

    async def go():
        client = BlueIrisClient(host, port, user, password, timeout=6.0)
        try:
            await client.login()
            return await client.camlist(), None
        except BlueIrisError as exc:
            return [], str(exc)
        except Exception as exc:  # DNS, refused, timeout - all mean "not there"
            return [], f"{type(exc).__name__}: {exc}"
        finally:
            await client.close()

    return asyncio.run(go())


def _toml_list(items: list[str]) -> str:
    return "[" + ", ".join(f'"{i}"' for i in items) + "]"


def _write_config(path, host: str, port: int, user: str, password: str,
                  always_hide: list[str], audio_only: list[str]) -> None:
    """The example file IS the documentation, so keep every comment in it and
    substitute only the values the wizard collected."""
    import re

    from .config import EXAMPLE_PATH
    text = EXAMPLE_PATH.read_text(encoding="utf-8")

    def set_in_section(section: str, key: str, value: str) -> None:
        nonlocal text
        pat = re.compile(rf"(^\[{section}\]\n(?:.*\n)*?^{key}\s*=\s*)(.*)$", re.M)
        text, n = pat.subn(lambda m: m.group(1) + value, text, count=1)
        if n != 1:
            raise RuntimeError(f"could not find [{section}] {key} in the example config")

    esc = lambda v: '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'
    set_in_section("blue_iris", "host", esc(host))
    set_in_section("blue_iris", "port", str(port))
    set_in_section("blue_iris", "user", esc(user))
    set_in_section("blue_iris", "password", esc(password))
    set_in_section("recording", "always_hide", _toml_list(always_hide))
    set_in_section("recording", "audio_only", _toml_list(audio_only))
    path.write_text(text, encoding="utf-8")


def _cmd_init(args) -> int:
    """Write config.toml here - by asking, and by checking the answers.

    The questions are the four things a first run cannot guess (where Blue Iris
    is and how to log in), and the test is the one that matters: can this
    machine reach that recorder with those credentials, and what cameras does it
    see. A config that was never tested is the classic "it starts but shows
    nothing" - so the wizard will not write one without saying so.
    """
    import shutil

    from .config import CONFIG_PATH, EXAMPLE_PATH

    if CONFIG_PATH.exists() and not args.force:
        print(f"{CONFIG_PATH} already exists. Use --force to overwrite.", file=sys.stderr)
        return 1

    if args.blank:
        shutil.copyfile(EXAMPLE_PATH, CONFIG_PATH)
        print(f"Wrote {CONFIG_PATH} (untouched example). Edit [blue_iris], then: shopcam doctor")
        return 0

    interactive = sys.stdin.isatty() and not args.yes
    print()
    print("  SHOPCAM 2000 - first-run setup")
    print("  Answers go into config.toml in this directory. Nothing leaves this machine.")
    print()

    host = args.host or (_ask("Blue Iris host or IP", "127.0.0.1") if interactive else "127.0.0.1")
    port = args.port or int(_ask("Blue Iris web server port", "81") if interactive else 81)
    user = args.user or (_ask("Blue Iris user (needs 'clip creation' permission)", "") if interactive else "")
    password = args.password or (_ask("Blue Iris password", "", secret=True) if interactive else "")

    cameras: list[dict] = []
    if args.no_test:
        print("  (connection test skipped)")
    else:
        print(f"\n  Testing http://{host}:{port}/json as {user or '<no user>'} ...")
        cameras, err = _probe_blue_iris(host, port, user, password)
        if err:
            print(f"  FAILED: {err}")
            print("  The Controller would start and show nothing. Check host/port (Blue Iris >")
            print("  Settings > Web server), the user's 'clip creation' permission, and that")
            print("  Blue Iris is actually running on that machine.")
            if interactive and _ask("Write config.toml anyway? (y/N)", "n").lower() != "y":
                return 2
        else:
            print(f"  OK - Blue Iris answered. {len(cameras)} camera(s):")
            for cam in cameras:
                short = cam.get("optionValue", "?")
                name = cam.get("optionDisplay", short)
                state = "online" if cam.get("isOnline", True) else "OFFLINE"
                print(f"      {short:<12} {name:<28} {state}")

    always_hide: list[str] = []
    audio_only: list[str] = []
    if cameras and interactive:
        print()
        print("  Every camera above will appear on the Controller. You can change these later")
        print("  in config.toml ([recording] always_hide / audio_only).")
        hide = _ask("Cameras to HIDE from the UI (short names, comma-separated, or blank)", "")
        mics = _ask("Sources that are MICROPHONES, not cameras (short names, or blank)", "")
        known = {str(c.get("optionValue")) for c in cameras}
        always_hide = [x.strip() for x in hide.split(",") if x.strip()]
        audio_only = [x.strip() for x in mics.split(",") if x.strip()]
        for x in always_hide + audio_only:
            if x not in known:
                print(f"  note: '{x}' is not a camera Blue Iris listed; kept as typed.")

    _write_config(CONFIG_PATH, host, port, user, password, always_hide, audio_only)
    print(f"\n  Wrote {CONFIG_PATH}")
    print("  Next:  shopcam doctor      (checks the things that fail silently)")
    print("         shopcam serve       (the Controller, on this LAN)")
    if not cameras and not args.no_test:
        print("  Blue Iris did not answer, so nothing was hidden or marked as a mic yet.")
    print()
    return 0


def _lan_address() -> str:
    """Best guess at the address a phone on the same network should use."""
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))   # no packets sent; this just picks the route
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def _cmd_serve(args) -> int:
    import logging

    from .config import ConfigError
    from .config import load as load_config

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(name)-22s %(message)s",
        datefmt="%H:%M:%S",
    )
    # httpx logs every request at INFO. At a one-second poll that is a line per
    # second for ever, which buries the messages that actually matter.
    if not args.verbose:
        logging.getLogger("httpx").setLevel(logging.WARNING)

    try:
        config = load_config()
    except ConfigError as exc:
        print(f"\n  {exc}\n\n  Try `shopcam init`, then `shopcam doctor`.\n", file=sys.stderr)
        return 2

    import uvicorn

    from .server import create_app

    host = args.host or config.server.host
    port = args.port or config.server.port
    where = _lan_address() if host in {"0.0.0.0", "::"} else host

    print()
    print("  SHOPCAM 2000 CONTROLLER")
    print(f"    controller : http://{where}:{port}")
    print(f"    blue iris  : http://{config.blue_iris.host}:{config.blue_iris.port}")
    print("    LAN only - do not port-forward this.")
    if host in {"0.0.0.0", "::"}:
        print()
        print("    NOTE  Other devices reach this over plain HTTP, which is not a")
        print("          secure context: the browser will not offer to install the")
        print("          app and the screen will not stay awake.")
        print("          `shopcam doctor` prints the two-minute fix.")
    print()

    uvicorn.run(create_app(config), host=host, port=port, log_level=args.log_level)
    return 0


def _cmd_doctor(args) -> int:
    from .doctor import main as doctor_main
    return doctor_main(colour=not args.no_colour)


def _cmd_encode(args) -> int:
    from .encode import main as encode_main
    return encode_main(args)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="shopcam",
        description="Arm-and-record controller for a multi-camera workshop rig.")
    p.add_argument("--version", action="version", version=f"shopcam2000 {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("doctor", help="check the things that fail silently (start here)")
    d.add_argument("--no-colour", "--no-color", action="store_true", dest="no_colour")
    d.set_defaults(func=_cmd_doctor)

    i = sub.add_parser(
        "init",
        help="first-run setup: asks for Blue Iris, tests the login, lists cameras, writes config.toml",
        description="Asks where Blue Iris is and how to log in, tests that answer, "
                    "shows the cameras it found, and writes config.toml here. "
                    "Pass the answers as flags to run it unattended.")
    i.add_argument("--force", action="store_true", help="overwrite an existing config.toml")
    i.add_argument("--host", help="Blue Iris host or IP")
    i.add_argument("--port", type=int, help="Blue Iris web server port (default 81)")
    i.add_argument("--user", help="Blue Iris user with 'clip creation' permission")
    i.add_argument("--password", help="that user's password (prefer the prompt: flags land in shell history)")
    i.add_argument("--no-test", action="store_true", help="write without contacting Blue Iris")
    i.add_argument("--yes", "-y", action="store_true", help="never prompt; use flags and defaults")
    i.add_argument("--blank", action="store_true", help="just copy the commented example, as before")
    i.set_defaults(func=_cmd_init)

    s = sub.add_parser("serve", help="run the Controller and its web UI")
    s.add_argument("--host", default=None)
    s.add_argument("--port", type=int, default=None)
    s.add_argument("--log-level", default="warning")
    s.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    s.set_defaults(func=_cmd_serve)

    e = sub.add_parser(
        "encode",
        help="USB / capture / desktop -> RTSP, so a recorder can see it",
        description="Build (and optionally run) the ffmpeg command that publishes a "
                    "local device as an RTSP camera. Use --dry-run to get a command "
                    "you can read, keep and run yourself.")
    e.add_argument("--list", action="store_true", help="list capture devices and exit")
    e.add_argument("--device", help="device name, EXACTLY as --list spells it")
    e.add_argument("--url", help="rtsp://127.0.0.1:8554/cam1")
    e.add_argument("--audio-only", action="store_true",
                   help="a microphone, published with a waveform for a picture")
    e.add_argument("--audio-device", default="", help="mix this mic into a video stream")
    e.add_argument("--width", type=int, default=1920)
    e.add_argument("--height", type=int, default=1080)
    e.add_argument("--fps", type=int, default=30)
    e.add_argument("--bitrate", default="8M")
    e.add_argument("--encoder", default="libx264",
                   help="libx264 (default, works everywhere) or h264_nvenc")
    e.add_argument("--pix-fmt", default="yuvj420p",
                   help="LOAD-BEARING. Do not leave to ffmpeg - see docs/usb-to-h264.md")
    e.add_argument("--input-codec", default="mjpeg",
                   help="dshow input pin. mjpeg is the one that works; '' to leave it alone")
    e.add_argument("--substream-url", default="",
                   help="also publish a 640x480@15 substream here, for the live grid")
    e.add_argument("--dry-run", action="store_true", help="print the command, do not run it")
    e.set_defaults(func=_cmd_encode)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
