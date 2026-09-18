# Toggle a bridge through the Controller API and time how long the ffmpeg
# process takes to appear or disappear. Quoting a JSON body through ssh is the
# thing that bit us twice today - so it is a file, and -File it.
# NB: -File passes every argument as a STRING, so a [bool] parameter cannot be
# bound at all - "Cannot convert System.String to System.Boolean", even for 1.
param(
    [string]$Bridge = '',      # empty = all
    [Parameter(Mandatory=$true)][ValidateSet('on','off')][string]$State,
    [int]$WaitSeconds = 90
)

$On = ($State -eq 'on')
$body = if ($Bridge) { @{ bridge = $Bridge; on = $On } } else { @{ all = $true; on = $On } }
$json = $body | ConvertTo-Json -Compress

$t0 = Get-Date
$resp = Invoke-RestMethod -Uri 'http://127.0.0.1:8787/api/bridges' -Method Post `
        -ContentType 'application/json' -Body $json
"request: $json"
"reply  : " + ($resp.bridges | ForEach-Object { "$($_.id)=$(if ($_.enabled) {'on'} else {'off'})" }) -join ' '

$target = if ($Bridge) { $Bridge } else { 'cam1' }
$want   = $On
while (((Get-Date) - $t0).TotalSeconds -lt $WaitSeconds) {
    $procs = @(Get-CimInstance Win32_Process -Filter "Name='ffmpeg.exe'" -EA SilentlyContinue |
               Where-Object { $_.CommandLine -match "8554/$target[ _]" })
    if (($procs.Count -gt 0) -eq $want) {
        "{0}: ffmpeg {1} after {2:N1}s" -f $target, $(if ($want) { 'up' } else { 'gone' }), ((Get-Date) - $t0).TotalSeconds
        break
    }
    Start-Sleep -Milliseconds 500
}
"ffmpeg total now: " + (@(Get-Process ffmpeg -EA SilentlyContinue)).Count
