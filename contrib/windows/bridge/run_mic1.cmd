@echo off
title *** DO NOT CLOSE *** Shopcam 2000 bridge - MIC1  (lav mic, A-roll audio)
echo ============================================================
echo.
echo    DO NOT CLOSE THIS WINDOW
echo.
echo    Shopcam 2000 - microphone to RTSP bridge
echo    MIC1  (Comica BoomX-D wireless lav, receiver direct to USB)
echo.
echo    Closing this window loses the A-ROLL AUDIO TRACK.
echo    It is blank on purpose - output goes to:
echo      C:\shopcam2000-bridge\mic1_ffmpeg.log
echo.
echo    The supervisor will respawn it within ~60s if it dies.
echo.
echo ============================================================
rem Shopcam 2000 bridge - MIC1 (the lav mic, on its own Blue Iris track)
rem   MAIN -> rtsp://127.0.0.1:8554/mic1   waveform video + AAC 192k mono
rem   no substream - one stream, like every other bridge camera in Blue Iris
rem
rem WHY THIS EXISTS
rem   Every other thing on this rig that carries audio carries it inside a camera, so the
rem   sound is tied to an angle. This is the user's A-ROLL AUDIO: the voice track for the
rem   video. It must not depend on which camera happens to be running, so it is its own
rem   Blue Iris camera and its own bridge.
rem
rem WHY THERE IS A PICTURE ON A MICROPHONE
rem   supervisor.ps1 measures health with Test-RtspFrame, which asks ffprobe for
rem   -select_streams v:0. An audio-only path has no video stream, so the probe fails, so
rem   the supervisor would declare MIC1 unhealthy and restart it every 30 s for ever. A
rem   synthetic video track makes the existing supervisor work UNMODIFIED. Making that
rem   track a WAVEFORM rather than a black rectangle costs the same and buys two things:
rem   you can see from across the shop whether the mic is live, and it is usable B-roll -
rem   this rig's whole thesis is instrumenting a shop.
rem   The GRID and the CENTRE LINE are not decoration. showwaves on true silence draws
rem   nothing at all, and the first frame pulled off this bridge was pure black - which is
rem   exactly what a DEAD camera looks like in the Blue Iris grid. The grid makes "live but
rem   quiet" read as a flat line on a lit tile instead. Measured, not assumed.
rem
rem WHY IT IS NOT IN THE ENCODER TOGGLE
rem   DELIBERATE. The four camera encoders default OFF because four NVENC sessions plus
rem   four x264 substreams cost 6.8 W GPU and 34pp CPU. This is one AAC stream and a
rem   640x360 waveform - a rounding error against that. And the failure it would buy is
rem   the worst one available: a moment happens, nine angles catch it, and there is no
rem   voice. "On is implied, off is only ever explicit" does not save us here, because
rem   arming a CAMERA wakes that camera's encoder, not this one. So MIC1 is always on.
rem   It has no Toggle in supervisor.ps1 and no entry in config [bridges], on purpose.
rem
rem HARDWARE CHANGED 2026-08-06 - THE OLD CHAIN IS GONE
rem   WAS: RODE-clone lav -> receiver -> its HEADPHONE OUT -> C-Media USB adapter,
rem        dshow name "Microphone (USB Audio Device)".  That mic FAILED.
rem   NOW: Comica BoomX-D 2.4 GHz digital wireless, RECEIVER PLUGS STRAIGHT INTO USB.
rem        dshow name "Microphone (Comica_BoomX-D)", USB\VID_10D6&PID_358C&MI_01.
rem   The headphone-out-into-mic-in stage is GONE, and with it the 30-40 dB level
rem   mismatch. THE OLD RULE "gain is low and that is CORRECT, do not raise it" WAS A
rem   CONSEQUENCE OF THAT MISMATCH AND NO LONGER APPLIES. Do not carry it forward.
rem   NOTE the C-Media adapter is STILL PLUGGED IN and still enumerates as
rem   "Microphone (USB Audio Device)" with its input unplugged - so the OLD device name
rem   still BINDS SUCCESSFULLY and records nothing while every health check stays green.
rem   That is why this file names the Comica explicitly.
rem
rem AUDIO NOTES (measured 2026-08-06, 180 s labelled take, a scratch folder)
rem   Device opens NATIVELY at 48000 Hz / 16 bit / stereo, so -sample_rate 48000 is
rem   requested here and the 44100->48000 resample the old chain needed is gone.
rem   Both channels are DUPLICATED MONO - measured, not assumed: L==R for 100% of samples
rem   on a quiet probe, max |L-R| = 3 LSB out of 32768, Pearson r = 0.999998 under real
rem   speech. Both transmitters SUM into both channels; this receiver does NOT put TX-A on
rem   left and TX-B on right. So -ac 1 is lossless here, not a 6 dB give-away.
rem   192k rather than the cameras' 128k: this is the primary voice track, and the bitrate
rem   is free next to any camera.
rem   LEVELS: mutter peak -23.2, conversational -15.4, full shout -11.6 dBFS, ZERO samples
rem   at full scale. Mutter-to-shout spans only 10.5 dB where a voice spans 30-40, so the
rem   RECEIVER IS COMPRESSING (AGC or limiter). Nothing clips, but the loud reactions this
rem   channel lives on are being flattened. Unresolved - see the vault note.
rem   GATE: still gates hard to TRUE DIGITAL SILENCE (100% of the tail after he stopped,
rem   peak -78 dBFS), so MIC1 delivers NO ROOM TONE and will cut audibly against camera
rem   audio that has some. It does NOT eat quiet speech though - a 22 s muttered aside came
rem   back 8% silent and fully intelligible, attack +25 to +40 dB in 30 ms. That closes the
rem   question left open on 2026-07-30.
rem 🔴 -threads 1 / sliced-threads=0 ARE LOAD-BEARING (2026-08-06). DO NOT REMOVE.
rem   `-tune zerolatency` switches x264 to SLICED THREADS, which emitted "slices=5" - five
rem   slice NAL units per frame. BLUE IRIS COUNTS SLICES AS FRAMES: it reported MIC1 at
rem   75.2 fps against a measured true 15 fps, exactly 5x, and recorded 980 frames into a
rem   14.269 s clip. Believing frames arrive 5x faster than they do, BI kept reconciling
rem   A/V sync by DISCARDING AUDIO - a measured 265 ms hole every 2.348 s (sd 15 ms), about
rem   11% of the voice track, cutting clean through mid-word speech at -22 dBFS.
rem   The bridge itself was never at fault: 60 s captured straight off the dshow device and
rem   60 s pulled off this RTSP path both contained ZERO zero-runs over 20 ms. Only the
rem   Blue Iris recording had holes. Fix the SLICING, not the audio path.
rem   This is the same defect as the known "bridge cams read isYellow" cosmetic issue - BI
rem   misreads the sliced x264 SUBSTREAMS. On the cameras that is harmless because their
rem   MAIN stream is NVENC and unsliced. MIC1 has no main stream, so it lands on the
rem   primary timeline and costs real audio.
rem DISPLAY GAIN 3x + RED TRACE (2026-08-08, user's call)
rem   volume=3 sits AFTER asplit, on the [awav] branch ONLY. The [aenc] branch that feeds the
rem   AAC encoder never sees it, so THE RECORDED VOICE TRACK IS BIT-IDENTICAL to before. This
rem   is a drawing change, not an audio change - the 2026-08-06 frozen-and-validated audio
rem   settings are untouched.
rem   WHY 3x: at 1x his real speech barely lifts off the centre line. Measured on
rem   comica_take1.wav: mutter peaks -23.2, conversational -15.4, full shout -11.6 dBFS, and
rem   showwaves scales linearly to full frame, so conversation was drawing about a sixth of
rem   the height and reading as a flat line across the shop. 3x is +9.5 dB.
rem   IT CANNOT PEG: the loudest thing ever measured on this mic is -11.6 dBFS, which +9.5 dB
rem   puts at -2.1 dBFS - still inside the frame. Anything louder just flat-tops the DRAWING;
rem   the audio is on the other branch and is unaffected either way.
rem   NO LATENCY COST: measured 2026-08-08, the visualiser branch emits its first frame after
rem   one frame period of audio (3200 samples at rate=15) whatever is drawn on it, and the
rem   voice never passes through it at all.
rem   The grid went dark red and the centre line red to match; the grid and centre line still
rem   exist for the original reason - showwaves on true silence draws NOTHING, and MIC1 gates
rem   to true digital silence, so without them "live but quiet" looks like a DEAD camera.
rem NB: continuation lines end with ^ - never insert a rem between them.
C:\shopcam2000-bridge\ffmpeg\ffmpeg.exe -hide_banner -y ^
  -f dshow -audio_buffer_size 80 -sample_rate 48000 -channels 2 ^
  -i audio="Microphone (Comica_BoomX-D)" ^
  -filter_complex "[0:a]asplit=2[aenc][awav];[awav]volume=3,showwaves=s=640x360:mode=cline:rate=15:colors=0xff0000,drawgrid=w=80:h=45:t=1:c=0x5f1e1e@0.7,drawbox=y=179:w=640:h=2:c=0xff0000@0.30:t=fill,format=yuv420p[v]" ^
  -map "[v]" -map "[aenc]" ^
  -c:v libx264 -preset veryfast -tune zerolatency -threads 1 -x264-params sliced-threads=0 -b:v 400k -maxrate 600k -bufsize 1M -g 15 ^
  -c:a aac -b:a 192k -ar 48000 -ac 1 ^
  -f rtsp -rtsp_transport tcp rtsp://127.0.0.1:8554/mic1 ^
  >> C:\shopcam2000-bridge\mic1_ffmpeg.log 2>&1
