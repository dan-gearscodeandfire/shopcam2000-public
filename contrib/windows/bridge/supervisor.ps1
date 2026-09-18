# Shopcam 2000 â€” rig supervisor.
#
# Blue Iris has a scheduled task. Nothing else did: MediaMTX and the ffmpeg
# bridges ran as bare detached processes, so a crash left a camera dark until a
# human noticed. Once CAM1/CAM5/CAM8 are all bridged that is station 1 â€” ~80% of
# the footage â€” hanging off unmanaged processes.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File supervisor.ps1
#   powershell ... -File supervisor.ps1 -Once        # one pass, for testing
#   powershell ... -File supervisor.ps1 -WhatIfStart # report only, start or stop nothing
#
# It also decides which bridges are wanted at all, from bridges.state.json -
# see DESIRED STATE below. Bridges are OFF until something turns them on.
#
# ðŸ”‘ HEALTH IS MEASURED ON THE TRANSPORT THE CONSUMER USES, NOT ON THE PROCESS.
# "Is ffmpeg running" would have passed all through 2026-07-26 while CAM6 sat
# there delivering zero frames, and it was a snapshot-vs-RTSP mix-up that made us
# blame Blue Iris for a camera fault in the first place. So a bridge is healthy
# only if a frame can actually be pulled off its RTSP path.

param(
    [int]$IntervalSeconds = 30,
    [switch]$Once,
    [switch]$WhatIfStart
)

$ErrorActionPreference = 'Continue'
$Root    = 'C:\shopcam2000-bridge'
$FFprobe = "$Root\ffmpeg\ffprobe.exe"
$LogFile = "$Root\supervisor.log"
$MaxLogBytes = 4MB

# Restart policy: a component that keeps dying must not be hammered. After
# $BurstLimit restarts inside $BurstWindowSeconds it is parked for $CooldownSeconds
# and logged loudly â€” a bridge that cannot stay up is a fault to look at, not a
# thing to respawn 2000 times overnight.
$BurstLimit       = 3
$BurstWindowSeconds = 300
$CooldownSeconds  = 600

# How often to repeat an alert about an OBSERVED component that is still down.
# It used to be logged every cycle, which at a 30 s interval buried the log: on
# 2026-07-28, 8,183 of 8,881 lines were one repeated "controller is DOWN" alert,
# and the bridge restart history — 56 restarts over two days, the thing anyone
# would actually want from this file — was invisible underneath it. A monitor
# nobody can read is not a monitor. Alerts now fire on the EDGE plus a reminder
# on this interval, and recovery is logged too.
$ObservedRepeatSeconds = 600

# --------------------------------------------------------------------------
# DESIRED STATE: which bridges are wanted at all.
#
# Four ffmpeg encoders running 24/7 cost real electricity - 4 NVENC sessions
# plus 4 libx264 substreams - and most of the time nobody is filming. So the
# Controller can ask for a bridge to be off, and it does that by writing a file
# rather than by killing processes: a killed process is exactly what this
# supervisor exists to undo, and it would have been back inside 30 s with a
# WARN in the log for good measure.
#
# The Controller declares intent, the supervisor owns start and stop. That
# keeps the restart-burst policy, the dependency ordering and the alerting in
# one place - here - instead of split across two programs that disagree.
#
#   C:\shopcam2000-bridge\bridges.state.json
#   { "version": 1, "bridges": { "cam1": true, "cam5": false, ... } }
#
# DEFAULT IS OFF. No file, no key, or an unreadable file that has never been
# read successfully = the bridge is not wanted. A camera that costs money to
# leave running should require someone to have said yes, not to have forgotten
# to say no.
#
# A disabled component is SKIPPED, not alerted on. Alerting on a camera that
# was deliberately turned off is the same noise that buried this log on
# 2026-07-28, and it is also wrong: nothing is broken.
$StateFile = "$Root\bridges.state.json"
$DefaultBridgeEnabled = $false

