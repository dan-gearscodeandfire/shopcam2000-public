@echo off
title *** DO NOT CLOSE *** Shopcam 2000 bridge - CAM9  (desktop screen capture to RTSP)
echo ============================================================
echo.
echo    DO NOT CLOSE THIS WINDOW
echo.
echo    Shopcam 2000 - USB/screen to RTSP camera bridge
echo    CAM9  (desktop screen capture to RTSP)
echo.
echo    Closing this window takes this camera OFFLINE in Blue Iris.
echo    It is blank on purpose - output goes to:
echo      C:\shopcam2000-bridge\cam9_ffmpeg.log
echo.
echo    The supervisor will respawn it within ~60s if it dies.
echo.
echo ============================================================
rem Shopcam 2000 bridge - CAM9 (the shop PC's own desktop, second panel)
rem   MAIN  -> rtsp://127.0.0.1:8554/cam9      1080p30 H.264 8 Mbps CBR, GOP 30, no audio
rem   SUB   -> DISABLED 2026-08-19: BI consuming a bridge substream caused recording frame loss
rem
rem WHY BRIDGE A SCREEN CAPTURE AT ALL: CAM9 was a DirectShow "screen capture" camera
rem (UScreenCapture). BI treats that as a LOCAL device, not a network camera, so it is
rem not direct-to-disk and its stream buffer is capped at 1.0 s of raw RGB - the exact
rem limitation that made a 60 s pre-roll impossible on the USB cams. Writing
rem rectime/movieroll=600 to a screencap CAM9 is a silent no-op. Bridged to RTSP it
rem becomes a network camera and gets the same real 60 s pre-roll as CAM4/CAM8.
rem Bonus: BI's own screen encode measured ~19 Mbps (~145 MB/min); this is 8.
rem
rem ???? GEOMETRY IS IN PHYSICAL PIXELS, AND THAT NEEDS A DPI OPT-IN.
rem The console runs at 125% scaling. A DPI-UNAWARE process sees the desktop scaled to
rem 80%: DISPLAY3 reports 1536x864 @ -1536,2 instead of its true 1920x1080 @ -1920,3.
rem gdigrab would then capture a soft, downscaled panel that still looks "fine".
rem The fix is a per-app compatibility flag, set once for THIS ffmpeg.exe:
rem   HKCU\Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers
rem     "C:\shopcam2000-bridge\ffmpeg\ffmpeg.exe" = "~ HIGHDPIAWARE"
rem ?????? If that value is ever removed, the offsets below address the WRONG rectangle and
rem CAM9 silently films part of the primary panel. Verified 2026-07-27: with the flag,
rem a single gdigrab frame came back exactly 1920x1080 and correctly framed.
rem
rem MONITOR CHOICE: DISPLAY3 @ -1920,3 is the secondary panel (browser / Controller UI).
rem DISPLAY1 @ 0,0 is the 2560x1440 primary holding the Blue Iris console. Only TWO
rem panels are attached now - the three-monitor layout in docs/desktop-capture-cam9.md
rem is stale, and there is no DISPLAY2.
rem
rem NO AUDIO: a screen has no natural mic, and the other bridges bind their camera's own
rem device. BI gets a video-only stream; that is intended, not a dropped feature.
rem
rem gdigrab draws the cursor by default, which is wanted here - the pointer is part of
rem the story when the tile shows someone driving the UI.
rem
rem NB: continuation lines end with ^ - never insert a rem between them.
C:\shopcam2000-bridge\ffmpeg\ffmpeg.exe -hide_banner -y ^
  -f gdigrab -framerate 30 -video_size 1920x1080 -offset_x -1920 -offset_y 3 ^
  -i desktop ^
  -map 0:v ^
  -pix_fmt yuv420p ^
  -c:v h264_nvenc -preset p4 -tune ll -rc cbr -b:v 8M -maxrate 8M -bufsize 8M -g 30 -bf 0 -delay 0 ^
  -f rtsp -rtsp_transport tcp rtsp://127.0.0.1:8554/cam9 ^
  >> C:\shopcam2000-bridge\cam9_ffmpeg.log 2>&1

