# Find Cisco Umbrella Root CA across all user/machine cert stores and export to PEM.
$cert = Get-ChildItem -Path Cert:\ -Recurse -ErrorAction SilentlyContinue |
    Where-Object { $_.Subject -like '*Cisco Umbrella Root*' } |
    Select-Object -First 1

if (-not $cert) {
    Write-Error "Cisco Umbrella Root CA not found in any cert store"
    exit 1
}

Write-Host "Found:"
Write-Host "  Subject:    $($cert.Subject)"
Write-Host "  Thumbprint: $($cert.Thumbprint)"
Write-Host "  Path:       $($cert.PSPath)"

$der = $cert.Export([System.Security.Cryptography.X509Certificates.X509ContentType]::Cert)
$b64 = [Convert]::ToBase64String($der, [Base64FormattingOptions]::InsertLineBreaks)
$pem = "-----BEGIN CERTIFICATE-----`n$b64`n-----END CERTIFICATE-----`n"

$out = Join-Path $PSScriptRoot '..\certs\cisco-umbrella-root.pem'
Set-Content -Path $out -Value $pem -Encoding ascii
Write-Host "Wrote $out"
