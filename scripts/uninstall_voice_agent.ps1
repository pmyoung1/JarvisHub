param([string]$TaskName = "JarvisHubVoice")
$ErrorActionPreference = "Stop"
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed scheduled task $TaskName."
} else {
    Write-Host "Task $TaskName was not registered."
}
