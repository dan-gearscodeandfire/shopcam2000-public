"""Transcribe one Shopcam 2000 event folder into a timestamped text file.

Launched as a DETACHED SUBPROCESS by `hooks.py` the moment the Controller has
finished filing a TWAB press. Nothing waits for it. It runs under the WhisperLive
virtualenv (see SHOPCAM_WHISPER_ROOT below), NOT the Controller's, so torch,
ctranslate2 and CUDA never enter the recording process.

    python transcribe_take.py "C:\\BlueIris\\New\\2026-08-03_21-22-37"

Writes `<stamp>_transcript.txt` (+ `.json`) into that same folder, where `<stamp>`
matches the clip filenames beside it. Exactly one transcript per folder.

Audio source, in order, from the manifest's own record of what saved:
MIC1 -> CAM5 -> CAM1 -> a marker saying this was a non-speaking clip.

Why the manifest and not a directory listing: `shopcam.json` carries `decoded`
per clip, so it knows the difference between "a file exists" and "a file plays".
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import datetime
from pathlib import Path

# --------------------------------------------------------------------- config

WHISPER_ROOT = Path(os.environ.get("SHOPCAM_WHISPER_ROOT", r"C:\whisper\WhisperLive"))  # your whisper checkout
MODEL_DIR = WHISPER_ROOT / "small_dan_ct2"
FFMPEG = Path(r"C:\shopcam2000-bridge\ffmpeg\ffmpeg.exe")

# The chain the user specified 2026-08-06. CAM2 carries audio too and was
# declined deliberately - do not add it back.
AUDIO_CHAIN = ["MIC1", "CAM5", "CAM1"]

# 🔴 NO initial_prompt. A jargon prompt was tried 2026-08-06 and LEAKED: the
# first real run emitted "Now, granted, CAM1, people with hyperactive super
# egos..." - "CAM1" was never said, it bled out of the prompt into the text.
# Whisper conditions on the prompt, so on quiet or ambient audio it will happily
# decode the prompt back at you. The fine-tune does not need it: measured on
# press_mic1.wav it already returns "cam one" and "cam four, cam five, and mic
# one" with no prompt at all. A leaked word in an INDEX is worse than a missing
# one, because it is searchable and false.
INITIAL_PROMPT = None

DEVICE = "cuda"
COMPUTE_TYPE = "auto"


def log(msg: str) -> None:
    print(f"[transcribe] {msg}", file=sys.stderr, flush=True)


# ------------------------------------------------------------------ the pieces

def local_stamp(epoch: float) -> str:
    """Compact local stamp, matching the sorted clip filenames beside us."""
    return datetime.fromtimestamp(epoch).strftime("%Y%m%d_%H%M%S")


def pick_source(manifest: dict, folder: Path) -> tuple[str, Path] | None:
    """First camera in the chain that actually filed a playable clip."""
    by_camera = {c.get("camera"): c for c in manifest.get("clips", [])}
    for camera in AUDIO_CHAIN:
        clip = by_camera.get(camera)
        if not clip:
            continue
        if clip.get("decoded") is False:
            log(f"{camera}: filed but does not decode ({clip.get('why') or 'no reason given'}); skipping")
            continue
        path = folder / str(clip.get("file") or "")
        if not path.is_file():
            log(f"{camera}: manifest names {clip.get('file')} but it is not on disk; skipping")
            continue
        return camera, path
    return None


def extract_audio(src: Path) -> Path:
    """16 kHz mono wav, which is what the model wants anyway."""
    handle, tmp = tempfile.mkstemp(suffix=".wav", prefix="shopcam_")
    os.close(handle)
    cmd = [str(FFMPEG), "-y", "-loglevel", "error", "-i", str(src),
           "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", tmp]
    subprocess.run(cmd, check=True, capture_output=True)
    return Path(tmp)


def load_model():
    """WhisperLive's own vendored transcriber - no server, nothing resident.

    Model load was measured at 0.97 s, so there is nothing worth keeping warm.
    """
    for sub in (r"torch\lib", "ctranslate2"):
        p = Path(sys.prefix) / "Lib" / "site-packages" / sub
        if p.is_dir():
            try:
                os.add_dll_directory(str(p))
            except OSError:
                pass
    if str(WHISPER_ROOT) not in sys.path:
        sys.path.insert(0, str(WHISPER_ROOT))

    from whisper_live.transcriber.transcriber_faster_whisper import WhisperModel
    return WhisperModel(str(MODEL_DIR), device=DEVICE, compute_type=COMPUTE_TYPE)


# ------------------------------------------------------------------- rendering

def render_text(head: dict, rows: list[dict]) -> str:
    out = [
        "Shopcam 2000 transcript",
        "=" * 72,
        f"folder      : {head['folder']}",
        f"event       : {head['kind']}"
        + (f" #{head['seq']}" if head.get("seq") is not None else ""),
        f"pressed at  : {head['pressLocal']}",
        f"clip starts : {head['clipStartLocal']}",
        f"source      : {head['source']}  ({head['sourceFile']})",
        f"model       : {head['model']}",
        f"generated   : {head['generatedLocal']}",
        "",
    ]
    if not rows:
        out += [
            "*** NON-SPEAKING CLIP ***",
            "",
            f"reason: {head['reason']}",
            "",
            "No speech was transcribed for this event.",
        ]
        return "\n".join(out) + "\n"

    out += [
        f"{len(rows)} segment(s).",
        "",
        "  t_clip = seconds into the clip",
        "  t_press = seconds relative to the button (negative = before the press)",
        "",
        f"{'t_clip':>9}  {'t_press':>9}  {'wall':>8}   text",
        f"{'-' * 9}  {'-' * 9}  {'-' * 8}   {'-' * 40}",
    ]
    for r in rows:
        out.append(f"{r['tClip']:>8.1f}s  {r['tPress']:>+8.1f}s  {r['wall']:>8}   {r['text']}")
    out.append("")
    return "\n".join(out) + "\n"


def write_atomic(path: Path, text: str) -> None:
    """Same discipline the sorter uses: .part then rename."""
    part = path.with_suffix(path.suffix + ".part")
    part.write_text(text, encoding="utf-8")
    part.replace(path)


# ------------------------------------------------------------------------ main

def main(argv: list[str]) -> int:
    if len(argv) < 2:
        log("usage: transcribe_take.py <event-folder>")
        return 2

    folder = Path(argv[1])
    if not folder.is_dir():
        log(f"not a directory: {folder}")
        return 2

    manifest_path = folder / "shopcam.json"
    manifest: dict = {}
    for attempt in range(5):
        try:
            # utf-8-sig, not utf-8: it reads BOM-less files identically but also
            # survives a manifest that picked up a BOM from a PowerShell tool.
            manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
            break
        except Exception:
            if attempt == 4:
                log(f"could not read {manifest_path}: {traceback.format_exc(limit=1).strip()}")
                return 1
            time.sleep(0.5)

    started_at = float(manifest.get("startedAt") or 0) or time.time()
    event_at = float(manifest.get("eventAt") or started_at)
    stamp = local_stamp(started_at)

    txt_path = folder / f"{stamp}_transcript.txt"
    json_path = folder / f"{stamp}_transcript.json"

    # Exactly one transcript per folder. A second launch is a no-op, so a
    # re-run can never duplicate or half-overwrite an existing one.
    if txt_path.exists():
        log(f"{txt_path.name} already exists; nothing to do")
        return 0

    head = {
        "folder": manifest.get("folder") or folder.name,
        "kind": manifest.get("kind") or "unknown",
        "seq": manifest.get("seq"),
        "pressLocal": datetime.fromtimestamp(event_at).strftime("%Y-%m-%d %H:%M:%S"),
        "clipStartLocal": datetime.fromtimestamp(started_at).strftime("%Y-%m-%d %H:%M:%S"),
        "model": MODEL_DIR.name,
        "generatedLocal": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "-", "sourceFile": "-", "reason": "",
    }

    picked = pick_source(manifest, folder)
    if picked is None:
        head["reason"] = (
            "no-audio-source: none of " + ", ".join(AUDIO_CHAIN)
            + " filed a playable clip for this event"
        )
        log(head["reason"])
        write_atomic(txt_path, render_text(head, []))
        write_atomic(json_path, json.dumps({"head": head, "segments": []}, indent=2))
        return 0

    camera, clip_path = picked
    head["source"], head["sourceFile"] = camera, clip_path.name
    log(f"source {camera} -> {clip_path.name}")

    wav = None
    try:
        wav = extract_audio(clip_path)
        model = load_model()
        t0 = time.perf_counter()
        segments, info = model.transcribe(
            str(wav),
            word_timestamps=True,
            vad_filter=True,
            initial_prompt=INITIAL_PROMPT,
            # Do not let one hallucinated segment seed the next. Shop audio is
            # long stretches of machine noise, which is exactly the condition
            # that sends Whisper into a repeating loop.
            condition_on_previous_text=False,
        )
        # The generator is lazy; draining it does the work.
        #
        # WhisperLive's transcriber returns None -- NOT an empty generator -- when
        # vad_filter removes every segment. That is simply a SILENT TAKE, which the
        # `if not rows:` path below already handles gracefully, so it must not be an
        # exception. Seen 2026-08-24 on a speechless timeline-test take: every silent
        # press was ending in a traceback and writing no transcript at all.
        segs = list(segments) if segments is not None else []
        elapsed = time.perf_counter() - t0
        lang = getattr(info, "language", "?")
        log(f"transcribed in {elapsed:.2f}s ({len(segs)} segments, lang={lang})")
    except Exception:
        log("transcription failed:\n" + traceback.format_exc())
        return 1
    finally:
        if wav is not None:
            wav.unlink(missing_ok=True)

    rows = []
    for s in segs:
        text = (s.text or "").strip()
        if not text:
            continue
        rows.append({
            "tClip": round(s.start, 2),
            "tPress": round(started_at + s.start - event_at, 2),
            "wall": datetime.fromtimestamp(started_at + s.start).strftime("%H:%M:%S"),
            "text": text,
            "words": [
                {"start": round(w.start, 3), "end": round(w.end, 3), "word": w.word}
                for w in (s.words or [])
            ],
        })

    # A clip whose audio carried no speech is a non-speaking clip too. On an
    # overwatch press this is the common case, not the edge case: 60 s of
    # pre-roll may be nothing but a running tool. VAD is what keeps Whisper
    # from inventing confident sentences over machine noise - and an invented
    # line is worse than a gap, because this file is an INDEX.
    if not rows:
        head["reason"] = f"no-speech: {camera} audio contained no detected speech"
        log(head["reason"])

    write_atomic(txt_path, render_text(head, rows))
    write_atomic(json_path, json.dumps({"head": head, "segments": rows}, indent=2))
    log(f"wrote {txt_path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
