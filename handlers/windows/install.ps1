# Register handlers\windows\callback.pyw as the handler for globalprotectcallback: links, for the current user only.
# Usage: powershell -ExecutionPolicy Bypass -File handlers\windows\install.ps1 [-TakeOver]
#   -TakeOver: register it even when the official GlobalProtect app is installed.
# Run it again when this folder moves: the registry entry holds the absolute path of the script.
param([switch]$TakeOver)
$ErrorActionPreference = "Stop"

$script = Join-Path $PSScriptRoot "callback.pyw"
$key = "HKCU:\Software\Classes\globalprotectcallback"
$official = "Registry::HKEY_LOCAL_MACHINE\SOFTWARE\Classes\globalprotectcallback"

# pythonw.exe runs the handler without a console window.
$pythonw = $null
$cmd = Get-Command pythonw.exe -ErrorAction SilentlyContinue
if ($cmd) { $pythonw = $cmd.Source }
if (-not $pythonw -and (Get-Command py.exe -ErrorAction SilentlyContinue)) {
    $dir = & py.exe -3 -c "import os, sys; print(os.path.dirname(sys.executable))"
    $pythonw = Join-Path $dir "pythonw.exe"
}
if (-not $pythonw -or -not (Test-Path $pythonw)) {
    Write-Error "pythonw.exe not found. Install Python 3 from https://www.python.org/downloads/windows/ first."
}

if ((Test-Path $official) -and -not $TakeOver) {
    Write-Warning "GlobalProtect is installed. The official app owns globalprotectcallback: links."
    Write-Warning "Registering VPN Gateway Callback breaks the official app's browser login until handlers\windows\uninstall.ps1 is run."
    Write-Host "Nothing changed. To take over the links, run: powershell -ExecutionPolicy Bypass -File handlers\windows\install.ps1 -TakeOver"
    exit 0
}

# A per-user class under HKCU wins over the machine-wide one under HKLM.
New-Item -Path "$key\shell\open\command" -Force | Out-Null
Set-ItemProperty -Path $key -Name "(default)" -Value "URL:GlobalProtect SAML callback (VPN Gateway)"
Set-ItemProperty -Path $key -Name "URL Protocol" -Value ""
Set-ItemProperty -Path "$key\shell\open\command" -Name "(default)" -Value "`"$pythonw`" `"$script`" `"%1`""

Write-Host "ok: VPN Gateway Callback now opens globalprotectcallback: links (current user)."
Write-Host "It reads the port and token from data\secrets\web-handler.json, written by vpn web while it runs."
Write-Host "The first time a login hands over a link, the browser asks whether to open it: allow it."
Write-Host "To remove it: powershell -ExecutionPolicy Bypass -File handlers\windows\uninstall.ps1"
