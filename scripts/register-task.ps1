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
    # 11:30 local. Changed from 10:00 on 2026-08-31 at Anish's request.
    [string] $Time     = '11:30',
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

# StartWhenAvailable matters on a laptop: without it a machine asleep at 11:30
# simply skips the day, and a skipped day is postings never seen. The run is
# idempotent, so a late start is harmless.
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -MultipleInstances IgnoreNew

# S4U - "service for user": run whether or not the user is logged on, with no
# stored password. The task was previously registered with the default
# Interactive principal, which needs a live session. It fired exactly on time
# and died at process init with 0xC0000142 (STATUS_DLL_INIT_FAILED) on 5 of 17
# days, writing no log at all because the failure preceded Python starting -
# so the scheduler reported 0 missed runs while a third of the days had no
# data. S4U still runs AS the calling user, so .env and the service account
# JSON in the profile stay readable; that is why this is not run-as-SYSTEM.
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType S4U -RunLevel Limited

# The description is the only thing visible in the Task Scheduler UI, so it
# must not claim a stage that -Spend has switched off.
$desc = if ($Spend) { 'jobpipe: fetch, filter, score, sync' }
        else        { 'jobpipe: fetch, filter, sync (no scoring)' }

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Description $desc `
    -Force | Out-Null

$mode = if ($Spend) { 'WITH scoring (spends money)' } else { 'no scoring (free)' }
Write-Output "registered '$TaskName' daily at $Time - $mode"
Write-Output "run it now with:  Start-ScheduledTask -TaskName $TaskName"
Write-Output "check history:    Get-ScheduledTaskInfo -TaskName $TaskName"
