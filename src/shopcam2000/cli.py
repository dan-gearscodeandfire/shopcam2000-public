"""``shopcam`` - the command line.

Five verbs. `doctor` is the one to run first and the one to run when something
is wrong; everything it checks is something that fails silently.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from . import __version__


def _cmd_init(args) -> int:
    """Write a commented config.toml into the current directory."""
    from .config import CONFIG_PATH, EXAMPLE_PATH
    if CONFIG_PATH.exists() and not args.force:
        print(f"{CONFIG_PATH} already exists. Use --force to overwrite.", file=sys.stderr)
        return 1
    shutil.copyfile(EXAMPLE_PATH, CONFIG_PATH)
    print(f"Wrote {CONFIG_PATH}\n\n"
          "Edit blue_iris.host / user / password, then run:\n"
          "    shopcam doctor")
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

    i = sub.add_parser("init", help="write a starter config.toml here")
    i.add_argument("--force", action="store_true")
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
