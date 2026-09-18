@echo off
title *** DO NOT CLOSE *** Shopcam 2000 bridge - CAM8  (bench USB camera to RTSP)
echo ============================================================
echo.
echo    DO NOT CLOSE THIS WINDOW
echo.
echo    Shopcam 2000 - USB/screen to RTSP camera bridge
echo    CAM8  (bench USB camera to RTSP)
echo.
echo    Closing this window takes this camera OFFLINE in Blue Iris.
echo    It is blank on purpose - output goes to:
echo      C:\shopcam2000-bridge\cam8_ffmpeg.log
echo.
echo    The supervisor will respawn it within ~60s if it dies.
echo.
echo ============================================================
rem Shopcam 2000 bridge - CAM8 (generic "USB Camera" / H264 USB Camera bench cam)
rem   MAIN  -> rtsp://127.0.0.1:8554/cam8      1080p30 H.264 8 Mbps CBR, GOP 30, +AAC
rem   SUB   -> DISABLED 2026-08-19: BI consuming a bridge substream caused recording frame loss
rem
rem WHY A SUBSTREAM: Blue Iris had no low-res stream for these bridged cameras, so its
rem live grid was decoding the full 1080p main stream and updating roughly once every
rem 10 s. IP cams already ship a 640x480/15 substream (CAM4/CAM7 confirmed) - this
rem gives the USB cameras the same thing. Point BI's sub-stream field at /cam8_sub.
rem
rem GOP 30 (1.0 s, was 60/2.0 s): measured +11.4% size at equal quality, and it is the
rem single biggest lever on timeline scrubbing. Keyframes align across all cameras at
rem 1 s, which also helps multicam.
rem
rem ???? SUBSTREAM IS MJPEG (A/B TEST vs CAM1/CAM5, which use H.264).
rem Every MJPEG frame is a keyframe, so a preview can draw immediately instead of
rem waiting up to a full GOP - which is why an H.264 substream can still feel sluggish
rem in the BI grid even at 15 fps. Costs ~1.3 Mbps vs ~0.5 Mbps for H.264: irrelevant
rem on loopback, and it is never recorded (BI records the MAIN stream).
rem ?????? TWO ffmpeg defaults both break RTP/JPEG and BOTH must be overridden:
rem   -pix_fmt yuvj420p  : RTP/JPEG cannot carry 4:4:4 -> "Only 1x1 chroma blocks are
rem                        supported"
rem   -huffman default   : the mjpeg encoder optimises Huffman tables by default ->
rem                        "RFC 2435 requires standard Huffman tables for jpeg"
rem Each fails as a flood of RTP errors while ffmpeg still reports encoding frames, so
rem the log looks busy and healthy while nothing is published.
rem ?????? RTP/JPEG signals no dimensions in SDP, so ffprobe reports width/height 0. That is
rem normal; the decoder learns geometry from the first frame.
rem Not NVENC: 3 cameras x 2 NVENC sessions = 6, and consumer GeForce drivers have
rem historically capped concurrent sessions.
rem
rem MJPEG pin only: the native h264 pin enumerates but delivers zero frames (0-for-3
rem across two vendors, measured 2026-07-25). Video+audio in ONE dshow input so they
rem share a clock. -pix_fmt is load-bearing (see run_cam1.cmd).
rem
rem NB: continuation lines end with ^ - never insert a rem between them.
C:\shopcam2000-bridge\ffmpeg\ffmpeg.exe -hide_banner -y ^
  -f dshow -vcodec mjpeg -video_size 1920x1080 -framerate 30 ^
  -audio_buffer_size 80 ^
  -use_wallclock_as_timestamps 1 ^
  -i video="USB Camera":audio="Microphone (H264 USB Camera)" ^
  -map 0:v -map 0:a ^
  -pix_fmt yuvj420p ^
  -vf fps=30 ^
  -c:v h264_nvenc -preset p4 -tune ll -rc cbr -b:v 8M -maxrate 8M -bufsize 8M -g 30 -bf 0 -delay 0 ^
  -c:a aac -b:a 128k -ar 48000 -ac 1 ^
  -f rtsp -rtsp_transport tcp rtsp://127.0.0.1:8554/cam8 ^
  >> C:\shopcam2000-bridge\cam8_ffmpeg.log 2>&1

