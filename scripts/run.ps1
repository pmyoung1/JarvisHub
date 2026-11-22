# Foreground run (for a first test, or as a Startup-folder alternative).
# Startup-folder shortcuts only launch after a user logs in, so prefer
# scripts/install_service.ps1 for the always-on, no-logon case.
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot
$env:JARVIS_CONFIG = Join-Path $RepoRoot "config.json"
if (Get-Command py -ErrorAction SilentlyContinue) {
    py -3 -m jarvis.launcher
} else {
    python -m jarvis.launcher
}
