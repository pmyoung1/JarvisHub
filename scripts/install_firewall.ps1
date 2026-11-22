<#
.SYNOPSIS
  Allow inbound TCP to the Jarvis dashboard on the Private profile only.
#>
[CmdletBinding()]
param(
    [int]$Port = 8765,
    [string]$Name = "Jarvis Hub Dashboard"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$existing = Get-NetFirewallRule -DisplayName $Name -ErrorAction SilentlyContinue
if ($existing) {
    Remove-NetFirewallRule -DisplayName $Name
}

New-NetFirewallRule `
    -DisplayName $Name `
    -Description "Jarvis Hub local dashboard and API (phase 1). Private profile only." `
    -Direction Inbound `
    -Protocol TCP `
    -LocalPort $Port `
    -Action Allow `
    -Profile Private `
    -Enabled True | Out-Null

Write-Host "Firewall rule '$Name' allows TCP $Port on the Private profile."
Write-Host "The hub is intended for the LAN only. Do not expose this port on Public / the internet."
