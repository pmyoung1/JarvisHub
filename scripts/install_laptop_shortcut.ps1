# Creates a desktop / Start-menu shortcut to the laptop dashboard.
param(
    [string]$HubUrl = "http://127.0.0.1:8765/laptop",
    [string]$Name = "Jarvis Hub"
)
$ErrorActionPreference = "Stop"
$desktop = [Environment]::GetFolderPath("Desktop")
$path = Join-Path $desktop "$Name.url"
@(
    "[InternetShortcut]",
    "URL=$HubUrl",
    "IconIndex=0"
) | Set-Content -Path $path -Encoding ASCII
Write-Host "Shortcut written to $path"
Write-Host "On another PC, change -HubUrl to http://<hub-ip>:8765/laptop"
Write-Host "Optional tray mute helper:  py -3 -m jarvis.tray"
