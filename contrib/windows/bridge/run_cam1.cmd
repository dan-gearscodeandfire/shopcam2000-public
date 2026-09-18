@echo off
title *** DO NOT CLOSE *** Shopcam 2000 bridge - CAM1  (OBSBOT Tiny 4K, USB to RTSP)
echo ============================================================
echo.
echo    DO NOT CLOSE THIS WINDOW
echo.
echo    Shopcam 2000 - USB/screen to RTSP camera bridge
echo    CAM1  (OBSBOT Tiny 4K, USB to RTSP)
echo.
echo    Closing this window takes this camera OFFLINE in Blue Iris.
echo    It is blank on purpose - output goes to:
echo      C:\shopcam2000-bridge\cam1_ffmpeg.log
echo.
echo    The supervisor will respawn it within ~60s if it dies.
echo.
echo ============================================================
rem Shopcam 2000 bridge - CAM1 (OBSBOT Tiny 4K)
rem   MAIN -> rtsp://127.0.0.1:8554/cam1      1080p30 H.264 8 Mbps CBR, GOP 30, +AAC
rem   SUB  -> DISABLED 2026-08-19: BI consuming a bridge substream caused recording frame loss
rem
rem CAM1 is the FINALIZED fleet colour reference. Re-verify against
rem var/calibrate/FLEET-LAST-KNOWN-GOOD.json after any repoint - if CAM1's look moves,
rem every other camera's match to it is invalidated.
rem
rem ???? -pix_fmt IS LOAD-BEARING. Left unset, ffmpeg picked yuvj444p for this camera
rem (High 4:4:4 H.264) while CAM8 got yuvj420p. Blue Iris decodes 4:4:4 differently and
rem CAM1 rendered 5.76pp off in R/G. An ffmpeg-to-ffmpeg check cannot see this - the
rem bridge output is fine, it is the CONSUMER's decode that differs.
rem
rem Substream is libx264 (CPU) not NVENC: 3 cameras x 2 NVENC sessions = 6, and consumer
rem GeForce drivers have historically capped concurrent sessions. 640x480@15 on x264 is
rem negligible CPU and removes the risk.
rem
rem MJPEG pin only - the native h264 pin delivers zero frames on this rig.
rem AUDIO TIMING (2026-09-09): CAM audio was drifting vs MIC1 in discrete 200 ms steps - dshow
rem   dropped audio chunks while timestamps stayed continuous, so A/V slid inside the file.
rem   Fix: wall-clock timestamps on the input + aresample async so a lost chunk becomes a
rem   short silence at the right place instead of a permanent shift. audio_buffer_size 80 -> 500.
rem NB: continuation lines end with ^ - never insert a rem between them.
C:\shopcam2000-bridge\ffmpeg\ffmpeg.exe -hide_banner -y ^
  -f dshow -vcodec mjpeg -video_size 1920x1080 -framerate 30 ^
  -rtbufsize 256M -audio_buffer_size 500 ^
  -use_wallclock_as_timestamps 1 ^
  -i video="OBSBOT Tiny 4K Camera":audio="OBSBOT Tiny 4K Microphone (OBSBOT Tiny 4K Audio)" ^
  -map 0:v -map 0:a ^
  -af aresample=async=1000:first_pts=0 ^
  -pix_fmt yuvj420p ^
  -c:v h264_nvenc -preset p4 -tune ll -rc cbr -b:v 8M -maxrate 8M -bufsize 8M -g 30 -bf 0 -delay 0 ^
  -c:a aac -b:a 128k -ar 48000 -ac 1 ^
  -f rtsp -rtsp_transport tcp rtsp://127.0.0.1:8554/cam1 ^
  >> C:\shopcam2000-bridge\cam1_ffmpeg.log 2>&1

