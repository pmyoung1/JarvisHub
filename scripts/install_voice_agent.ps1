# Requires a logged-on user session. Do not run this as the Session 0
# JarvisHub boot task — Windows will not give that service the microphone.
# Run from an elevated or normal PowerShell in Patrick's desktop session:
#   cd C:\JarvisHub
#   .\scripts\install_voice_agent.ps1

param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$TaskName = "JarvisHubVoice",
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
Set-Location $RepoRoot

if (-not $Python) {
    $Python = (Get-Command py -ErrorAction SilentlyContinue | Select-Object -First 1).Source
    if ($Python) { $PythonArgsPrefix = @("-3") } else {
        $Python = (Get-Command python -ErrorAction SilentlyContinue | Select-Object -First 1).Source
        $PythonArgsPrefix = @()
    }
} else {
    $PythonArgsPrefix = @()
}
if (-not $Python) { throw "Python 3.10+ was not found. Install it from python.org and re-run." }

$venv = Join-Path $RepoRoot ".venv"
$venvPy = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Write-Host "Creating .venv ..."
    & $Python @PythonArgsPrefix -m venv $venv
}
Write-Host "Installing voice extras from requirements-voice.txt ..."
& $venvPy -m pip install --upgrade pip
& $venvPy -m pip install -r (Join-Path $RepoRoot "requirements-voice.txt")

Write-Host "Downloading the default Piper voice (en_US-lessac-medium) ..."
& $venvPy -m jarvis.voice.enroll --download-voice

# Register an "At log on" task in the interactive session.
$action = New-ScheduledTaskAction -Execute $venvPy -Argument "-m jarvis.voice" -WorkingDirectory $RepoRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
Write-Host "Scheduled task '$TaskName' will start the voice agent at logon."
Write-Host "Enroll your voice next:"
Write-Host "  $venvPy -m jarvis.voice.enroll --record --clips 3 --seconds 8"
Write-Host "Or start it now:"
Write-Host "  Start-ScheduledTask -TaskName $TaskName"
Write-Host "List audio devices with:  $venvPy -m jarvis.voice --list-devices"
