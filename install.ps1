# jobsift in one line, on Windows (PowerShell):
#
#   irm https://raw.githubusercontent.com/kimlj/jobsift/main/install.ps1 | iex
#
# Puts jobsift in $HOME\jobsift (or $env:JOBSIFT_DIR), gives it its own Python
# environment, installs what it needs, and starts the setup. Safe to run again: an
# existing copy is updated with git pull, and config.yaml, .env, your resume and
# data\ are never touched. It needs git and Python 3.10 or newer, and says how to
# get either if it is missing. Read it first if you would rather: it is this file.

$repo = 'https://github.com/kimlj/jobsift.git'
$dir = if ($env:JOBSIFT_DIR) { $env:JOBSIFT_DIR } else { Join-Path $HOME 'jobsift' }

function Stop-Install($why) {
    Write-Host "`n$why" -ForegroundColor Yellow
    # `return` rather than `exit`: under `irm | iex` this runs in your own window,
    # and exit would close it.
    return
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    return Stop-Install "jobsift needs git. Install it with:  winget install --id Git.Git -e`nthen open a new PowerShell window and run the same line again."
}

# The py launcher first: on a fresh Windows, `python` can be the Microsoft Store
# placeholder, which opens the Store instead of running anything.
$py = $null
foreach ($candidate in @('py', 'python')) {
    if (Get-Command $candidate -ErrorAction SilentlyContinue) {
        & $candidate -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) { $py = $candidate; break }
    }
}
if (-not $py) {
    return Stop-Install "jobsift needs Python 3.10 or newer. Install it with:  winget install --id Python.Python.3.12 -e`nthen open a new PowerShell window and run the same line again."
}

if (Test-Path (Join-Path $dir '.git')) {
    Write-Host "Updating jobsift in $dir"
    git -C $dir pull --ff-only --quiet
} else {
    Write-Host "Downloading jobsift into $dir"
    git clone --quiet $repo $dir
}
if ($LASTEXITCODE -ne 0) { return Stop-Install "git could not get jobsift; the message above says why." }

Push-Location $dir
try {
    if (-not (Test-Path '.venv\Scripts\python.exe')) {
        Write-Host 'Making its Python environment (.venv)'
        & $py -m venv .venv
        if ($LASTEXITCODE -ne 0) { return Stop-Install 'Python could not make the .venv folder; the message above says why.' }
    }
    Write-Host 'Installing what it needs (a minute or two the first time)'
    & .\.venv\Scripts\python.exe -m pip install --quiet --disable-pip-version-check -r requirements.txt
    if ($LASTEXITCODE -ne 0) { return Stop-Install 'pip could not install the requirements; the message above says why.' }

    if ($env:JOBSIFT_SKIP_SETUP) {
        Write-Host 'Installed. Setup skipped (JOBSIFT_SKIP_SETUP is set).'
    } else {
        & .\.venv\Scripts\python.exe -m jobsift --setup

        # Keeping it running: a scheduled task that starts it at sign-in and brings
        # it back within 15 minutes if it stops. The same settings as
        # docs/deploy.md, Option B, where each one is explained.
        if (Get-ScheduledTask -TaskName jobsift -ErrorAction SilentlyContinue) {
            Write-Host "`nA jobsift task is already set up; it runs this version from its next start."
        } elseif ((Read-Host "`nStart jobsift by itself whenever you sign in to Windows? [Y/n]") -notmatch '^[nN]') {
            try {
                $launcher = Join-Path $dir 'run-jobsift.ps1'
                $action = New-ScheduledTaskAction -Execute 'powershell.exe' `
                    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$launcher`""
                $triggers = @(
                    New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
                    New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 15)
                )
                $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew `
                    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartInterval (New-TimeSpan -Minutes 5) `
                    -RestartCount 999 -StartWhenAvailable
                $settings.DisallowStartIfOnBatteries = $false
                $settings.StopIfGoingOnBatteries = $false
                Register-ScheduledTask -TaskName jobsift -Action $action -Trigger $triggers `
                    -Settings $settings -ErrorAction Stop | Out-Null
                Start-ScheduledTask -TaskName jobsift
                Write-Host "Running in the background. Its log:  Get-Content $dir\logs\jobsift-*.log -Tail 40 -Wait"
            } catch {
                Write-Host "Windows would not create the task ($($_.Exception.Message)). docs/deploy.md, Option B, has the steps." -ForegroundColor Yellow
            }
        }
    }
    Write-Host "`nFrom now on, in $dir :"
    Write-Host '  .\jobsift.cmd --setup                 change any answer'
    Write-Host '  .\jobsift.cmd --once --no-telegram    one pass, no alerts'
    Write-Host '  .\jobsift.cmd                         keep it running'
} finally {
    Pop-Location
}
