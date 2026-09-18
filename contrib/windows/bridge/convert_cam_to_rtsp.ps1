# Convert an existing USB (DirectShow) Blue Iris camera into an RTSP network camera
# that pulls its own USB->RTSP bridge stream.
#
# Rather than hand-setting the ~15 fields that differ between a USB and a network
# camera -- where one missed field is a silent misconfiguration -- this copies a
# PROVEN network-camera key tree wholesale and then restores the target's identity.
# The proven tree comes from a throwaway camera that was verified online, in colour,
# and delivering real pre-roll before this script was ever run.
#
#   powershell -File convert_cam_to_rtsp.ps1 -Cam CAM8 -ProvenSource CAM10 -DeleteSource
#
# Rollback: kill BI, delete the CAM8 key, `reg import %USERPROFILE%\cam8_usb_fallback.reg`,
# start BI. That file is a full export of the original USB definition.

param(
    [Parameter(Mandatory)][string]$Cam,
    [Parameter(Mandatory)][string]$ProvenSource,
    [Parameter(Mandatory)][string]$StreamPath,   # e.g. /cam1 -- the source's path is cloned otherwise
    [string]$BridgeHost = '127.0.0.1',
    [int]$RtspPort      = 8554,
    [switch]$DeleteSource,
    # Buffer staged on EVERY profile: a camera with a stream buffer gives 60 s of
    # pre-roll on MANUAL recording too, not just on trigger (measured 2026-07-27),
    # so staging it everywhere means a take can never start too late.
    [double]$Pretrigger = 60
)

$ErrorActionPreference = 'Continue'
$CamRoot  = 'HKLM:\SOFTWARE\Perspective Software\Blue Iris\Cameras'
$CamRootR = 'HKLM\SOFTWARE\Perspective Software\Blue Iris\Cameras'

if (-not (Test-Path "$CamRoot\$Cam"))          { "$Cam not found - ABORT"; return }
if (-not (Test-Path "$CamRoot\$ProvenSource")) { "$ProvenSource not found - ABORT"; return }

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

# ---- 2. preserve the target's identity + a fallback -------------------------
$old      = Get-ItemProperty "$CamRoot\$Cam"
$oldNum   = $old.Number
$oldIndex = $old.index
$oldNotes = $old.notes
"$Cam identity: Number=$oldNum index=$oldIndex"

$fallback = "$env:USERPROFILE\$Cam`_usb_fallback.reg"
reg export "$CamRootR\$Cam" $fallback /y | Out-Null
if (-not (Test-Path $fallback)) { "*** fallback export failed - ABORT ***"; return }
"fallback saved: $fallback"

# ---- 3. replace the tree with the proven one --------------------------------
$tmp = "$env:USERPROFILE\$Cam`_from_$ProvenSource.reg"
reg export "$CamRootR\$ProvenSource" $tmp /y | Out-Null
$txt = Get-Content $tmp -Raw -Encoding Unicode
$txt = $txt -replace "\\Cameras\\$ProvenSource", "\Cameras\$Cam"
Set-Content -Path $tmp -Value $txt -Encoding Unicode

Remove-Item -Path "$CamRoot\$Cam" -Recurse -Force
$out = reg import $tmp 2>&1
"import: $out"
if (-not (Test-Path "$CamRoot\$Cam")) { "*** $Cam key missing after import - RESTORE FROM $fallback ***"; return }

# ---- 4. restore identity ----------------------------------------------------
$k = "$CamRoot\$Cam"
Set-ItemProperty -Path $k -Name shortname -Value $Cam -Type String
Set-ItemProperty -Path $k -Name uuid  -Value ([guid]::NewGuid().ToString()) -Type String
Set-ItemProperty -Path $k -Name notes -Value "$oldNotes | converted to RTSP bridge 2026-07-27" -Type String
Set-ItemProperty -Path $k -Name Number -Value $oldNum   -Type DWord
Set-ItemProperty -Path $k -Name index  -Value $oldIndex -Type DWord

# ---- 4b. point it at ITS OWN stream -----------------------------------------
# The cloned tree carries the proven source's path (e.g. /cam8). Without this the
# converted camera would silently show the WRONG camera's picture -- and it would
# look perfectly healthy while doing it.
$dsname = "@{0}:80{1}/:{2}:1[]" -f $BridgeHost, $StreamPath, $RtspPort
Set-ItemProperty -Path $k -Name ip_path -Value $StreamPath -Type String
Set-ItemProperty -Path $k -Name dsname  -Value $dsname     -Type String
Set-ItemProperty -Path $k -Name ip      -Value $BridgeHost -Type String
Set-ItemProperty -Path $k -Name ip_port2 -Value $RtspPort  -Type DWord

# ---- 5. stage the rolling buffer on every profile ---------------------------
# 🔑 rectime alone does NOTHING without movieroll -- movieroll IS the stream
# buffer time (deciseconds), despite the name suggesting file rollover. With
# movieroll at its 50 default, a 60 s pretrigger silently delivers ~4 s.
$ds = [int]($Pretrigger * 10)
foreach ($p in 1..7) {
    $pk = "$k\Clips\$p"
    if (Test-Path $pk) {
        Set-ItemProperty -Path $pk -Name rectime   -Value $ds -Type DWord
        Set-ItemProperty -Path $pk -Name playtime  -Value $ds -Type DWord
        Set-ItemProperty -Path $pk -Name movieroll -Value $ds -Type DWord
    }
}
"  buffer staged on profiles 1-7: rectime=$ds movieroll=$ds ($Pretrigger s)"

if ($DeleteSource) {
    Remove-Item -Path "$CamRoot\$ProvenSource" -Recurse -Force
    "removed throwaway $ProvenSource"
}

"$Cam now:"
foreach ($n in @('shortname','dsname','type','ip','ip_port2','ip_path','ip_device','enabled','Number','index')) {
    "  {0,-12} = {1}" -f $n, (Get-ItemProperty -Path $k -Name $n -ErrorAction SilentlyContinue).$n
}

# ---- 6. start Blue Iris -----------------------------------------------------
"starting BlueIris..."
try { Stop-ScheduledTask -TaskName 'BI-Console-Interactive' -ErrorAction SilentlyContinue } catch {}
Start-Sleep -Seconds 2
try { Start-ScheduledTask -TaskName 'BI-Console-Interactive' -ErrorAction Stop }
catch { "*** TASK START FAILED: $($_.Exception.Message) ***" }
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Seconds 1
    if (Test-NetConnection -ComputerName 127.0.0.1 -Port 81 -InformationLevel Quiet -WarningAction SilentlyContinue) { ":81 is up"; break }
}
"DONE"
