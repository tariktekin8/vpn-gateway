#!/bin/sh
# Run the VPN Gateway WireGuard tunnel as a launchd service, so profile changes apply by themselves.
# Usage: sudo handlers/macos/install-tunnel.sh [--yes]      (needs: brew install wireguard-tools)
# It shows what the service will trust (the gateway's public key, the address ranges, the DNS domains and the three
# programs it installs, with their SHA-256) and asks before it changes anything; --yes skips the question.
# Run it again when this checkout moves (the service holds the absolute path of data/wireguard/mac.conf), after
# brew upgrades wireguard-tools or wireguard-go (the service runs its own copies), and after the gateway gets new
# keys or you add addresses or DNS domains outside what it pinned: the service accepts nothing else.
# The whole script is one function, called on the last line: the shell reads all of it before it runs any of it,
# so editing this file while it waits for your answer changes nothing.
main() {
set -eu
# sudo on macOS keeps your PATH, which may start with folders your user can write (Homebrew). Root runs only
# system programs here.
PATH=/usr/bin:/bin:/usr/sbin:/sbin
export PATH

yes=""
case "${1:-}" in
  --yes) yes=1 ;;
  "") ;;
  *) echo "Usage: sudo $0 [--yes]" >&2; exit 2 ;;
esac
if [ "$(id -u)" -ne 0 ]; then
  echo "Run it with sudo: sudo $0" >&2
  exit 1
fi
here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../.." && pwd)"
profile="$root/data/wireguard/mac.conf"
dns="$root/data/wireguard/dns"
label="com.athenayazilim.vpn-gateway.tunnel"
plist="/Library/LaunchDaemons/$label.plist"
# Root runs everything in this folder, so it and its files belong to root and only root can change them.
dir="/Library/PrivilegedHelperTools/vpn-gateway"
bin="$dir/tunnel-service"
# The same domain rule as src/cli.py (DOMAIN_RE), docker/gateway/entrypoint.sh and tunnel-service.
DOMAIN_RE='^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$'

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

# What the service will trust from now on. These files sit in a folder your user can write, so they are shown in
# full below and you confirm them.
peer=$(awk '/^\[Peer\]$/ { p = 1 } p && /^PublicKey = [A-Za-z0-9+\/]+=$/ && length($3) == 44 { print $3; exit }' "$profile")
if [ -z "$peer" ]; then
  echo "The profile at $profile has no gateway key. Start the gateway first (vpn web or ./vpn up)." >&2
  exit 1
fi
allowed=$(awk '/^AllowedIPs = / { n = split(substr($0, 14), e, /[, ]+/); for (i = 1; i <= n; i++) if (e[i] != "") print e[i] }' \
  "$profile" | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/([89]|[12][0-9]|3[012])$' | sort -u || true)
domains=""
if [ -f "$dns" ] && [ ! -L "$dns" ]; then
  domains=$(awk '{ print tolower($1) }' "$dns" | grep -E "$DOMAIN_RE" | awk 'length($0) <= 253' | sort -u || true)
fi

# $1: title; $2: the new list; $3: the file with the list pinned last time. "+" marks a new line, "-" one that goes.
show() {
  echo "$1:"
  printf '%s\n' "$2" | while IFS= read -r line; do
    [ -n "$line" ] || continue
    if [ -f "$3" ] && grep -Fxq -- "$line" "$3"; then echo "    $line"; else echo "  + $line"; fi
  done
  [ -f "$3" ] || return 0
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    printf '%s\n' "$2" | grep -Fxq -- "$line" || echo "  - $line"
  done < "$3"
}
sum() { shasum -a 256 "$1" | awk '{ print $1 }'; }

# The three programs root will run are copied first into a folder only root can write, and hashed there: what you
# confirm is what gets installed, even if the originals change while the question waits.
stage=$(mktemp -d /tmp/vpn-gateway-install.XXXXXX)
trap 'rm -rf "$stage"' EXIT
install -o root -g wheel -m 755 "$here/tunnel-service" "$stage/tunnel-service"
install -o root -g wheel -m 755 "$wg" "$stage/wg"
install -o root -g wheel -m 755 "$wg_go" "$stage/wireguard-go"

echo "The tunnel service will trust only what is listed here. + is new since the last install, - goes away."
show "Gateway public key" "$peer" "$dir/peer"
show "Address ranges" "$allowed" "$dir/allowed"
show "DNS domains" "$domains" "$dir/domains"
echo "Programs installed into $dir (run as root):"
for f in "$here/tunnel-service:tunnel-service" "$wg:wg" "$wg_go:wireguard-go"; do
  echo "  ${f%:*}"
  echo "    sha256 $(sum "$stage/${f##*:}")"
done
if [ -z "$yes" ]; then
  if ! { exec 3<>/dev/tty; } 2>/dev/null; then
    echo "No terminal to ask on. Nothing changed. Run it again with --yes to accept without asking." >&2
    exit 1
  fi
  printf 'Install with these? [y/N] ' >&3
  answer=""
  read -r answer <&3 || true
  exec 3>&-
  case "$answer" in
    y | Y | yes | YES) ;;
    *) echo "Nothing changed."; exit 1 ;;
  esac
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
install -o root -g wheel -m 755 "$stage/tunnel-service" "$bin"
install -o root -g wheel -m 755 "$stage/wg" "$dir/wg"
install -o root -g wheel -m 755 "$stage/wireguard-go" "$dir/wireguard-go"
pin() {  # $1: file name; stdin: content
  cat > "$dir/$1.tmp" && chown root:wheel "$dir/$1.tmp" && chmod 644 "$dir/$1.tmp" && mv "$dir/$1.tmp" "$dir/$1"
}
printf '%s\n' "$peer" | pin peer
printf '%s\n' "$allowed" | grep -v '^$' | pin allowed
printf '%s\n' "$domains" | grep -v '^$' | pin domains
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
echo "Run this again after you add addresses or domains outside the ones listed above."
echo "Turn off the tunnel for VPN Gateway in the WireGuard app: two clients with the same key fight each other."
echo "Log: /var/log/vpn-gateway.log (root only)    Remove: sudo handlers/macos/uninstall-tunnel.sh"
}

main "$@"
exit $?
