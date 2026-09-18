# Register the Shopcam 2000 Controller as an at-logon scheduled task.
#
# Modelled on install_supervisor_task.ps1 (<your user> / Interactive / Highest) so the
# rig keeps ONE startup model rather than a mix of tasks and services.
#
# ⚠️ There is no auto-logon on this box, by the user's deliberate choice, so this
# task only fires when someone logs in at the shop console. That is accepted and
# not an oversight: Blue Iris and the four ffmpeg bridges are session-1 desktop
# apps too (CAM9's bridge screencaps the desktop), so a Controller that survived
# a reboot on its own would just sit there backing off against a dead Blue Iris.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File install_controller_task.ps1
#   powershell ... -File install_controller_task.ps1 -Remove

param([switch]$Remove)

$TaskName = 'Shopcam-Controller'
$Script   = 'C:\shopcam2000\bridge\run_controller.cmd'

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    "removed $TaskName"
    return
}
if (-not (Test-Path $Script)) { "$Script not found - ABORT"; return }

$action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument "/c `"$Script`"" `
    -WorkingDirectory 'C:\shopcam2000'

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
# Blue Iris needs a moment after logon. The Controller tolerates BI being absent
# (it backs off at 20s and reports offline), so this delay is politeness, not a
# dependency -- do not lengthen it hoping to "fix" a startup race.
$trigger.Delay = 'PT30S'

$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest

# Long-running server: no execution time limit, restart if it dies, and never two
# copies -- two Controllers polling one Blue Iris would fight over takes via the
# _reconcile adopt-in-progress path.
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
