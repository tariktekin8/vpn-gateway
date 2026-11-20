#!/bin/bash
# WireGuard server for the Mac. Only the subnets from systems.json (/keys/static-routes, written by the CLI) are
# reachable: they are the Mac profile's AllowedIPs, the firewall's allow-list, and the only routes. Each one goes
# to the VPN container that publishes it in /state while that VPN is connected, and nowhere otherwise (never out
# through Docker's internet connection). Nothing on the VPN side can open a connection to the Mac.
# It also writes /keys/dns ("domain server..." lines) for the macOS tunnel service: the domains approved for each
# VPN (/keys/dns-owners), with the DNS servers that VPN pushed inside its own subnets, or this gateway while it is off.
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
# DNS on the WireGuard address only, with no upstream: every question gets "no such name" at once. The Mac sends
# here the domains of a VPN that is off, so they fail fast instead of leaking to the computer's normal DNS.
dnsmasq --conf-file=/dev/null --no-resolv --no-hosts --no-poll --bind-interfaces --listen-address=10.200.0.1 \
  --except-interface=lo --address='/#/' --user=nobody --pid-file=
# VPN containers see the gateway as the source, so they need no route back to the Mac.
# One network per VPN container (eth1, eth2, ...), plus Docker's default network with the published ports.
iptables -t nat -A POSTROUTING -s "$WG_NET" -o eth+ -j MASQUERADE
# Firewall: replies pass; from the Mac only to the subnets (chain ALLOWED, filled below); nothing towards the Mac.
iptables -N ALLOWED
iptables -A FORWARD -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -A FORWARD -i wg0 -o eth+ -j ALLOWED
iptables -A FORWARD -j DROP  # also every VPN container towards another one
# VPN containers may not open connections to the gateway itself (for example the SAML port 8020); only the
# default network (published ports), WireGuard and replies may.
DEFAULT_IF=$(ip -4 route show default | awk '{print $5; exit}')
iptables -A INPUT -i lo -j ACCEPT
iptables -A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -A INPUT -i wg0 -j ACCEPT
iptables -A INPUT -i "${DEFAULT_IF:?no default route}" -j ACCEPT
iptables -A INPUT -j DROP

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
  limits=$(cat "$KEYS/port-limits" 2>/dev/null || true)  # "subnet port,port": only these ports there
  if [ "$static|$limits" != "$last_static" ]; then
    declare -A ports=()
    while read -r net list; do [ -n "$net" ] && ports[$net]=$list; done <<< "$limits"
    iptables -F ALLOWED
    for net in $static; do
      if [ -n "${ports[$net]:-}" ]; then
        for proto in tcp udp; do
          iptables -A ALLOWED -d "$net" -p "$proto" -m multiport --dports "${ports[$net]}" -j ACCEPT
        done
      else
        iptables -A ALLOWED -d "$net" -j ACCEPT
      fi
    done
    unset ports
    last_static="$static|$limits"
  fi
  declare -A want=() owner=()
  while read -r net who; do [ -n "$net" ] && owner[$net]=$who; done < <(cat "$KEYS/route-owners" 2>/dev/null || true)
  for net in $static; do want[$net]=unreachable; done  # no VPN connected for it: drop, never fall back to the internet
  for f in /state/*/*.routes; do
    [ -f "$f" ] && [ ! -L "$f" ] || continue  # a VPN container could point a symlink at /keys/*.key
    name=$(basename "$(dirname "$f")")  # the volume's folder, which only that VPN can write
    [ "$f" = "/state/$name/$name.routes" ] || continue
    hop=$(getent hosts "$name" | awk '{print $1; exit}') || true
    [ -n "$hop" ] || continue
    while read -r net; do
      # Only this VPN's own subnets from systems.json; anything else a container lists is ignored.
      [ -n "$net" ] && [ -n "${want[$net]:-}" ] && [ "${owner[$net]:-}" = "$name" ] && want[$net]=$hop
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
  # DNS for the Mac: one line per domain the user approved for a VPN (data/wireguard/dns-owners). A connected VPN
  # gets the DNS servers it pushed that are inside its own subnets. Domains the VPN pushed are only suggestions for
  # the page: a VPN cannot take over another organization's names or public ones such as a big website's.
  # A VPN that is off (or pushed no usable server) gets this gateway's dnsmasq, which answers "no such name":
  # the lookup fails at once instead of sending the internal name to the computer's normal DNS.
  dns=""
  for name in $(awk '{ print $2 }' "$KEYS/dns-owners" 2>/dev/null | grep -E '^[a-z][a-z0-9-]{0,30}$' | sort -u); do
    servers=""
    f="/state/$name/$name.dns"
    if [ -f "$f" ] && [ ! -L "$f" ] && getent hosts "$name" >/dev/null; then
      awk -v n="$name" '$2 == n { print $1 }' "$KEYS/route-owners" 2>/dev/null > /tmp/own-nets || true
      for ip in $(awk '$1 == "nameserver" && $2 ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/ { print $2 }' "$f"); do
        inside "$ip" /tmp/own-nets && servers+=" $ip"
      done
    fi
    [ -n "$servers" ] || servers=" 10.200.0.1"
    for domain in $(awk -v n="$name" '$2 == n { print $1 }' "$KEYS/dns-owners" 2>/dev/null |
                    grep -E '^[a-z0-9-]+(\.[a-z0-9-]+)+$'); do
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
  unset want owner
  sleep 5
done
