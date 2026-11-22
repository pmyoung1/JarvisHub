<#
.SYNOPSIS
  Create a self-signed TLS certificate so a phone browser can use the mic
  (Web Speech API requires HTTPS or localhost).

  After generating, set ssl.enabled to true in config.json and restart.
  You will need to trust the certificate on the phone (or accept the
  warning). This is optional — typing, or the phone keyboard's dictation
  key, works over plain HTTP.
#>
[CmdletBinding()]
param(
    [string]$RepoRoot = "",
    [string[]]$DnsName = @("jarvis-hub", "localhost"),
    [int]$Years = 5
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not $RepoRoot) {
    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}
$CertDir = Join-Path $RepoRoot "certs"
New-Item -ItemType Directory -Force -Path $CertDir | Out-Null

# Also include the current LAN IPv4 addresses so https://192.168.x.x works.
$ips = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -notlike "127.*" -and $_.PrefixOrigin -ne "WellKnown" } |
    Select-Object -ExpandProperty IPAddress
$names = @($DnsName + $ips | Where-Object { $_ } | Select-Object -Unique)

$cert = New-SelfSignedCertificate `
    -DnsName $names `
    -CertStoreLocation "Cert:\CurrentUser\My" `
    -FriendlyName "Jarvis Hub" `
    -NotAfter (Get-Date).AddYears($Years) `
    -KeyExportPolicy Exportable `
    -HashAlgorithm SHA256 `
    -KeySpec Signature

$cerPath = Join-Path $CertDir "cert.cer"
$pfxPath = Join-Path $CertDir "cert.pfx"
$pwd = ConvertTo-SecureString -String "jarvis-hub" -Force -AsPlainText
Export-Certificate -Cert $cert -FilePath $cerPath | Out-Null
Export-PfxCertificate -Cert $cert -FilePath $pfxPath -Password $pwd | Out-Null

# Export PEM if openssl or certutil can help; also write a small .pem via .NET.
$certPem = Join-Path $CertDir "cert.pem"
$bytes = $cert.Export([System.Security.Cryptography.X509Certificates.X509ContentType]::Cert)
$b64 = [Convert]::ToBase64String($bytes, [Base64FormattingOptions]::InsertLineBreaks)
@(
    "-----BEGIN CERTIFICATE-----"
    $b64
    "-----END CERTIFICATE-----"
) | Set-Content -Path $certPem -Encoding Ascii

# Private key PEM export is awkward on Windows without extra tools.
# The hub also accepts a PFX if you convert it, but the documented path
# is cert.pem + key.pem. Use openssl when available:
$keyPem = Join-Path $CertDir "key.pem"
$openssl = Get-Command openssl -ErrorAction SilentlyContinue
if ($openssl) {
    & openssl pkcs12 -in $pfxPath -nocerts -nodes -passin pass:jarvis-hub -out $keyPem
    Write-Host "Wrote $certPem and $keyPem"
} else {
    Write-Warning "OpenSSL was not found, so key.pem was not written. Install OpenSSL (or Git for Windows) and run:"
    Write-Host "  openssl pkcs12 -in `"$pfxPath`" -nocerts -nodes -passin pass:jarvis-hub -out `"$keyPem`""
    Write-Host "Certificate (public) is at $certPem / $cerPath."
}

Write-Host "Enable HTTPS in config.json:"
Write-Host '  "ssl": { "enabled": true, "certfile": "certs/cert.pem", "keyfile": "certs/key.pem" }'
Write-Host "Trust certs/cert.cer (or cert.pem) on the phone, then open https://<hub-ip>:8765"
