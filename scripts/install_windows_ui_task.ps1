[CmdletBinding()]
param(
    [string]$TaskName = "Hayden Room Booker UI",
    [int]$Port = 8787,
    [string]$ConfigPath = (Join-Path (Get-Location) "config.yaml"),
    # pythonw.exe runs the dashboard without a console window; pass the interpreter
    # from the virtual environment that has hayden-booker installed.
    [string]$Pythonw = (Join-Path (Get-Location) ".venv\Scripts\pythonw.exe")
)

$ErrorActionPreference = "Stop"

if ($Port -lt 1024 -or $Port -gt 65535) {
    throw "Port must be between 1024 and 65535."
}
if (-not (Test-Path -LiteralPath $Pythonw)) {
    throw "Interpreter not found: $Pythonw"
}

$resolvedConfig = (Resolve-Path -LiteralPath $ConfigPath).Path
$resolvedPythonw = (Resolve-Path -LiteralPath $Pythonw).Path

$arguments = "-m hayden_booker.cli --config `"$resolvedConfig`" ui --port $Port --no-open"
$action = New-ScheduledTaskAction -Execute $resolvedPythonw -Argument $arguments -WorkingDirectory (Split-Path $resolvedConfig)
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Read-only Hayden Booker dashboard on loopback; it never books or submits." `
    -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName

Write-Host "Installed '$TaskName'; the dashboard starts at every logon."
Write-Host "Open http://127.0.0.1:$Port/"
Write-Host "Remove it with: .\scripts\remove_windows_task.ps1 -TaskName '$TaskName'"
