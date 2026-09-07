# Launcher for the Windows Task Scheduler deployment (see docs/deploy.md).
#
# The systemd unit on a server runs `python -m jobsift` and lets the program's
# own loop do the pacing. This does the same: ONE long-lived process, not a
# scheduled re-run. A single pass spends minutes in the scrape sources at
# onlinejobs.ph's five-second Crawl-delay - a catch-up pass measured 29 minutes -
# so a task that re-fired every five minutes would overlap runs against one
# SQLite file.
#
# Paths are derived from this script's own location, so the repo can live
# anywhere.

Set-Location -LiteralPath $PSScriptRoot

$logDir = Join-Path $PSScriptRoot 'logs'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$log = Join-Path $logDir ("jobsift-{0}.log" -f (Get-Date -Format 'yyyyMMdd'))

$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { $python = 'python' }   # fall back to PATH

"=== jobsift starting $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" |
    Out-File -FilePath $log -Append -Encoding utf8

# Redirect through cmd.exe rather than PowerShell's `*>>`.
#
# Python's logging writes to STDERR. Windows PowerShell 5.1 wraps every stderr
# line from a native command in a NativeCommandError, so with
# $ErrorActionPreference = 'Stop' the first log line jobsift emits becomes a
# terminating error and the task dies on startup having written nothing. That
# failure looks exactly like "python is broken": task result 1, empty log, no
# process. cmd's own `>>` and `2>&1` do no such wrapping.
& cmd.exe /c "`"$python`" -u -m jobsift >> `"$log`" 2>&1"

exit $LASTEXITCODE
