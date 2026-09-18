# Hooks: what happens after the press

`hooks.example.py` is the contract. Copy it to `hooks.py` next to your
`config.toml`; the Controller imports it if it exists. A hook runs in a
background thread with a timeout and **can never delay, block or fail a
recording**.

The other three files are what the reference rig's `hooks.py` launches, detached,
the moment a press has been filed into its event folder:

| File | Job | Needs |
|---|---|---|
| `post_take.py` | The sequencer: re-tag colour first, transcribe second, on the folder the press just made | Python only |
| `retag_colour.py` | Losslessly write a complete, consistent colour description onto every clip (most cameras emit none, so an NLE guesses limited range and the calibration is undone on import) | ffmpeg on PATH |
| `transcribe_take.py` | Whisper the take's best audio source (mic → cam5 → cam1) into a timestamped transcript beside the clips | A whisper install in its own venv; set `SHOPCAM_WHISPER_PY` and `SHOPCAM_WHISPER_ROOT` |

None of these are required. They are here because "what did I say at 01:07"
turned out to be the question asked most often about a folder of nine clips.