# --------------------------------------------------------------------------
# Components. Adding CAM1/CAM5 later is one line each â€” that is the point.
#
# `Toggle` marks a component the Controller may turn off. Its Name doubles as
# the RTSP path and as the run_<name>.cmd script name, which is what makes
# Stop-Bridge able to find the right processes without a PID file.
# --------------------------------------------------------------------------
$Components = @(
    @{
        Name    = 'mediamtx'
        # The RTSP server itself. Checked by port, because a listening socket is
        # exactly what every bridge and Blue Iris depends on.
        Check   = { Test-Port -Port 8554 }
        Start   = { Start-Detached -File 'cmd.exe' -Arguments "/c $Root\run_mediamtx.cmd" }
        # Restarting MediaMTX invalidates every published path, so the bridges
        # must be restarted after it. Declared, not assumed.
        Invalidates = @('cam8','cam1','cam5','cam9','mic1')
    }
    @{
        Name   = 'cam8'
        Toggle = $true
        Check  = { Test-RtspFrame -Path 'cam8' }
        Start  = { Start-Detached -File 'cmd.exe' -Arguments "/c $Root\run_cam8.cmd" }
    }
    @{
        # CAM1 is the finalised fleet colour reference (run_cam1.cmd says so in
        # its own header). Turning it off is cheap; turning it back on is free.
        # Changing HOW it comes back up is not - see the note in run_cam1.cmd.
        Name   = 'cam1'
        Toggle = $true
        Check  = { Test-RtspFrame -Path 'cam1' }
        Start  = { Start-Detached -File 'cmd.exe' -Arguments "/c $Root\run_cam1.cmd" }
    }
    @{
        Name   = 'cam5'
        Toggle = $true
        Check  = { Test-RtspFrame -Path 'cam5' }
        Start  = { Start-Detached -File 'cmd.exe' -Arguments "/c $Root\run_cam5.cmd" }
    }
    @{
        # The desktop capture. Bridged for the same reason as the USB cams: as a
        # DirectShow screencap device it was not direct-to-disk, so its stream buffer
        # was capped at 1.0 s and a 60 s pre-roll was impossible. See run_cam9.cmd.
        Name   = 'cam9'
        Toggle = $true
        Check  = { Test-RtspFrame -Path 'cam9' }
        Start  = { Start-Detached -File 'cmd.exe' -Arguments "/c $Root\run_cam9.cmd" }
    }
    @{
        # MIC1 is the lav mic, and it is the user's A-ROLL AUDIO - the voice track.
        #
        # NO Toggle, DELIBERATELY. Every other bridge here can be switched off because
        # four NVENC sessions plus four x264 substreams cost 6.8 W GPU and 34pp CPU. MIC1
        # is one AAC stream and a 640x360 waveform, which is a rounding error against
        # that, and the failure switching it off would buy is the worst one on the rig: a
        # moment happens, nine angles catch it, and there is no voice on any of them.
        #
        # "On is implied, off is only ever explicit" does NOT cover this. Arming a camera
        # wakes that camera's encoder - it has no reason to wake a microphone. So the mic
        # is simply always on, and the rule "everything with an encoder can be switched
        # off" now has exactly one documented exception, which is this one.
        #
        # It passes Test-RtspFrame unmodified because it publishes a real video stream:
        # the waveform. That is why there is a picture on a microphone. See run_mic1.cmd.
        Name   = 'mic1'
        Check  = { Test-RtspFrame -Path 'mic1' }
        Start  = { Start-Detached -File 'cmd.exe' -Arguments "/c $Root\run_mic1.cmd" }
    }
)

# Blue Iris and the Controller are CHECKED but never auto-restarted here.
# BI owns its own scheduled task and restarting it drops all nine cameras â€” that
# is a decision for a human, not a 30-second loop. The Controller is not yet
# deployed to this box; when it is, give it a Start block.
$Observed = @(
    @{ Name = 'blueiris';   Check = { (Get-Process BlueIris -EA SilentlyContinue) -and (Test-Port -Port 81) } }
    @{ Name = 'controller'; Check = { Test-Port -Port 8787 } }
)

# --------------------------------------------------------------------------
function Write-Log {
    # âš ï¸ Write-Host, NOT Write-Output. Write-Output puts the log line on the
    # PIPELINE, so a log call inside a function silently becomes part of that
    # function's return value: `if (Invoke-Restart ...)` then sees @(string, bool),
    # which is always truthy, and the line never reaches the console. Cost us a
    # debugging session on 2026-07-27 â€” the file log had entries the console did not.
    param([string]$Level, [string]$Message)
    $line = "{0} [{1}] {2}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Level, $Message
    Write-Host $line
    try {
        if ((Test-Path $LogFile) -and ((Get-Item $LogFile).Length -gt $MaxLogBytes)) {
            Move-Item $LogFile "$LogFile.1" -Force
        }
        Add-Content -Path $LogFile -Value $line -Encoding utf8
    } catch {}
}

