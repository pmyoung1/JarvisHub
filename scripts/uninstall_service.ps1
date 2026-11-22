<#
.SYNOPSIS
  Remove the Jarvis Hub scheduled task (and optionally the firewall rule).
#>
[CmdletBinding()]
param(
    [string]$TaskName = "JarvisHub",
    [string]$FirewallRuleName = "Jarvis Hub Dashboard",
    [switch]$KeepFirewall
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Scheduled task '$TaskName' removed."
} else {
    Write-Host "Scheduled task '$TaskName' was not registered."
}

if (-not $KeepFirewall) {
    $rule = Get-NetFirewallRule -DisplayName $FirewallRuleName -ErrorAction SilentlyContinue
    if ($rule) {
        Remove-NetFirewallRule -DisplayName $FirewallRuleName
        Write-Host "Firewall rule '$FirewallRuleName' removed."
    }
}
