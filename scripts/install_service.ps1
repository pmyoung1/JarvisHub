<#
.SYNOPSIS
  Install Jarvis Hub as a Windows scheduled task that starts at boot and
  keeps running even when nobody is logged in.

.DESCRIPTION
  Registers a Task Scheduler task with:
    - Trigger: AtStartup
    - "Run whether user is logged on or not"
    - Restart on failure
    - No execution time limit

  Running as the user who owns the movie library is usually better than
  SYSTEM, because SYSTEM may not have permission to read those folders.
  Use -User and (if needed) -Password for that account. If neither is
  given, the task runs as SYSTEM.

  Optionally installs a private-profile firewall rule for the dashboard
  port (default 8765) unless -SkipFirewall is passed.

.EXAMPLE
  # From an elevated PowerShell prompt, in the repo root:
  .\scripts\install_service.ps1

.EXAMPLE
  .\scripts\install_service.ps1 -User "$env:USERDOMAIN\$env:USERNAME"
#>
[CmdletBinding()]
param(
    [string]$TaskName = "JarvisHub",
    [string]$RepoRoot = "",
    [string]$Python = "",
    [string]$User = "",
    [string]$Password = "",
    [int]$Port = 0,
    [switch]$SkipFirewall
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Find-Python {
    param([string]$Hint)
    if ($Hint -and (Test-Path $Hint)) { return (Resolve-Path $Hint).Path }
    foreach ($cmd in @("py", "python", "python3")) {
        $found = Get-Command $cmd -ErrorAction SilentlyContinue
        if ($found) {
            if ($cmd -eq "py") {
                return $found.Source
            }
            return $found.Source
        }
    }
    throw "Python 3.10+ was not found. Install it from https://www.python.org/downloads/windows/ and re-run."
}

if (-not $RepoRoot) {
    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
} else {
    $RepoRoot = (Resolve-Path $RepoRoot).Path
}

$PythonPath = Find-Python -Hint $Python
# Prefer pythonw.exe (no console window) when launching via the same prefix.
$PythonW = $null
$dir = Split-Path -Parent $PythonPath
$candidate = Join-Path $dir "pythonw.exe"
if (Test-Path $candidate) { $PythonW = $candidate }

$Exe = if ($PythonW) { $PythonW } else { $PythonPath }
# When the launcher is "py", arguments must include -3 so we get 3.x.
$ArgPrefix = ""
if ((Split-Path -Leaf $PythonPath) -match "^py(.exe)?$") {
    $ArgPrefix = "-3 "
    # pyw.exe is the windowless equivalent if present.
    $pyw = Join-Path $dir "pyw.exe"
    if (Test-Path $pyw) { $Exe = $pyw }
}

$Arguments = ("{0}-m jarvis.launcher" -f $ArgPrefix).Trim()
$Config = Join-Path $RepoRoot "config.json"
if (-not (Test-Path $Config)) {
    Write-Warning "config.json is missing. Copy config.example.json to config.json and edit media_paths before starting the task."
}

# If a port was passed, remember it for the firewall rule; the process
# itself still reads the port from config.json.
if ($Port -le 0) {
    $Port = 8765
    if (Test-Path $Config) {
        try {
            $cfg = Get-Content -Raw -Path $Config | ConvertFrom-Json
            if ($cfg.port) { $Port = [int]$cfg.port }
        } catch {
            # keep default
        }
    }
}

$action = New-ScheduledTaskAction -Execute $Exe -Argument $Arguments -WorkingDirectory $RepoRoot

$trigger = New-ScheduledTaskTrigger -AtStartup

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -DontStopOnIdleEnd `
    -RestartCount 5 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew

# "Run whether user is logged on or not":
#   SYSTEM  -> LogonType ServiceAccount
#   a user  -> LogonType Password (Register-ScheduledTask -Password) or S4U
if ($User) {
    $principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Password -RunLevel Highest
} else {
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
}

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

$registerParams = @{
    TaskName    = $TaskName
    Action      = $action
    Trigger     = $trigger
    Settings    = $settings
    Principal   = $principal
    Description = "Jarvis household AI hub (phase 1). Starts at boot, no interactive logon required."
    Force       = $true
}
if ($User -and $Password) {
    $registerParams.User = $User
    $registerParams.Password = $Password
} elseif ($User -and -not $Password) {
    Write-Warning "No -Password given. You may be prompted, or the task may fail to start until credentials are stored in Task Scheduler."
}

Register-ScheduledTask @registerParams | Out-Null

# Environment for the task: pin the config path so cwd surprises do not matter.
try {
    $task = Get-ScheduledTask -TaskName $TaskName
    # Task Scheduler XML env vars are fiddly; the launcher also looks for
    # config.json in the working directory (the repo root we set above).
    $null = $task
} catch {
    # non-fatal
}

Write-Host "Scheduled task '$TaskName' registered."
Write-Host "  Execute : $Exe $Arguments"
Write-Host "  Start in: $RepoRoot"
Write-Host "  Identity: $(if ($User) { $User } else { 'SYSTEM' })"
Write-Host "Start it now with:  Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "Or run once in the foreground:  $PythonPath -m jarvis.launcher"

if (-not $SkipFirewall) {
    $fw = Join-Path $PSScriptRoot "install_firewall.ps1"
    if (Test-Path $fw) {
        & $fw -Port $Port
    }
}

Write-Host ""
Write-Host "After the hub is running, open http://<this-pc-lan-ip>:$Port from a phone or the TV browser."
