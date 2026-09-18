@echo off
title *** DO NOT CLOSE *** Shopcam 2000 bridge - CAM5  (OBSBOT Tiny, USB to RTSP)
echo ============================================================
echo.
echo    DO NOT CLOSE THIS WINDOW
echo.
echo    Shopcam 2000 - USB/screen to RTSP camera bridge
echo    CAM5  (OBSBOT Tiny, USB to RTSP)
echo.
echo    Closing this window takes this camera OFFLINE in Blue Iris.
echo    It is blank on purpose - output goes to:
echo      C:\shopcam2000-bridge\cam5_ffmpeg.log
echo.
echo    The supervisor will respawn it within ~60s if it dies.
echo.
echo ============================================================
rem Shopcam 2000 bridge - CAM5 (OBSBOT Tiny)
rem   MAIN -> rtsp://127.0.0.1:8554/cam5      1080p30 H.264 8 Mbps CBR, GOP 30, +AAC
rem   SUB  -> DISABLED 2026-08-19: BI consuming a bridge substream caused recording frame loss
rem
rem AUDIO: binds the OBSBOT Tiny's OWN microphone.
rem   CHANGED 2026-07-30. It previously bound the C-Media "Microphone (USB Audio Device)",
rem   inherited from what Blue Iris chose before the cutover. That C-Media device is the
rem   USB adapter carrying the user's LAV MIC (and the PC's speaker output), and the lav
rem   is his A-ROLL AUDIO -- so it now belongs to MIC1, its own Blue Iris track, and must
rem   not be tied to a camera angle. See run_mic1.cmd.
rem   Measured 2026-07-30: the C-Media input CAN be opened twice concurrently, so this
rem   rebind is about editorial ownership, not device contention.
rem   Sibling levels, same room, same minute: OBSBOT Tiny mic -54.7 dB mean / -18.6 peak.
rem
rem Substream is libx264 (CPU) not NVENC - see run_cam1.cmd for why.
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
  -i video="OBSBOT Tiny Camera":audio="OBSBOT Tiny Microphone (4- OBSBOT Tiny Audio)" ^
  -map 0:v -map 0:a ^
  -af aresample=async=1000:first_pts=0 ^
  -pix_fmt yuvj420p ^
  -c:v h264_nvenc -preset p4 -tune ll -rc cbr -b:v 8M -maxrate 8M -bufsize 8M -g 30 -bf 0 -delay 0 ^
  -c:a aac -b:a 128k -ar 48000 -ac 1 ^
  -f rtsp -rtsp_transport tcp rtsp://127.0.0.1:8554/cam5 ^
  >> C:\shopcam2000-bridge\cam5_ffmpeg.log 2>&1
