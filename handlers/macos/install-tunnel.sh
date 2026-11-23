#!/bin/sh
# Run the VPN Gateway WireGuard tunnel as a launchd service, so profile changes apply by themselves.
# Usage: sudo handlers/macos/install-tunnel.sh      (needs: brew install wireguard-tools)
# Run it again when this checkout moves (the service holds the absolute path of data/wireguard/mac.conf), after
# brew upgrades wireguard-tools or wireguard-go (the service runs its own copies), and after the gateway gets new
# keys or you add addresses or DNS domains outside what it pinned: it pins the gateway's public key, the address
# ranges and the domains as they are now, and the service accepts nothing else.
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
# Root runs everything in this folder, so it and its files belong to root and only root can change them.
dir="/Library/PrivilegedHelperTools/vpn-gateway"
bin="$dir/tunnel-service"

tool() {
  for p in /opt/homebrew/bin/$1 /usr/local/bin/$1; do
    [ -x "$p" ] && { (cd "$(dirname "$p")" && realpath "$1"); return; }
  done
}
wg=$(tool wg)
wg_go=$(tool wireguard-go)
if [ -z "$wg" ] || [ -z "$wg_go" ]; then
  echo "wg or wireguard-go not found. Install them first, as your user: brew install wireguard-tools" >&2
  exit 1
fi
if [ ! -f "$profile" ] || [ -L "$profile" ]; then
  echo "No profile yet at $profile. Start the gateway first (vpn web or ./vpn up)." >&2
  exit 1
fi
# The gateway's public key as it is now. The service accepts no other peer, so a program running as your user
# cannot point the tunnel at a server of its own by editing the profile.
peer=$(awk '/^\[Peer\]$/ { p = 1 } p && /^PublicKey = [A-Za-z0-9+\/]+=$/ && length($3) == 44 { print $3; exit }' "$profile")
if [ -z "$peer" ]; then
  echo "The profile at $profile has no gateway key. Start the gateway first (vpn web or ./vpn up)." >&2
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

# The service runs as root, so it and the two programs it starts live in a root-owned folder, not in this checkout
# or in Homebrew's folders, which your user can write. Both programs use only system libraries, so a copy works.
install -d -o root -g wheel -m 755 "$dir"
install -o root -g wheel -m 755 "$here/tunnel-service" "$bin"
install -o root -g wheel -m 755 "$wg" "$dir/wg"
install -o root -g wheel -m 755 "$wg_go" "$dir/wireguard-go"
# What the service may use from now on, in root-owned files: the gateway key, the address ranges of the profile
# and the domains of the dns file, as they are now.
pin() {  # $1: file name; stdin: content
  cat > "$dir/$1.tmp" && chown root:wheel "$dir/$1.tmp" && chmod 644 "$dir/$1.tmp" && mv "$dir/$1.tmp" "$dir/$1"
}
printf '%s\n' "$peer" | pin peer
awk '/^AllowedIPs = / { n = split(substr($0, 14), e, /[, ]+/); for (i = 1; i <= n; i++) if (e[i] != "") print e[i] }' \
  "$profile" | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/([89]|[12][0-9]|3[012])$' | pin allowed
dns="$root/data/wireguard/dns"
{ [ -f "$dns" ] && [ ! -L "$dns" ] && awk '{ print tolower($1) }' "$dns" |
    grep -E '^[a-z0-9-]+(\.[a-z0-9-]+)+$' || true; } | sort -u | pin domains
# The log holds the VPNs' subnets and domains: only root may read it.
log_file=/var/log/vpn-gateway.log
[ -L "$log_file" ] && rm -f "$log_file"
touch "$log_file" && chown root:wheel "$log_file" && chmod 600 "$log_file"
rm -f /usr/local/libexec/vpn-gateway-tunnel  # where older versions kept the script

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
echo "Pinned $(wc -l < "$dir/allowed" | tr -d ' ') address ranges and $(wc -l < "$dir/domains" | tr -d ' ') DNS domains."
echo "Run this again after you add addresses or domains outside them."
echo "Turn off the tunnel for VPN Gateway in the WireGuard app: two clients with the same key fight each other."
echo "Log: /var/log/vpn-gateway.log    Remove: sudo handlers/macos/uninstall-tunnel.sh"
