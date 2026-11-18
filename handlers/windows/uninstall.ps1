# Remove the per-user globalprotectcallback: handler that handlers\windows\install.ps1 registered.
# Usage: powershell -ExecutionPolicy Bypass -File handlers\windows\uninstall.ps1
$ErrorActionPreference = "Stop"
$key = "HKCU:\Software\Classes\globalprotectcallback"
if (Test-Path $key) {
    Remove-Item -Path $key -Recurse
    Write-Host "Removed. globalprotectcallback: links go back to the machine-wide handler, if any (GlobalProtect)."
} else {
    Write-Host "Nothing to remove."
}