function Test-Port {
    param([int]$Port, [string]$ComputerName = '127.0.0.1')
    try {
        $c = New-Object Net.Sockets.TcpClient
        $ok = $c.ConnectAsync($ComputerName, $Port).Wait(2000)
        $c.Close()
        return $ok
    } catch { return $false }
}

function Test-RtspFrame {
    # Pull one frame. Anything else â€” process alive, port open, ffmpeg log looking
    # busy â€” can be true while the stream delivers nothing.
    param([string]$Path, [int]$TimeoutSeconds = 12)
    $url = "rtsp://127.0.0.1:8554/$Path"
    try {
        $p = Start-Process -FilePath $FFprobe -PassThru -WindowStyle Hidden `
             -ArgumentList @('-v','error','-rtsp_transport','tcp','-timeout','8000000',
                             '-select_streams','v:0','-show_entries','stream=codec_name',
                             '-of','csv=p=0','-i',$url)
        if (-not $p.WaitForExit($TimeoutSeconds * 1000)) {
            try { $p.Kill() } catch {}
            return $false
        }
        return ($p.ExitCode -eq 0)
    } catch { return $false }
}

function Start-Detached {
    # ðŸ”‘ WMI Win32_Process.Create, NOT Start-Process. A process started with
    # Start-Process is a child of this PowerShell and dies with it â€” over SSH the
    # whole job object is torn down on disconnect, so the bridge came up, wrote
    # about eight frames, and vanished. Win32_Process.Create spawns a genuinely
    # detached process that outlives its launcher. This is the same reason the
    # bridge was always launched this way by hand (shopcam-usb-camera-stack).
    #
    # NB: the parameter is $Arguments, NOT $Args â€” `$Args` is a PowerShell
    # automatic variable and declaring it as a parameter silently yields "".
    param([string]$File, [string]$Arguments)
    $cmdline = if ($Arguments) { "`"$File`" $Arguments" } else { "`"$File`"" }
    if ($WhatIfStart) { Write-Log 'WOULD' "start: $cmdline"; return }
    try {
        $r = ([wmiclass]'Win32_Process').Create($cmdline)
        if ($r.ReturnValue -ne 0) { Write-Log 'ERROR' "spawn failed rc=$($r.ReturnValue): $cmdline" }
        else { Write-Log 'INFO' "spawned pid $($r.ProcessId): $cmdline" }
    } catch {
        Write-Log 'ERROR' "spawn threw: $_"
    }
}

# --------------------------------------------------------------------------
# Desired state, read from disk once per cycle.
$script:Desired      = @{}   # name -> bool, from bridges.state.json
$script:DesiredKnown = $false # has a read ever succeeded?
$script:Announced    = @{}   # name -> bool, what we last logged about it

function Get-StateStamp {
    try {
        if (Test-Path $StateFile) { return (Get-Item $StateFile).LastWriteTimeUtc.Ticks }
    } catch {}
    return 0
}

function Read-Desired {
    # An absent file is a valid answer, not a failure: it means nothing has been
    # turned on yet, and the default is off. An unreadable one is different - the
    # Controller writes via a temp file and a rename, so a read can lose that
    # race - and there the last good answer is kept rather than shutting four
    # cameras down over a transient IO error.
    if (-not (Test-Path $StateFile)) {
        if ($script:Desired.Count -gt 0) {
            Write-Log 'WARN' 'bridges.state.json is gone - every bridge falls back to the default (off)'
        }
        $script:Desired = @{}
        $script:DesiredKnown = $true
        return
    }
    try {
        $raw = [IO.File]::ReadAllText($StateFile)
        if (-not $raw.Trim()) { throw 'file is empty' }
        $obj = $raw | ConvertFrom-Json
    } catch {
        Write-Log 'WARN' "could not read bridges.state.json ($_) - keeping the last known desired state"
        return
    }
    $map = @{}
    if ($obj.bridges) {
        foreach ($prop in $obj.bridges.PSObject.Properties) {
            $map[$prop.Name] = [bool]$prop.Value
        }
    }
    $script:Desired = $map
    $script:DesiredKnown = $true
}

function Test-BridgeWanted {
    param([hashtable]$Comp)
    if (-not $Comp.Toggle) { return $true }   # not toggleable: always wanted
    if ($script:Desired.ContainsKey($Comp.Name)) { return [bool]$script:Desired[$Comp.Name] }
    return $DefaultBridgeEnabled
}

