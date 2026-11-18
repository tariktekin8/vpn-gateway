#!/bin/sh
# Shares this container's tunnel with the gateway: NAT out of the tunnel, publish its subnets and DNS to /state.
# Only the subnets from systems.json go through the tunnel; a system without subnets publishes nothing.
# Runs in the background next to the VPN client. Usage: hub-link.sh <tunnel interface>
iface="$1"
out="/state/${VPN_NAME:?}.routes"
dns_out="/state/${VPN_NAME}.dns"

# Each rule is added once: the page starts this script again to apply new subnets (src/server.py apply_subnets).
rule() { iptables -C "$@" 2>/dev/null || iptables -A "$@"; }
rule POSTROUTING -t nat -o "$iface" -j MASQUERADE
# Nothing on the VPN side may open a connection towards the gateway or the computer; replies still pass.
rule FORWARD -i "$iface" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
rule FORWARD -i "$iface" -j DROP

# VPN_SUBNETS entries are "real" or "real>shown". A shown range is what the computer uses for a range another VPN
# also has: addresses arriving from the gateway are mapped one to one onto the real range (replies map back).
for s in ${VPN_SUBNETS:-}; do
  real=${s%%>*}
  shown=${s#*>}
  [ "$real" = "$shown" ] && continue
  rule PREROUTING -t nat -d "$shown" -j NETMAP --to "$real"
done

# The DNS the VPN pushed, as "nameserver <ip>" and "domain <name>" lines.
vpn_dns() {
  if grep -q '^#@VPNC_GENERATED@' /etc/resolv.conf 2>/dev/null; then
    # openconnect: vpnc-script wrote the VPN's servers and search domains into this container's resolv.conf.
    awk '$1 == "nameserver" { print "nameserver", $2 }
         $1 == "search" || $1 == "domain" { for (i = 2; i <= NF; i++) print "domain", $i }' /etc/resolv.conf
  elif [ -n "${VPN_LOG:-}" ] && [ -f "$VPN_LOG" ]; then
    # openfortivpn: "Got addresses: [..], ns [10.0.0.1, 10.0.0.2], ns_suffix [a.example;b.example]"
    grep 'Got addresses' "$VPN_LOG" | tail -n 1 |
      sed -n 's/.*ns \[\([^]]*\)\], ns_suffix \[\([^]]*\)\].*/\1|\2/p' |
      awk -F'|' '{ n = split($1, a, /[, ]+/); for (i = 1; i <= n; i++) if (a[i] != "") print "nameserver", a[i]
                   m = split($2, d, /[;, ]+/); for (i = 1; i <= m; i++) if (d[i] != "") print "domain", d[i] }'
  fi
}

while :; do
  routes=""
  dns=""
  if ip link show "$iface" up >/dev/null 2>&1; then
    dns=$(vpn_dns)  # published for the page and the Mac's DNS; its servers are reached only inside subnets
    # Connected: give the FortiGate SAML callback port back (see openfortivpn/entrypoint.sh).
    [ "$(cat /state/saml-target 2>/dev/null)" = "$VPN_NAME" ] && rm -f /state/saml-target
    # The gateway gets the shown side of each entry ("real>shown", see src/cli.py's subnets_env).
    for s in ${VPN_SUBNETS:-}; do
      ip route replace "${s%%>*}" dev "$iface"
      routes="$routes ${s#*>}"
    done
  fi
  printf '%s\n' $routes | sort -u > "$out.tmp" && mv "$out.tmp" "$out"
  printf '%s\n' "$dns" > "$dns_out.tmp" && mv "$dns_out.tmp" "$dns_out"
  sleep 5
done
