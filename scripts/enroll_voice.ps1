# Record or import enrollment clips for Patrick's voice.
#   .\scripts\enroll_voice.ps1
#   .\scripts\enroll_voice.ps1 -Files a.wav,b.wav,c.wav
param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string[]]$Files = @(),
    [int]$Clips = 3,
    [double]$Seconds = 8,
    [string]$Name = "patrick"
)
$ErrorActionPreference = "Stop"
Set-Location $RepoRoot
$venvPy = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) { $venvPy = "py" }
if ($Files.Count -gt 0) {
    & $venvPy -m jarvis.voice.enroll --name $Name --files $Files
} else {
    & $venvPy -m jarvis.voice.enroll --name $Name --record --clips $Clips --seconds $Seconds
}