function Get-BridgeProcesses {
    # Both halves of a bridge: the cmd.exe wrapper started by Start-Detached and
    # the ffmpeg it is waiting on. Identified by command line rather than a PID
    # file, because the PID file would go stale the moment anything started a
    # bridge by hand - which is how they were run for the first week.
    param([string]$Name)
    $all = @(Get-CimInstance Win32_Process -Filter "Name='ffmpeg.exe' or Name='cmd.exe'" -ErrorAction SilentlyContinue)
    $ff  = @($all | Where-Object { $_.Name -eq 'ffmpeg.exe' -and $_.CommandLine -match "8554/$Name[ _]" })
    $sh  = @($all | Where-Object { $_.Name -eq 'cmd.exe'    -and $_.CommandLine -match "run_$Name\.cmd" })
    return @{ FFmpeg = $ff; Shell = $sh }
}

function Stop-Bridge {
    param([string]$Name)
    $procs = Get-BridgeProcesses -Name $Name
    $pids  = @($procs.FFmpeg + $procs.Shell | ForEach-Object { $_.ProcessId })
    if (-not $pids.Count) { return 0 }
    if ($WhatIfStart) { Write-Log 'WOULD' "stop ${Name}: pid $($pids -join ', ')"; return 0 }
    # ffmpeg first. Kill the cmd.exe wrapper first and ffmpeg is orphaned but
    # very much alive, still holding the USB camera and still on the GPU - which
    # is the one outcome this whole feature exists to avoid.
    foreach ($p in @($procs.FFmpeg + $procs.Shell)) {
        try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop }
        catch { Write-Log 'ERROR' "could not stop pid $($p.ProcessId) for ${Name}: $_" }
    }
    Write-Log 'INFO' "$Name stopped (pid $($pids -join ', '))"
    return $pids.Count
}

$history = @{}   # name -> [datetime[]] recent restart times
$parked  = @{}   # name -> datetime until which we leave it alone
$obsDown = @{}   # name -> @{ Since; LastLogged } for observed components that are down

function Invoke-Restart {
    param([hashtable]$Comp, [switch]$Deliberate)
    $name = $Comp.Name
    $now  = Get-Date

    if ($parked.ContainsKey($name) -and $now -lt $parked[$name]) { return $false }

    $recent = @($history[$name] | Where-Object { $_ -gt $now.AddSeconds(-$BurstWindowSeconds) })
    if ($recent.Count -ge $BurstLimit) {
        $parked[$name] = $now.AddSeconds($CooldownSeconds)
        $history[$name] = @()
        Write-Log 'ALERT' "$name restarted $BurstLimit times in $BurstWindowSeconds s and is still failing - parked for $CooldownSeconds s. INVESTIGATE."
        return $false
    }

    # A bridge that was just switched on is not unhealthy, it is new. Logging
    # "unhealthy - restarting" for something the operator asked for thirty
    # seconds ago is how a WARN stops meaning anything.
    if ($Deliberate) { Write-Log 'INFO' "$name was switched on - starting" }
    else             { Write-Log 'WARN' "$name unhealthy - restarting" }
    & $Comp.Start
    $history[$name] = $recent + $now
    return $true
}

$toggleable = @($Components | Where-Object { $_.Toggle }).Count
Write-Log 'INFO' ("supervisor start (interval ${IntervalSeconds}s, $($Components.Count) managed, " +
                  "$toggleable toggleable, $($Observed.Count) observed; desired state from " +
                  "$(Split-Path $StateFile -Leaf), default " +
                  "$(if ($DefaultBridgeEnabled) { 'on' } else { 'off' }))")

