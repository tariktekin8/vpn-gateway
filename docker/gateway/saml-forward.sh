#!/bin/sh
# One connection on Mac port 8020 (the FortiGate SAML callback) -> the forti-saml container that is logging in.
# That container writes its name to /state/saml-target when its login starts and removes it once connected.
target=$(cat /state/saml-target 2>/dev/null)
case "$target" in "" | *[!a-z0-9-]*) exit 0 ;; esac
exec socat - "TCP:$target:8021"
