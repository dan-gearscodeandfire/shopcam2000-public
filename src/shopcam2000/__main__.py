"""Entry point for ``python -m shopcam2000``.

Delegates to the ``shopcam`` CLI so there is exactly one implementation of
every verb. `python -m shopcam2000` with no arguments means `serve`, because
that is what it meant before this package had subcommands.
"""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    argv = sys.argv[1:] or ["serve"]
    raise SystemExit(main(argv))
