#!/usr/bin/env python3
"""
post_take.py -- everything that must happen to a take AFTER the press.

Launched DETACHED by hooks.on_twab_filed, so the press path is never delayed and the
10 s HOOK_TIMEOUT in hooks_runner never comes into play. This process is allowed to
take minutes; the Controller has already moved on.

WHY THE STEPS RUN IN SEQUENCE, NOT IN PARALLEL
----------------------------------------------
They touch the same files. `retag_colour` replaces each clip via os.replace once it
has verified the rewrite; `transcribe_take` reads the audio of those same clips. Run
concurrently, transcription can hold a handle to a file that is being swapped under
it, or read a half-written temp. Sequential is not a style preference here.

ORDER: re-tag FIRST, transcribe SECOND.
  * re-tagging is seconds; transcription is minutes. Doing tags first means the take
    is correct on disk almost immediately after the press, instead of waiting behind
    a Whisper run.
  * if Whisper is missing, broken, or the GPU is busy, the tags still get fixed.

Each step is independently guarded: a step that fails or throws is logged and the
next step still runs. A take is never left half-processed because of one bad step.

LOG: C:\\shopcam2000\\var\\post_take.log   (var/ is gitignored)
"""
from __future__ import annotations

import subprocess
import sys
import time
import os
from pathlib import Path

REPO = Path(r"C:\shopcam2000")
RETAG = REPO / "tools" / "retag_colour.py"
TRANSCRIBE = REPO / "tools" / "transcribe_take.py"

# The Whisper virtualenv is deliberately NOT the Controller's -- see hooks.py.
WHISPER_PY = Path(os.environ.get("SHOPCAM_WHISPER_PY", r"C:\whisper\.venv\Scripts\python.exe"))  # your whisper venv

# Revert manifests must NOT be written into the take folder: takes live under
# C:\BlueIris\New, which the Blue Iris registry ages out at archdays=7. A manifest
# is the only undo for a re-tag, so it goes somewhere durable.
MANIFEST_DIR = REPO / "var" / "retag"

LOG = REPO / "var" / "post_take.log"

STEP_TIMEOUT = 60 * 60  # an hour; a long take's transcription is still far under this


def log(msg: str) -> None:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {msg}"
    print(line, flush=True)


def run_step(name: str, argv: list[str]) -> bool:
    """Run one step to completion. Never raises -- a failed step must not stop the rest."""
    log(f"--- {name}: starting")
    t0 = time.time()
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=STEP_TIMEOUT)
    except subprocess.TimeoutExpired:
        log(f"--- {name}: TIMED OUT after {STEP_TIMEOUT}s")
        return False
    except Exception as exc:                                  # noqa: BLE001
        log(f"--- {name}: FAILED to launch: {exc!r}")
        return False
    dur = time.time() - t0
    for line in (r.stdout or "").splitlines():
        log(f"    {name}| {line}")
    if r.returncode != 0:
        for line in (r.stderr or "").splitlines()[-20:]:
            log(f"    {name}! {line}")
        log(f"--- {name}: EXIT {r.returncode} after {dur:.1f}s")
        return False
    log(f"--- {name}: ok in {dur:.1f}s")
    return True


def main(folder: str) -> int:
    take = Path(folder)
    log(f"=== post_take for {take.name}")
    if not take.is_dir():
        log(f"!!! not a directory, nothing to do: {take}")
        return 1

    results = {}

    # ---- step 1: colour tags (fast) ------------------------------------------
    if RETAG.is_file():
        MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
        manifest = MANIFEST_DIR / f"{take.name}.json"
        results["retag"] = run_step(
            "retag", [sys.executable, str(RETAG), str(take), "-m", str(manifest)])
    else:
        log(f"--- retag: SKIPPED, missing {RETAG}")
        results["retag"] = None

    # ---- step 2: transcription (slow) ----------------------------------------
    if WHISPER_PY.is_file() and TRANSCRIBE.is_file():
        results["transcribe"] = run_step(
            "transcribe", [str(WHISPER_PY), str(TRANSCRIBE), str(take)])
    else:
        missing = WHISPER_PY if not WHISPER_PY.is_file() else TRANSCRIBE
        log(f"--- transcribe: SKIPPED, missing {missing}")
        results["transcribe"] = None

    log(f"=== done {take.name}: " + ", ".join(f"{k}={v}" for k, v in results.items()))
    return 0 if all(v is not False for v in results.values()) else 2


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: post_take.py <take folder>")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    # Own the log here so the steps' output is interleaved in one place, in order.
    with open(LOG, "a", encoding="utf-8", buffering=1) as fh:
        sys.stdout = fh
        sys.stderr = fh
        try:
            sys.exit(main(sys.argv[1]))
        except SystemExit:
            raise
        except Exception:                                     # noqa: BLE001
            import traceback
            traceback.print_exc()
            sys.exit(3)
