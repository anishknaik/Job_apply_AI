<#
    Wrapper for the scheduled daily run.

    Task Scheduler gives you one exit code and nothing else, so everything
    that matters has to end up in a log file. This script owns that: it picks
    the interpreter, redirects both streams, prevents overlapping runs, and
    prunes old logs so the directory does not grow without bound.

    Run it by hand first to confirm it works before registering the task:
        powershell -ExecutionPolicy Bypass -File scripts\daily.ps1
#>
[CmdletBinding()]
param(
    # Without -Spend the run fetches, filters and syncs but scores nothing,
    # so scheduling the job and authorising money are separate decisions.
    [switch] $Spend,
    [int]    $KeepLogDays = 30
)

$ErrorActionPreference = 'Stop'

$root   = Split-Path -Parent $PSScriptRoot
$python = 'D:\python_312\python.exe'
$logDir = Join-Path $root 'logs'

if (-not (Test-Path $python)) {
    # Fall back to whatever is on PATH rather than failing outright: a moved
    # interpreter should not silently stop the pipeline for weeks.
    $found = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $found) { Write-Error "no python found at $python or on PATH"; exit 2 }
    $python = $found.Source
}

if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }

# A run that hangs must not have tomorrow's run pile up behind it. The lock is
# a file holding a PID; a stale one from a killed process is cleared rather
# than blocking forever.
$lock = Join-Path $logDir '.daily.lock'
if (Test-Path $lock) {
    $old = Get-Content $lock -ErrorAction SilentlyContinue | Select-Object -First 1
    $alive = $null
    if ($old) { $alive = Get-Process -Id $old -ErrorAction SilentlyContinue }
    if ($alive) {
        Write-Output "a run is already in progress (pid $old); exiting"
        exit 0
    }
    Remove-Item $lock -Force -ErrorAction SilentlyContinue
}
$PID | Out-File -FilePath $lock -Encoding utf8

$stamp  = Get-Date -Format 'yyyyMMdd-HHmmss'
$log    = Join-Path $logDir "daily-$stamp.log"
$errLog = Join-Path $logDir "daily-$stamp.err.log"

$pyArgs = @('-m', 'jobpipe.cli', 'daily')
if ($Spend) { $pyArgs += '--yes' }

try {
    # Start-Process rather than the call operator with 2>&1: in PowerShell 5.1
    # redirecting a native command's stderr wraps each line in an ErrorRecord
    # and sets $? to false even on a clean exit.
    $proc = Start-Process -FilePath $python -ArgumentList $pyArgs `
                          -WorkingDirectory $root -NoNewWindow -Wait -PassThru `
                          -RedirectStandardOutput $log -RedirectStandardError $errLog
    $code = $proc.ExitCode
}
finally {
    Remove-Item $lock -Force -ErrorAction SilentlyContinue
}

# An empty stderr file per run is just clutter.
if ((Test-Path $errLog) -and ((Get-Item $errLog).Length -eq 0)) {
    Remove-Item $errLog -Force -ErrorAction SilentlyContinue
}

$cutoff = (Get-Date).AddDays(-$KeepLogDays)
Get-ChildItem -Path $logDir -Filter 'daily-*.log' -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -lt $cutoff } |
    Remove-Item -Force -ErrorAction SilentlyContinue

# Keep this file pure ASCII: PowerShell 5.1 reads a BOM-less .ps1 as ANSI, so
# a stray em-dash arrives as mojibake and breaks parsing at load time.
Write-Output "exit $code - $log"
exit $code
