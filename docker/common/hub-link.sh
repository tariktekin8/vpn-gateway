#!/bin/sh
# Shares this container's tunnel with the gateway: NAT out of the tunnel, publish its subnets and DNS to /state.
# Only the subnets from systems.json go through the tunnel; a system without subnets publishes nothing.
# Runs in the background next to the VPN client. Usage: hub-link.sh <tunnel interface>
iface="$1"
out="/state/${VPN_NAME:?}.routes"
dns_out="/state/${VPN_NAME}.dns"

# Each rule is added once: the page starts this script again to apply new subnets (src/server.py apply_subnets).
rule() { iptables -C "$@" 2>/dev/null || iptables -A "$@"; }
rule6() { ip6tables -C "$@" 2>/dev/null || ip6tables -A "$@" 2>/dev/null || true; }  # no IPv6 support: nothing to do
rule POSTROUTING -t nat -o "$iface" -j MASQUERADE
# Nothing on the VPN side may open a connection towards the gateway, the computer or this container itself (for
# example the FortiGate SAML relay on 8021); replies still pass. The same for IPv6, if the VPN gives one.
for chain in FORWARD INPUT; do
  rule "$chain" -i "$iface" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
  rule "$chain" -i "$iface" -j DROP
  rule6 "$chain" -i "$iface" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
  rule6 "$chain" -i "$iface" -j DROP
done

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

# idle_minutes in systems.json: disconnect when nothing went into the tunnel for that long. Only packets sent
# into the tunnel count (they come from the computer); traffic arriving from the VPN side does not.
idle_limit=$(( ${VPN_IDLE_MINUTES:-0} * 60 ))
idle_for=0
last_tx=""

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
    if [ "$idle_limit" -gt 0 ]; then
      tx=$(cat "/sys/class/net/$iface/statistics/tx_packets" 2>/dev/null || echo 0)
      if [ "$tx" = "$last_tx" ]; then idle_for=$((idle_for + 5)); else idle_for=0; last_tx=$tx; fi
      if [ "$idle_for" -ge "$idle_limit" ]; then
        echo "No traffic for ${VPN_IDLE_MINUTES} minutes: disconnecting (idle_minutes in systems.json)."
        kill -TERM 1  # the VPN client is PID 1; it logs out and the container stops
      fi
    fi
  fi
  printf '%s\n' $routes | sort -u > "$out.tmp" && mv "$out.tmp" "$out"
  printf '%s\n' "$dns" > "$dns_out.tmp" && mv "$dns_out.tmp" "$dns_out"
  sleep 5
done
