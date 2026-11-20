#!/bin/sh
# One connection on Mac port 8020 (the FortiGate SAML callback) -> the forti-saml container that is logging in.
# VPN Gateway on the computer writes the name of the login started last into /keys/saml-target; a VPN container
# cannot choose it. The container must also still be logging in: it writes saml-target into its own state volume
# (/state/<name>/ here) when its login starts and removes it once connected.
target=$(head -n 1 /keys/saml-target 2>/dev/null)
case "$target" in "" | *[!a-z0-9-]*) exit 0 ;; esac
[ -f "/state/$target/saml-target" ] && [ ! -L "/state/$target/saml-target" ] || exit 0
exec socat - "TCP:$target:8021"
