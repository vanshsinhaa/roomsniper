[CmdletBinding()]
param(
    [string]$TaskName = "Hayden Room Booker",
    [string]$ReleaseTime = "00:00",
    [string]$ConfigPath = (Join-Path (Get-Location) "config.yaml"),
    [string]$Executable = "hayden-booker"
)

$ErrorActionPreference = "Stop"

if ($ReleaseTime -notmatch '^([01]\d|2[0-3]):[0-5]\d$') {
    throw "ReleaseTime must use 24-hour HH:mm format."
}

$resolvedConfig = (Resolve-Path -LiteralPath $ConfigPath).Path
$today = Get-Date -Format "yyyy-MM-dd"
$triggerAt = [datetime]::ParseExact(
    "$today $ReleaseTime",
    "yyyy-MM-dd HH:mm",
    [Globalization.CultureInfo]::InvariantCulture
)

$arguments = "--config `"$resolvedConfig`" run --due --live"
$action = New-ScheduledTaskAction -Execute $Executable -Argument $arguments -WorkingDirectory (Split-Path $resolvedConfig)
$trigger = New-ScheduledTaskTrigger -Daily -At $triggerAt
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -WakeToRun `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Idempotent Hayden Library room booking; logs rotate in ~/.hayden-booker/logs." `
    -Force | Out-Null

Write-Host "Installed '$TaskName' for $ReleaseTime system-local time."
Write-Host "Hayden Booker interprets booking dates in America/Phoenix."
Write-Host "No ASU password is stored in Task Scheduler."
