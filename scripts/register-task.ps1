<#
    Registers (or re-registers) the daily run with Windows Task Scheduler.

    Deliberately NOT elevated and NOT run-as-SYSTEM. The job needs the calling
    user's profile: .env, config\service_account.json, and the Google
    credentials all live there, and a SYSTEM-context task would fail to read
    them in a way that is tedious to diagnose from a scheduler error code.

        powershell -ExecutionPolicy Bypass -File scripts\register-task.ps1
        powershell -ExecutionPolicy Bypass -File scripts\register-task.ps1 -Spend
        powershell -ExecutionPolicy Bypass -File scripts\register-task.ps1 -Remove

    Keep this file pure ASCII; PowerShell 5.1 reads a BOM-less .ps1 as ANSI.
#>
[CmdletBinding()]
param(
    [string] $Time     = '07:00',
    [string] $TaskName = 'jobpipe-daily',
    # Off by default: registering the schedule and authorising spend are two
    # separate decisions, and the default should be the one that costs nothing.
    [switch] $Spend,
    [switch] $Remove
)

$ErrorActionPreference = 'Stop'

$root   = Split-Path -Parent $PSScriptRoot
$script = Join-Path $root 'scripts\daily.ps1'

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "removed scheduled task '$TaskName'"
    exit 0
}

if (-not (Test-Path $script)) { Write-Error "missing $script"; exit 2 }

$argline = "-NoProfile -ExecutionPolicy Bypass -File `"$script`""
if ($Spend) { $argline += ' -Spend' }

$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
                                  -Argument $argline -WorkingDirectory $root

$trigger = New-ScheduledTaskTrigger -Daily -At $Time

# StartWhenAvailable matters on a laptop: without it a machine asleep at 07:00
# simply skips the day, and a skipped day is postings never seen. The run is
# idempotent, so a late start is harmless.
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Description 'jobpipe: fetch, filter, score, sync' `
    -Force | Out-Null

$mode = if ($Spend) { 'WITH scoring (spends money)' } else { 'no scoring (free)' }
Write-Output "registered '$TaskName' daily at $Time - $mode"
Write-Output "run it now with:  Start-ScheduledTask -TaskName $TaskName"
Write-Output "check history:    Get-ScheduledTaskInfo -TaskName $TaskName"
