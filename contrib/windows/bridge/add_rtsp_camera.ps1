# Add a Blue Iris camera that pulls one of the USB->RTSP bridge streams.
#
# Clones an existing WORKING network camera's key tree (so every network-specific
# field is already right) and overrides only the source and identity. Modelled on
# add_cam8.ps1, which is the proven recipe on this rig.
#
# BI is force-killed first: a forced kill means BI does not rewrite the registry on
# exit, which is what makes a scripted edit survive. Fully reversible - delete the
# key and restart.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File add_rtsp_camera.ps1 `
#       -Cam CAM10 -Template CAM4 -StreamPath /cam8 -Index 9 -Notes "throwaway"

param(
    [Parameter(Mandatory)][string]$Cam,
    [string]$Template  = 'CAM4',
    [Parameter(Mandatory)][string]$StreamPath,   # e.g. /cam8
    [string]$BridgeHost = '127.0.0.1',
    [int]$RtspPort      = 8554,
    [Parameter(Mandatory)][int]$Index,
    [string]$Notes      = ''
)

$ErrorActionPreference = 'Continue'
$CamRoot  = 'HKLM:\SOFTWARE\Perspective Software\Blue Iris\Cameras'
$CamRootR = 'HKLM\SOFTWARE\Perspective Software\Blue Iris\Cameras'
$tmpl     = "$env:USERPROFILE\$Cam`_from_$Template.reg"

if (Test-Path "$CamRoot\$Cam") { "$Cam already exists - aborting so nothing is clobbered."; return }
if (-not (Test-Path "$CamRoot\$Template")) { "template $Template not found - ABORT"; return }

# ---- 1. stop Blue Iris ------------------------------------------------------
if (Get-Process -Name BlueIris -ErrorAction SilentlyContinue) {
    "stopping BlueIris (force)..."
    Stop-Process -Name BlueIris -Force
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Seconds 1
        if (-not (Get-Process -Name BlueIris -ErrorAction SilentlyContinue)) { break }
    }
}
if (Get-Process -Name BlueIris -ErrorAction SilentlyContinue) { "*** BI STILL RUNNING - ABORT ***"; return }
"BlueIris stopped."

# ---- 2. clone the template subtree ------------------------------------------
reg export "$CamRootR\$Template" $tmpl /y | Out-Null
if (-not (Test-Path $tmpl)) { "*** export failed - ABORT ***"; return }
$txt = Get-Content $tmpl -Raw -Encoding Unicode
$txt = $txt -replace "\\Cameras\\$Template", "\Cameras\$Cam"
Set-Content -Path $tmpl -Value $txt -Encoding Unicode
$out = reg import $tmpl 2>&1
"import: $out"
if (-not (Test-Path "$CamRoot\$Cam")) { "*** $Cam key not created - ABORT ***"; return }

# ---- 3. repoint at the bridge -----------------------------------------------
$k = "$CamRoot\$Cam"

# dsname is BI's composite display/source string. Format taken from the two working
# examples on this rig:  user@ip:httpport/path/:rtspport:flag[substream]
#   CAM6 -> user@192.0.2.20:80/videoMain/:88:1[]      (an IP camera with RTSP on 88)
# MediaMTX runs without auth, so the user part is empty and there is no substream.
$dsname = "@{0}:80{1}/:{2}:1[]" -f $BridgeHost, $StreamPath, $RtspPort

$strings = @{
    shortname   = $Cam
    dsname      = $dsname
    devpath     = ''
    ip          = $BridgeHost
    ip_path     = $StreamPath
    ip_subpath  = ''          # no substream - the bridge publishes one stream
    ipid        = ''          # MediaMTX: no auth
    ippw        = ''
    audio_path  = ''          # audio arrives over RTSP, not a WDM device
    audio_name  = ''
    uuid        = [guid]::NewGuid().ToString()
    notes       = $Notes
}
foreach ($n in $strings.Keys) { Set-ItemProperty -Path $k -Name $n -Value $strings[$n] -Type String }

$dwords = @{
    type        = 4           # 4 = network camera (2 = DirectShow/USB)
    ip_port     = 80
    ip_port2    = $RtspPort
    ip_onvifuri = 0           # MediaMTX speaks no ONVIF - use ip_path verbatim
    ip_device   = 135         # the generic ONVIF/RTSP family used by CAM3/CAM4/CAM6
    ip_aformat  = 7           # AAC, as the bridge encodes
    audio       = 1
    enabled     = 1
}
foreach ($n in $dwords.Keys) { Set-ItemProperty -Path $k -Name $n -Value $dwords[$n] -Type DWord }

# Number and index must be unique. Cameras\Number is BI's next-number counter -
# take it and bump it, exactly as BI would.
$next = (Get-ItemProperty -Path $CamRoot -Name Number).Number
Set-ItemProperty -Path $k       -Name Number -Value $next       -Type DWord
Set-ItemProperty -Path $k       -Name index  -Value $Index      -Type DWord
Set-ItemProperty -Path $CamRoot -Name Number -Value ($next + 1) -Type DWord

"$Cam created: Number=$next index=$Index dsname='$dsname'"
foreach ($n in @('shortname','dsname','type','ip','ip_port2','ip_path','ip_device','ip_onvifuri','ip_aformat','enabled')) {
    "  {0,-12} = {1}" -f $n, (Get-ItemProperty -Path $k -Name $n -ErrorAction SilentlyContinue).$n
}

# ---- 4. start Blue Iris -----------------------------------------------------
"starting BlueIris..."
try { Stop-ScheduledTask -TaskName 'BI-Console-Interactive' -ErrorAction SilentlyContinue } catch {}
Start-Sleep -Seconds 2
try { Start-ScheduledTask -TaskName 'BI-Console-Interactive' -ErrorAction Stop }
catch { "*** TASK START FAILED: $($_.Exception.Message) ***" }

for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Seconds 1
    if (Get-Process -Name BlueIris -ErrorAction SilentlyContinue) { break }
}
$p = Get-Process -Name BlueIris -ErrorAction SilentlyContinue
if ($p) { "BlueIris PID $($p.Id) session $($p.SessionId)" } else { "*** BlueIris did NOT start ***" }

for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Seconds 1
    if (Test-NetConnection -ComputerName 127.0.0.1 -Port 81 -InformationLevel Quiet -WarningAction SilentlyContinue) {
        ":81 is up"; break
    }
}
"DONE"
