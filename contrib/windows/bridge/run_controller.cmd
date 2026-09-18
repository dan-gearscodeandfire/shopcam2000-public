@echo off
rem Shopcam 2000 - the Controller (FastAPI + PWA), on the rig itself.
rem
rem It lives here rather than on the dev box because the physical TWAB button
rem posts to it, and a button that depends on a second machine being awake is
rem not a button. Blue Iris is local from here, so config.toml points at
rem 127.0.0.1 and /api/clip is a loopback proxy.
rem
rem python.exe, NEVER pythonw.exe. __main__.py prints a startup banner and under
rem pythonw sys.stdout is None, so the app dies instantly and silently. This cost
rem a previous session an hour.
rem
rem Logging goes to STDERR, so controller.err.log is the monitor - that is where
rem TWAB presses land, not controller.log.
cd /d C:\shopcam2000
if not exist var mkdir var
.venv\Scripts\python.exe -m shopcam2000 >> var\controller.log 2>> var\controller.err.log
