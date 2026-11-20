#!/bin/sh
# Stop and remove the VPN Gateway tunnel service that install-tunnel.sh set up.
# Usage: sudo handlers/macos/uninstall-tunnel.sh
set -u
if [ "$(id -u)" -ne 0 ]; then
  echo "Run it with sudo: sudo $0" >&2
  exit 1
fi
plist="/Library/LaunchDaemons/com.athenayazilim.vpn-gateway.tunnel.plist"
export PATH=/usr/bin:/bin:/usr/sbin:/sbin
launchctl bootout system "$plist" >/dev/null 2>&1 || true
# Stop the tunnel: wireguard-go exits when its socket is gone, and the interface and its routes go with it.
name_file=/var/run/wireguard/vpn-gateway.name
if [ -s "$name_file" ]; then
  iface=$(cat "$name_file")
  case "$iface" in utun[0-9]*) rm -f "/var/run/wireguard/$iface.sock" ;; esac
  rm -f "$name_file"
fi
rm -rf /Library/PrivilegedHelperTools/vpn-gateway
rm -f "$plist" /usr/local/libexec/vpn-gateway-tunnel /etc/wireguard/vpn-gateway.conf
# DNS files the service wrote; files you wrote yourself do not start with this line and stay.
for f in /etc/resolver/*; do
  first=$(head -n 1 "$f" 2>/dev/null)
  [ -f "$f" ] && { [ "$first" = "# managed by vpn-gateway" ] || [ "$first" = "# managed by vpnhub" ]; } && rm -f "$f"
done
echo "Removed. Import the profile in the WireGuard app again if you want to use it there."