do {
    $restarted = @()

    # Wanted-or-not comes first, and acting on "not wanted" comes before the
    # health checks: an unwanted bridge is stopped in the same second the file
    # says so, and a stopped bridge must never be probed - Test-RtspFrame would
    # sit through a 12 s ffprobe timeout for each one and stretch a 30 s cycle
    # past a minute.
    # 🔑 Stamped BEFORE the read, not after the cycle. A cycle takes several
    # seconds of ffprobe, and a toggle that lands inside that window was already
    # reflected in a stamp taken at the end - so the sleep saw nothing new and
    # the change waited out a full interval. Measured 2026-07-30: 32 s from
    # request to stopped, where it should have been about two.
    $stateStamp = Get-StateStamp
    Read-Desired
    $wanted = [ordered]@{}
    foreach ($comp in $Components) { $wanted[$comp.Name] = [bool](Test-BridgeWanted -Comp $comp) }

    $switchedOn = @()
    foreach ($comp in $Components) {
        if (-not $comp.Toggle) { continue }
        $name = $comp.Name
        $on   = $wanted[$name]
        if ($script:Announced[$name] -ne $on) {
            if ($on) { $switchedOn += $name }
            Write-Log 'INFO' "$name is now $(if ($on) { 'ENABLED' } else { 'DISABLED' }) by bridges.state.json"
            $script:Announced[$name] = $on
            # Re-enabling must not inherit the cooldown from whatever was wrong
            # with it before it was switched off.
            if ($on) { $history[$name] = @(); $parked.Remove($name) }
        }
        if (-not $on) { [void](Stop-Bridge -Name $name) }
    }

    # Evaluate everything FIRST and log the whole picture, then act. A supervisor
    # that only speaks up when it restarts something is impossible to debug when
    # it wrongly believes a dead component is fine.
    $health = [ordered]@{}
    foreach ($comp in $Components) {
        $health[$comp.Name] = if ($wanted[$comp.Name]) { [bool](& $comp.Check) } else { $null }
    }
    foreach ($obs  in $Observed)   { $health[$obs.Name]  = [bool](& $obs.Check) }
    Write-Log 'INFO' ("health: " + (($health.Keys | ForEach-Object {
        "{0}={1}" -f $_, $(if ($null -eq $health[$_]) { 'OFF' }
                           elseif ($health[$_])       { 'OK'  }
                           else                       { 'FAIL' }) }) -join '  '))

    foreach ($comp in $Components) {
        if ($restarted -contains $comp.Name) { continue }   # just started; give it a cycle
        if (-not $wanted[$comp.Name]) { continue }          # deliberately off, not a fault
        if ($health[$comp.Name]) { continue }

        if (Invoke-Restart -Comp $comp -Deliberate:($switchedOn -contains $comp.Name)) {
            $restarted += $comp.Name
            # A dependency restart invalidates its dependents even if they look
            # fine right now - their published path went away with the server.
            foreach ($dep in @($comp.Invalidates)) {
                $child = $Components | Where-Object { $_.Name -eq $dep }
                if ($child -and -not $wanted[$dep]) {
                    # It depended on what just restarted, but nobody wants it up.
                    continue
                }
                if ($child) {
                    Write-Log 'INFO' "$dep depends on $($comp.Name) - restarting too"
                    & $child.Start
                    $restarted += $dep
                }
            }
        }
    }

    # Observed components are alerted on the EDGE, not every cycle. Repeating an
    # unchanged alert 100 times an hour adds no information and destroys the log's
    # usefulness for everything else in it. The state is still visible every cycle
    # in the `health:` line above, so nothing is hidden by staying quiet here.
    $obsNow = Get-Date
    foreach ($obs in $Observed) {
        $name = $obs.Name
        if (-not $health[$name]) {
            if (-not $obsDown.ContainsKey($name)) {
                $obsDown[$name] = @{ Since = $obsNow; LastLogged = $obsNow }
                Write-Log 'ALERT' "$name is DOWN (not auto-restarted by design)"
            }
            elseif (($obsNow - $obsDown[$name].LastLogged).TotalSeconds -ge $ObservedRepeatSeconds) {
                $obsDown[$name].LastLogged = $obsNow
                $mins = [math]::Round(($obsNow - $obsDown[$name].Since).TotalMinutes)
                Write-Log 'ALERT' "$name STILL down after $mins min (not auto-restarted by design)"
            }
        }
        elseif ($obsDown.ContainsKey($name)) {
            $mins = [math]::Round(($obsNow - $obsDown[$name].Since).TotalMinutes)
            $obsDown.Remove($name)
            # Recovery was never logged at all, so the log could tell you something
            # broke but never that it came back.
            Write-Log 'INFO' "$name is back after $mins min down"
        }
    }

    # Sleep in slices so a toggle does not wait out a whole cycle. Someone
    # standing in the shop who has just tapped a camera on should see it come
    # back in seconds, not stand there wondering whether the button worked -
    # and "wondering whether the button worked" is how you end up with two
    # people pressing things.
    if (-not $Once) {
        $deadline = (Get-Date).AddSeconds($IntervalSeconds)
        while ((Get-Date) -lt $deadline) {
            Start-Sleep -Seconds 2
            if ((Get-StateStamp) -ne $stateStamp) {
                Write-Log 'INFO' 'bridges.state.json changed - waking early'
                break
            }
        }
    }
} while (-not $Once)

Write-Log 'INFO' 'supervisor exit'


