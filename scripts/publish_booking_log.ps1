# Publish freshly logged bookings after a run.
#
# Two modes:
#   -Mode Dispatch (default)  send the new bookings to the GitHub booking-log workflow, which
#                             creates the commits. Needs $env:BOOKING_LOG_TOKEN and -Repo.
#   -Mode Local               create the commits here and push them yourself.
#
# Wire it into the scheduled task right after `hayden-booker run --live`, for example:
#   powershell -ExecutionPolicy Bypass -File scripts\publish_booking_log.ps1 -Mode Local -Push

[CmdletBinding()]
param(
    [ValidateSet('Dispatch', 'Local')]
    [string]$Mode = 'Dispatch',
    [string]$Repo = $env:BOOKING_REPO,
    [switch]$Push
)

$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)

$booker = Join-Path '.venv' 'Scripts\hayden-booker.exe'
if (-not (Test-Path $booker)) { $booker = 'hayden-booker' }

if ($Mode -eq 'Dispatch') {
    if (-not $Repo) { throw 'Set -Repo owner/name or the BOOKING_REPO environment variable.' }
    if (-not $env:BOOKING_LOG_TOKEN) { throw 'Set BOOKING_LOG_TOKEN to a token with repo scope.' }
    & $booker log publish --repo $Repo
    exit $LASTEXITCODE
}

& $booker log commit
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if ($Push) {
    git push origin HEAD
    exit $LASTEXITCODE
}

Write-Host 'Commits created locally. Run `git push` when you are ready.'
