#!/bin/sh
# One connection on Mac port 8020 (the FortiGate SAML callback) -> the forti-saml container that is logging in.
# That container writes saml-target into its own state volume (/state/<name>/ here) when its login starts and
# removes it once connected. The folder name says which one; the last login started wins.
target=$(ls -t /state/*/saml-target 2>/dev/null | head -n 1)
target=${target#/state/}
target=${target%/saml-target}
case "$target" in "" | *[!a-z0-9-]*) exit 0 ;; esac
exec socat - "TCP:$target:8021"
