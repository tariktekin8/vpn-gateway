#!/bin/bash
# WireGuard server for the Mac. Only the subnets from systems.json (/keys/static-routes, written by the CLI) are
# reachable: they are the Mac profile's AllowedIPs, the firewall's allow-list, and the only routes. Each one goes
# to the VPN container that publishes it in /state while that VPN is connected, and nowhere otherwise (never out
# through Docker's internet connection). Nothing on the VPN side can open a connection to the Mac.
# It also collects the DNS each connected VPN pushed (/state/<name>.dns) into /keys/dns ("domain server..." lines)
# for the macOS tunnel service, keeping only servers that are inside the subnets.
set -euo pipefail
umask 077

KEYS=/keys
WG_NET=10.200.0.0/24
MAC_IP=10.200.0.2

[ -s "$KEYS/server.key" ] || wg genkey > "$KEYS/server.key"
[ -s "$KEYS/mac.key" ] || wg genkey > "$KEYS/mac.key"
rm -f "$KEYS/dns-servers"  # older versions kept DNS servers in the profile outside the subnets

ip link add wg0 type wireguard
wg set wg0 listen-port 51820 private-key "$KEYS/server.key" \
  peer "$(wg pubkey < "$KEYS/mac.key")" allowed-ips "$MAC_IP/32"
ip addr add 10.200.0.1/24 dev wg0
ip link set wg0 up
# VPN containers see the gateway as the source, so they need no route back to the Mac.
iptables -t nat -A POSTROUTING -s "$WG_NET" -o eth0 -j MASQUERADE
# Firewall: replies pass; from the Mac only to the subnets (chain ALLOWED, filled below); nothing towards the Mac.
iptables -N ALLOWED
iptables -A FORWARD -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -A FORWARD -i wg0 -o eth0 -j ALLOWED
iptables -A FORWARD -j DROP

# FortiGate SAML logins end on http://127.0.0.1:8020/ in the Mac browser. One port serves every forti-saml system:
# each connection goes to the system whose login runs now (see saml-forward.sh).
socat TCP-LISTEN:8020,fork,reuseaddr EXEC:/usr/local/bin/saml-forward.sh &

write_profile() {  # $1: comma-separated AllowedIPs
  cat > "$KEYS/mac.conf.tmp" <<EOF
[Interface]
PrivateKey = $(cat "$KEYS/mac.key")
Address = $MAC_IP/32

[Peer]
PublicKey = $(wg pubkey < "$KEYS/server.key")
Endpoint = 127.0.0.1:51820
AllowedIPs = $1
PersistentKeepalive = 25
EOF
  mv "$KEYS/mac.conf.tmp" "$KEYS/mac.conf"
}

# True if IPv4 address $1 is inside one of the networks in file $2 (one CIDR per line).
inside() {
  awk -v ip="$1" 'function n(a,  p) { split(a, p, "."); return ((p[1] * 256 + p[2]) * 256 + p[3]) * 256 + p[4] }
    NF { split($1, c, "/"); s = 2 ^ (32 - c[2]); if (int(n(ip) / s) == int(n(c[1]) / s)) f = 1 }
    END { exit !f }' "$2"
}

declare -A routed=()   # subnet -> next hop, or "unreachable"
last_allowed=""
last_static="-"
last_dns="-"  # never a real value, so the first round always writes the file
while :; do
  static=$(cat "$KEYS/static-routes" 2>/dev/null || true)
  if [ "$static" != "$last_static" ]; then
    iptables -F ALLOWED
    for net in $static; do iptables -A ALLOWED -d "$net" -j ACCEPT; done
    last_static=$static
  fi
  declare -A want=()
  for net in $static; do want[$net]=unreachable; done  # no VPN connected for it: drop, never fall back to the internet
  for f in /state/*.routes; do
    [ -e "$f" ] || continue
    name=$(basename "$f" .routes)
    hop=$(getent hosts "$name" | awk '{print $1; exit}') || true
    [ -n "$hop" ] || continue
    while read -r net; do
      # Only subnets from systems.json; anything else a container lists is ignored.
      [ -n "$net" ] && [ -n "${want[$net]:-}" ] && want[$net]=$hop
    done < "$f"
  done
  for net in "${!want[@]}"; do
    if [ "${routed[$net]:-}" != "${want[$net]}" ]; then
      if [ "${want[$net]}" = unreachable ]; then
        ip route replace unreachable "$net" && routed[$net]=unreachable
      else
        ip route replace "$net" via "${want[$net]}" && routed[$net]=${want[$net]}
      fi
    fi
  done
  for net in "${!routed[@]}"; do
    if [ -z "${want[$net]:-}" ]; then
      ip route del "$net" 2>/dev/null || true
      unset "routed[$net]"
    fi
  done
  # DNS of connected systems: one line per domain with its servers inside the subnets. Odd names are dropped.
  dns=""
  printf '%s\n' $static > /tmp/static
  for f in /state/*.dns; do
    [ -e "$f" ] || continue
    name=$(basename "$f" .dns)
    getent hosts "$name" >/dev/null || continue
    servers=""
    for ip in $(awk '$1 == "nameserver" && $2 ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/ { print $2 }' "$f"); do
      inside "$ip" /tmp/static && servers+=" $ip"
    done
    [ -n "$servers" ] || continue
    for domain in $(awk '$1 == "domain" { print tolower($2) }' "$f" | grep -E '^[a-z0-9-]+(\.[a-z0-9-]+)+$'); do
      dns+="$domain$servers"$'\n'
    done
  done
  dns=$(printf '%s' "$dns" | sort -u)
  if [ "$dns" != "$last_dns" ]; then
    printf '%s\n' "$dns" > "$KEYS/dns.tmp" && mv "$KEYS/dns.tmp" "$KEYS/dns"
    echo "DNS for the Mac: ${dns//$'\n'/; }"
    last_dns=$dns
  fi
  # shellcheck disable=SC2086  # one subnet per word
  allowed=$(printf '%s\n' 10.200.0.1/32 $static | sort -uV | paste -sd, - | sed 's/,/, /g')
  if [ "$allowed" != "$last_allowed" ]; then
    write_profile "$allowed"
    echo "Mac profile updated: AllowedIPs = $allowed"
    last_allowed=$allowed
  fi
  unset want
  sleep 5
done
