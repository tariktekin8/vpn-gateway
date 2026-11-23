#!/bin/bash
# One connection on Mac port 8020 (the FortiGate SAML callback) -> the forti-saml container that is logging in.
# VPN Gateway on the computer writes the name of the login started last into /keys/saml-target; a VPN container
# cannot choose it. The container must also still be logging in: it writes saml-target into its own state volume
# (/state/<name>/ here) when its login starts and removes it once connected. And it must be alive (its hub-link.sh
# wrote <name>.routes in the last 15 seconds): a stopped container leaves saml-target behind, and its name must not
# be looked up then, or Docker's DNS would ask the local network's DNS, which could answer with any address.
target=$(head -n 1 /keys/saml-target 2>/dev/null)
[[ "$target" =~ ^[a-z][a-z0-9-]{0,30}$ ]] || exit 0
[ -f "/state/$target/saml-target" ] && [ ! -L "/state/$target/saml-target" ] || exit 0
routes="/state/$target/$target.routes"
[ -f "$routes" ] && [ ! -L "$routes" ] || exit 0
t=$(stat -c %Y "$routes" 2>/dev/null) || exit 0
[ $(( $(date +%s) - t )) -le 15 ] || exit 0
hop=$(getent hosts "$target" | awk '{print $1; exit}')
[[ "$hop" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || exit 0
default_if=$(ip -4 route show default | awk '{print $5; exit}')
route=$(ip -4 route get "$hop" 2>/dev/null | head -n 1)
case "$route" in *" via "* | "" | *" dev ${default_if:-none} "*) exit 0 ;; *" dev eth"*) ;; *) exit 0 ;; esac
exec socat - "TCP:$hop:8021"
