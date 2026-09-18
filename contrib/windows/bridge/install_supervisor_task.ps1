# Register the Shopcam 2000 supervisor as an at-logon scheduled task.
#
# Modelled on BI-Console-Interactive (<your user> / Interactive / Highest), so the rig
# has ONE startup model rather than a mix of tasks and services.
#
# ⚠️ There is no auto-logon on this box, by the user's deliberate choice. So this
# task -- like Blue Iris's -- only fires when someone logs in at the shop console.
# A reboot still strands the rig until that happens. That is a known, accepted
# limitation, not an oversight in this script.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File install_supervisor_task.ps1
#   powershell ... -File install_supervisor_task.ps1 -Remove

param([switch]$Remove)

$TaskName = 'Shopcam-Supervisor'
$Script   = 'C:\shopcam2000-bridge\supervisor.ps1'

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    "removed $TaskName"
    return
}
if (-not (Test-Path $Script)) { "$Script not found - ABORT"; return }

$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Script`""

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
# Blue Iris needs a moment after logon; the supervisor tolerates BI being absent
# (it only observes it) but there is no value in a flurry of FAIL lines at boot.
$trigger.Delay = 'PT1M'

$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest

# The supervisor is a long-running loop, so: no execution time limit, restart it
# if it ever dies, and never run two copies (two supervisors would fight over
# restarts and could double-spawn a bridge).
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings | Out-Null

$t = Get-ScheduledTask -TaskName $TaskName
"registered: $($t.TaskName)  state=$($t.State)"
"  user      = $($t.Principal.UserId) / $($t.Principal.LogonType) / $($t.Principal.RunLevel)"
"  action    = $($t.Actions[0].Execute) $($t.Actions[0].Arguments)"
"NOTE: at-logon only - no auto-logon on this box, so a reboot needs a console login."
