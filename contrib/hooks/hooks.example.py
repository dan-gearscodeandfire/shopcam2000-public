"""Outgoing hooks for the Shopcam 2000 Controller.

Copy this file to `hooks.py` in the repository root and edit it. `hooks.py` is
gitignored, so whatever you automate stays yours and never reaches GitHub.

Both functions are optional — define one, both, or neither.

CONTRACT
--------
Hooks run in a background thread with a timeout. A hook that raises, hangs or
takes too long is logged and abandoned. **A hook can never delay, block or fail
a recording.** That is deliberate: the recording is the point, your smart-home
automation is not.

Do not assume a hook completed before the next one starts.
"""

from __future__ import annotations


def on_record_start(cameras: list[str], audio_matters: bool) -> None:
    """Called immediately after a recording has been started.

    Args:
        cameras: short names of the cameras that actually started recording,
            e.g. ``["CAM2", "CAM4", "CAM7"]``. Cameras that failed to start are
            not included.
        audio_matters: the state of the Controller's audio switch. ``False``
            means the operator has said this take does not need silence — skip
            anything that would interrupt music or television.
    """
    if not audio_matters:
        return  # this take does not need a quiet shop

    # Examples — delete these and write your own.
    #
    # Pause a Home Assistant media player:
    #   import urllib.request, json
    #   req = urllib.request.Request(
    #       "http://homeassistant.local:8123/api/services/media_player/media_pause",
    #       data=json.dumps({"entity_id": "media_player.shop"}).encode(),
    #       headers={"Authorization": "Bearer " + TOKEN,
    #                "Content-Type": "application/json"},
    #   )
    #   urllib.request.urlopen(req, timeout=5)
    #
    # Pause Spotify, mute an Apple TV, send a TiVo pause — anything you like.
    print(f"[hooks] recording started on {', '.join(cameras)}")


def on_twab_filed(sort: dict) -> None:
    """Called once a That Was Awesome press has been verified and filed.

    This is the ONLY hook on the press path - `on_record_start` / `on_record_stop`
    fire for takes, and a press never goes through those. If you want something to
    happen when the button is pressed and the footage has landed, it goes here.

    Args:
        sort: what ClipSorter returned. Useful keys:
            ``folder`` (absolute path to the event folder), ``name`` (just the
            folder name), ``kind``, ``files``, ``bytes``, ``failed``.

    By the time this runs the Controller has already waited out
    ``verify_delay_seconds`` and ffprobed every clip, so the files are not merely
    written - they have been opened and played. There is nothing left to wait for.

    Keep it quick, or hand off and return. Example - transcribe the take's audio
    with a detached subprocess, so nothing here blocks:

        import subprocess
        subprocess.Popen(
            [r"C:\path\to\whisper\.venv\Scripts\python.exe",
             r"C:\shopcam2000\tools\transcribe_take.py", sort["folder"]],
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
        )
    """
    print(f"[hooks] twab filed {sort.get('files')} file(s) into {sort.get('name')}")


def on_record_stop(cameras: list[str], clips: list[dict], audio_matters: bool) -> None:
    """Called immediately after a recording has been stopped.

    Args:
        cameras: short names of the cameras the Controller stopped.
        clips: the clips Blue Iris reports for this take. Each entry is the raw
            ``cliplist`` record, so useful keys include ``camera``, ``file``,
            ``path``, ``date`` (UTC seconds), ``msec`` (playable duration),
            ``filesize`` and ``res``. May be empty if Blue Iris has not finished
            writing the database records yet.
        audio_matters: as above — the switch state for the take that just ended.
    """
    if not audio_matters:
        return

    # Resume whatever you paused in on_record_start.
    print(f"[hooks] recording stopped, {len(clips)} clip(s) from {', '.join(cameras)}")
