"""Loads and fires the user's optional ``hooks.py``.

The contract, restated because it is the whole point of this module: **a hook can
never delay, block or fail a recording.** Hooks run on a daemon thread, are given
a deadline, and every failure is logged and swallowed. If your Apple TV is
unplugged, the take still happens.
"""

from __future__ import annotations

import importlib.util
import logging
import threading
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

HOOK_TIMEOUT = 10.0  # seconds before a hook is abandoned (it keeps running, we stop waiting)


class HookRunner:
    def __init__(self, hooks_path: Path) -> None:
        self._path = hooks_path
        self._module: Any = None
        self.load()

    @property
    def loaded(self) -> bool:
        return self._module is not None

    @property
    def available(self) -> list[str]:
        if not self._module:
            return []
        return [
            name for name in ("on_record_start", "on_record_stop", "on_twab_filed")
            if callable(getattr(self._module, name, None))
        ]

    def load(self) -> None:
        """Import ``hooks.py`` if the user has written one.

        A broken hooks file is a warning, not a crash - the Controller's job is
        to record, and it should still do that when your automation has a syntax
        error in it.
        """
        if not self._path.exists():
            log.info("no hooks.py found; outgoing hooks disabled "
                     "(copy hooks.example.py to hooks.py to enable them)")
            self._module = None
            return
        try:
            spec = importlib.util.spec_from_file_location("shopcam_hooks", self._path)
            if spec is None or spec.loader is None:
                raise ImportError("could not build a module spec")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception:
            log.exception("hooks.py failed to load; continuing without hooks")
            self._module = None
            return
        self._module = module
        log.info("hooks.py loaded (%s)", ", ".join(self.available) or "no hook functions defined")

    def _fire(self, name: str, *args: Any) -> None:
        func: Callable | None = getattr(self._module, name, None) if self._module else None
        if not callable(func):
            return

        def target() -> None:
            try:
                func(*args)
            except Exception:
                log.exception("hook %s() raised; recording is unaffected", name)

        thread = threading.Thread(target=target, name=f"hook-{name}", daemon=True)
        thread.start()
        thread.join(HOOK_TIMEOUT)
        if thread.is_alive():
            log.warning("hook %s() exceeded %.0fs and was abandoned; "
                        "recording is unaffected", name, HOOK_TIMEOUT)

    def record_start(self, cameras: list[str], audio_matters: bool) -> None:
        self._fire("on_record_start", list(cameras), audio_matters)

    def record_stop(self, cameras: list[str], clips: list[dict],
                    audio_matters: bool) -> None:
        self._fire("on_record_stop", list(cameras), list(clips), audio_matters)

    def twab_filed(self, sort: dict) -> None:
        """Fired once a TWAB press has been verified and filed into its folder.

        ``sort`` is the summary ClipSorter returned - notably ``folder``, the
        absolute path of the event folder, plus ``name``, ``kind``, ``files``
        and ``bytes``.

        This is the only hook on the press path. The take path has
        ``on_record_stop``; a press never went through it, so anything that
        should happen "when the button is pressed and the footage has landed"
        belongs here.
        """
        self._fire("on_twab_filed", dict(sort))
