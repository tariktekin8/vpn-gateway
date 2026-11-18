#!/bin/sh
# Run the VPN Gateway WireGuard tunnel with wg-quick as a launchd service, so profile changes apply by themselves.
# Usage: sudo handlers/macos/install-tunnel.sh      (needs: brew install wireguard-tools)
# Run it again when this checkout moves: the service holds the absolute path of data/wireguard/mac.conf.
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "Run it with sudo: sudo $0" >&2
  exit 1
fi
here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../.." && pwd)"
profile="$root/data/wireguard/mac.conf"
label="com.athenayazilim.vpn-gateway.tunnel"
plist="/Library/LaunchDaemons/$label.plist"
bin="/usr/local/libexec/vpn-gateway-tunnel"

if [ ! -x /opt/homebrew/bin/wg-quick ] && [ ! -x /usr/local/bin/wg-quick ]; then
  echo "wg-quick not found. Install it first, as your user: brew install wireguard-tools" >&2
  exit 1
fi
if [ ! -f "$profile" ]; then
  echo "No profile yet at $profile. Start the gateway first (vpn web or ./vpn up)." >&2
  exit 1
fi

# Remove the service of the same tool from before the project was renamed (VPN Hub), if any.
old_plist="/Library/LaunchDaemons/com.athenayazilim.vpnhub.wireguard.plist"
if [ -f "$old_plist" ]; then
  launchctl bootout system "$old_plist" >/dev/null 2>&1 || true
  PATH=/opt/homebrew/bin:/usr/local/bin:$PATH wg-quick down /etc/wireguard/vpnhub.conf >/dev/null 2>&1 || true
  rm -f "$old_plist" /usr/local/libexec/vpnhub-wg /etc/wireguard/vpnhub.conf
  echo "Removed the old VPN Hub tunnel service."
fi

# The service runs as root, so its script lives in a root-owned place, not in this (user-writable) checkout.
mkdir -p /usr/local/libexec
install -o root -g wheel -m 755 "$here/tunnel-service" "$bin"

cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array><string>$bin</string><string>$profile</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/var/log/vpn-gateway.log</string>
  <key>StandardErrorPath</key><string>/var/log/vpn-gateway.log</string>
</dict>
</plist>
EOF
chown root:wheel "$plist"
chmod 644 "$plist"

launchctl bootout system "$plist" >/dev/null 2>&1 || true
launchctl bootstrap system "$plist"
echo "Installed. The VPN Gateway tunnel now follows $profile by itself."
echo "Turn off the tunnel for VPN Gateway in the WireGuard app: two clients with the same key fight each other."
echo "Log: /var/log/vpn-gateway.log    Remove: sudo handlers/macos/uninstall-tunnel.sh"
