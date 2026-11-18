#!/bin/sh
# Stop and remove the VPN Gateway wg-quick service that install-tunnel.sh set up.
# Usage: sudo handlers/macos/uninstall-tunnel.sh
set -u
if [ "$(id -u)" -ne 0 ]; then
  echo "Run it with sudo: sudo $0" >&2
  exit 1
fi
plist="/Library/LaunchDaemons/com.athenayazilim.vpn-gateway.tunnel.plist"
export PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
launchctl bootout system "$plist" >/dev/null 2>&1 || true
[ -f /etc/wireguard/vpn-gateway.conf ] && wg-quick down /etc/wireguard/vpn-gateway.conf >/dev/null 2>&1
rm -f "$plist" /usr/local/libexec/vpn-gateway-tunnel /etc/wireguard/vpn-gateway.conf
# DNS files the service wrote; files you wrote yourself do not start with this line and stay.
for f in /etc/resolver/*; do
  first=$(head -n 1 "$f" 2>/dev/null)
  [ -f "$f" ] && { [ "$first" = "# managed by vpn-gateway" ] || [ "$first" = "# managed by vpnhub" ]; } && rm -f "$f"
done
echo "Removed. Import the profile in the WireGuard app again if you want to use it there."
