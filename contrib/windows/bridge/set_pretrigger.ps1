# Set a camera's pre-trigger buffer for one Blue Iris profile.
#
# These are the fields the BI GUI exposes as "pre-trigger video buffer": they live
# at Cameras\<cam>\Clips\<profile>\ and are in DECISECONDS (60.0 s = 600). Every
# camera has 7 profile subkeys, which is what makes the two-mode design work:
# profile 1 = 0 (no pre-buffer, today's behaviour), profile 2 = 600 (perma-watch).
#
# BI must be stopped: a forced kill means it does not rewrite the registry on exit.
#
#   powershell -File set_pretrigger.ps1 -Cam CAM10 -Profile 1 -Seconds 60
#
# NOTE: on a USB camera these fields WRITE but do nothing -- measured 2026-07-23,
# a 15 s change moved the saved clip by 10 ms. That is the whole reason the bridge
# exists. Only meaningful on a network camera with direct-to-disk recording.

param(
    [Parameter(Mandatory)][string]$Cam,
    [int]$Profile = 1,
    [Parameter(Mandatory)][double]$Seconds,
    [double]$BufferSeconds = -1,   # stream buffer; defaults to matching $Seconds
    [switch]$NoRestart
)

$ErrorActionPreference = 'Continue'
$k  = "HKLM:\SOFTWARE\Perspective Software\Blue Iris\Cameras\$Cam\Clips\$Profile"
$ds = [int]($Seconds * 10)

if (-not (Test-Path $k)) { "profile key $k not found - ABORT"; return }

if (Get-Process -Name BlueIris -ErrorAction SilentlyContinue) {
    "stopping BlueIris (force)..."
    Stop-Process -Name BlueIris -Force
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Seconds 1
        if (-not (Get-Process -Name BlueIris -ErrorAction SilentlyContinue)) { break }
    }
}
if (Get-Process -Name BlueIris -ErrorAction SilentlyContinue) { "*** BI STILL RUNNING - ABORT ***"; return }

# 🔑 rectime ALONE IS NOT ENOUGH. The pre-trigger can only write what the stream
# buffer is holding, and the stream buffer is `movieroll` -- also deciseconds, and
# badly named (it is not a file-rollover interval). Measured on CAM4 2026-07-27:
#   rectime=600, movieroll=50  -> 4.1 s of pre-roll  (capped by the ~5 s buffer floor)
#   rectime=600, movieroll=600 -> 58.7 s of pre-roll  <- what we want
# Leaving movieroll at its default is exactly why a 60 s pre-trigger silently
# delivered ~4 s and looked like the setting "did nothing".
$bufDs = if ($BufferSeconds -lt 0) { $ds } else { [int]($BufferSeconds * 10) }

$b = Get-ItemProperty $k
"before: rectime=$($b.rectime) playtime=$($b.playtime) movieroll=$($b.movieroll)"
Set-ItemProperty -Path $k -Name rectime   -Value $ds    -Type DWord
Set-ItemProperty -Path $k -Name playtime  -Value $ds    -Type DWord
Set-ItemProperty -Path $k -Name movieroll -Value $bufDs -Type DWord
$a = Get-ItemProperty $k
"after:  rectime=$($a.rectime) playtime=$($a.playtime) movieroll=$($a.movieroll)  (pretrigger $Seconds s, buffer $($bufDs/10) s)"

if (-not $NoRestart) {
    "starting BlueIris..."
    try { Stop-ScheduledTask -TaskName 'BI-Console-Interactive' -ErrorAction SilentlyContinue } catch {}
    Start-Sleep -Seconds 2
    try { Start-ScheduledTask -TaskName 'BI-Console-Interactive' -ErrorAction Stop }
    catch { "*** TASK START FAILED: $($_.Exception.Message) ***" }
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 1
        if (Test-NetConnection -ComputerName 127.0.0.1 -Port 81 -InformationLevel Quiet -WarningAction SilentlyContinue) { ":81 is up"; break }
    }
}
"DONE"
