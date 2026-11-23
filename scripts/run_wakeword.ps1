<#
.SYNOPSIS
  Run the offline "hey jarvis" listener (scripts\wakeword.py) from the project .venv.

.DESCRIPTION
  Must run in YOUR logged-in session (Startup folder), not as the SYSTEM boot
  service: Windows Session 0 cannot use the microphone. The hub keeps running
  as the JarvisHub scheduled task; this only adds the ears.

  Paths are relative to this script, so it works from C:\JarvisHub or any
  other folder. Output goes to logs\wakeword.log (plus logs\wakeword.console.log
  for crash text). Unless -Foreground is given, it restarts the listener if it
  exits with an error (waits 10 s; gives up after 5 quick failures in a row).

.PARAMETER Foreground
  Run in this console, no log redirection, no restart loop. Use it to test:
    .\scripts\run_wakeword.ps1 -Foreground --dry-run

.EXAMPLE
  .\scripts\run_wakeword.ps1 -Foreground --list-devices
  .\scripts\run_wakeword.ps1 -Foreground --dry-run
  .\scripts\run_wakeword.ps1 --hub-url http://127.0.0.1:8765 --token SECRET
#>
[CmdletBinding()]
param(
    [switch]$Foreground,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$WakewordArgs
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot
$VenvPy = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$Script = Join-Path $RepoRoot "scripts\wakeword.py"
$LogDir = Join-Path $RepoRoot "logs"
$LogFile = Join-Path $LogDir "wakeword.log"
$ConsoleLog = Join-Path $LogDir "wakeword.console.log"
$env:JARVIS_CONFIG = Join-Path $RepoRoot "config.json"

if (-not (Test-Path $VenvPy)) {
    throw ".venv not found at $VenvPy. Create it and install the listener packages first:`n  py -3 -m venv .venv`n  .\.venv\Scripts\python.exe -m pip install -r requirements-wakeword.txt"
}
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Format-Arg([string]$a) {
    if ($a -match '[\s"]') { return '"' + ($a -replace '"', '\"') + '"' }
    return $a
}

if ($Foreground) {
    & $VenvPy $Script --log-file $LogFile @WakewordArgs
    exit $LASTEXITCODE
}

$argList = @((Format-Arg $Script), "--log-file", (Format-Arg $LogFile))
foreach ($a in $WakewordArgs) { $argList += (Format-Arg $a) }

$quickFailures = 0
while ($true) {
    $started = Get-Date
    $p = Start-Process -FilePath $VenvPy -ArgumentList $argList -WorkingDirectory $RepoRoot `
        -NoNewWindow -Wait -PassThru `
        -RedirectStandardOutput $ConsoleLog -RedirectStandardError ($ConsoleLog + ".err")
    $code = $p.ExitCode
    if ($code -eq 0) { break }               # clean stop (Ctrl+C / logoff)
    if ($code -eq 2 -or $code -eq 3) { break } # missing packages, or another listener is running: do not loop
    $ran = ((Get-Date) - $started).TotalSeconds
    if ($ran -lt 60) { $quickFailures++ } else { $quickFailures = 0 }
    if ($quickFailures -ge 5) {
        Add-Content -Path $LogFile -Value ("{0} [ERROR] run_wakeword: giving up after 5 quick failures (last exit {1}). See {2}.err" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $code, $ConsoleLog)
        break
    }
    Add-Content -Path $LogFile -Value ("{0} [WARNING] run_wakeword: listener exited with {1}; restarting in 10 s" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $code)
    Start-Sleep -Seconds 10
}
