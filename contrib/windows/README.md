# Windows helpers

## What a supervisor has to do

The Controller **declares intent**. It writes the desired state of every encoder
bridge to a JSON file and never starts or stops a process itself. Something else
has to make reality match. That something is a supervisor: a scheduled task on a
short interval (30 s is plenty) that, every cycle:

1. Reads the desired-state file (`state_path`, default
   `<SHOPCAM_HOME>/var/bridges.state.json`).
2. For each bridge, compares desired against actual.
3. Starts the ones that should be running and are not.
4. Stops the ones that should not be running and are.
5. **Health-checks the running ones by pulling a frame**, not by asking whether a
   process exists. A wedged ffmpeg is still a process. `ffprobe -select_streams
   v:0` against the RTSP URL is the check that means something.

🔴 **This is why a microphone bridge needs a synthetic video track** — step 5
asks for a video stream, and an audio-only source has none, so the supervisor
would restart it for ever. See [../../docs/audio.md](../../docs/audio.md).

## Two things that will bite you

- **`Start-Process -ArgumentList` given an array silently splits arguments on
  spaces.** A device name with a space in it becomes two arguments and ffmpeg
  fails with something unrelated. Pass **one string**.
- **Never redirect a native exe's stderr inside PowerShell with `2>&1`.** In
  PowerShell 5.1 each line comes back wrapped in an ErrorRecord and `$?` goes
  false even on exit code 0. Use `-RedirectStandardError <file>` and read the
  file.

## Running it at boot

Register the Controller and the supervisor as Scheduled Tasks set to run whether
or not a user is logged on.

⚠️ If the machine has no auto-logon, a reboot leaves the rig dead until somebody
logs in at the console. That is a deliberate choice on the reference rig, not an
oversight — but know which one you have made.
