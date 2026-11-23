<#
.SYNOPSIS
  Add the wake-word listener to YOUR Startup folder (shell:startup).

.DESCRIPTION
  Run as your normal (non-admin) user, in the account that is logged in at the
  Hub PC: the shortcut is created in that user's Startup folder and launches
  scripts\run_wakeword.ps1 hidden every time you log on. (The microphone is
  only available in a logged-in session, never to the SYSTEM boot task.)

  Which listener to use (run ONE):
    install_wakeword_startup.ps1   lightweight: wake word -> text -> hub.
                                   No speaker verification, no spoken replies on the PC.
    install_voice_agent.ps1        full voice agent: also speaker verification,
                                   Piper/SAPI replies on the hub PC, mute sync.
  They share a lock, so a second listener exits instead of double-answering.
  Pass -ReplaceVoiceAgent to disable the JarvisHubVoice scheduled task.

.PARAMETER ReplaceVoiceAgent
  Disable the JarvisHubVoice task (created by install_voice_agent.ps1) so only this listener starts.

.PARAMETER Extra
  Extra arguments baked into the shortcut, e.g. '--hub-url http://127.0.0.1:8765 --threshold 0.6'.

.EXAMPLE
  .\scripts\install_wakeword_startup.ps1
  .\scripts\install_wakeword_startup.ps1 -ReplaceVoiceAgent
#>
[CmdletBinding()]
param(
    [string]$ShortcutName = "Jarvis Wakeword.lnk",
    [string]$Extra = "",
    [switch]$ReplaceVoiceAgent,
    [string]$VoiceTaskName = "JarvisHubVoice"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Runner = Join-Path $RepoRoot "scripts\run_wakeword.ps1"
if (-not (Test-Path $Runner)) { throw "run_wakeword.ps1 not found at $Runner" }

$startup = [Environment]::GetFolderPath("Startup")
$lnk = Join-Path $startup $ShortcutName

$ps = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$shortcutArgs = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Runner`""
if ($Extra) { $shortcutArgs += " $Extra" }

$shell = New-Object -ComObject WScript.Shell
$sc = $shell.CreateShortcut($lnk)
$sc.TargetPath = $ps
$sc.Arguments = $shortcutArgs
$sc.WorkingDirectory = $RepoRoot
$sc.WindowStyle = 7   # minimized
$sc.Description = "Jarvis Hub offline wake-word listener"
$sc.Save()
Write-Host "Startup shortcut created: $lnk"
Write-Host "  runs: powershell.exe $shortcutArgs"

$voice = Get-ScheduledTask -TaskName $VoiceTaskName -ErrorAction SilentlyContinue
if ($voice) {
    if ($ReplaceVoiceAgent) {
        Disable-ScheduledTask -TaskName $VoiceTaskName | Out-Null
        Stop-ScheduledTask -TaskName $VoiceTaskName -ErrorAction SilentlyContinue
        Write-Host "Disabled scheduled task '$VoiceTaskName' (full voice agent). Re-enable it with: Enable-ScheduledTask -TaskName $VoiceTaskName"
    } else {
        Write-Warning "The full voice agent task '$VoiceTaskName' is also installed. Only one microphone listener can run; whichever starts first wins. Re-run with -ReplaceVoiceAgent to keep just this one."
    }
}

Write-Host ""
Write-Host "Test it now (Ctrl+C to stop):"
Write-Host "  powershell -ExecutionPolicy Bypass -File `"$Runner`" -Foreground --dry-run"
Write-Host "Logs: $(Join-Path $RepoRoot 'logs\wakeword.log')"
