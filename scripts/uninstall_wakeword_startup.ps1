<#
.SYNOPSIS
  Remove the wake-word listener's Startup shortcut (and optionally stop it / re-enable the voice agent).
#>
[CmdletBinding()]
param(
    [string]$ShortcutName = "Jarvis Wakeword.lnk",
    [switch]$StopRunning,
    [switch]$EnableVoiceAgent,
    [string]$VoiceTaskName = "JarvisHubVoice"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$lnk = Join-Path ([Environment]::GetFolderPath("Startup")) $ShortcutName
if (Test-Path $lnk) {
    Remove-Item -LiteralPath $lnk -Force
    Write-Host "Removed $lnk"
} else {
    Write-Host "No shortcut at $lnk"
}

if ($StopRunning) {
    Get-CimInstance Win32_Process |
        Where-Object { $_.CommandLine -match 'wakeword\.py' -or $_.CommandLine -match 'run_wakeword\.ps1' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Host "Stopped PID $($_.ProcessId)" }
}

if ($EnableVoiceAgent) {
    if (Get-ScheduledTask -TaskName $VoiceTaskName -ErrorAction SilentlyContinue) {
        Enable-ScheduledTask -TaskName $VoiceTaskName | Out-Null
        Write-Host "Enabled scheduled task '$VoiceTaskName'."
    } else {
        Write-Host "Task '$VoiceTaskName' is not installed (run scripts\install_voice_agent.ps1)."
    }
}
