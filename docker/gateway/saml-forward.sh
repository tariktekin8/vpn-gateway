#!/bin/bash
# One connection on Mac port 8020 (the FortiGate SAML callback) -> the forti-saml container that is logging in.
# VPN Gateway on the computer writes the name of the login started last into /config/saml-target; a VPN container
# cannot choose it, and it counts for five minutes only: a website that sends the browser to 127.0.0.1:8020 later
# reaches nothing. The container must also still be logging in: it writes saml-target into its own state volume
# (/state/<name>/ here) when its login starts and removes it once connected. And it must be alive (its hub-link.sh
# wrote <name>.routes in the last 15 seconds): a stopped container leaves saml-target behind, and its name must not
# be looked up then, or Docker's DNS would ask the local network's DNS, which could answer with any address.
[ -f /config/saml-target ] && [ ! -L /config/saml-target ] || exit 0
written=$(stat -c %Y /config/saml-target 2>/dev/null) || exit 0
age=$(( $(date +%s) - written ))
[ "$age" -ge -30 ] && [ "$age" -le 300 ] || exit 0  # a file time far ahead (a clock gap after sleep) does not count
target=$(head -n 1 /config/saml-target)
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
# Browsers open spare connections ahead of time and may leave them idle. openfortivpn's listener serves one
# connection at a time, so an idle one would hold back the real callback until the browser drops it (about a
# minute). Connect only once the request line has come; a connection that stays silent for 60 s is closed.
IFS= read -r -t 60 -n 8192 line || exit 0
{ printf '%s\n' "$line"; timeout 30 cat; } | socat - "TCP:$hop:8021"
