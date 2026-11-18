#!/bin/bash
# Starts openfortivpn with SAML login. The browser step happens on the Mac.
set -eu

: "${VPN_HOST:?set host (host or host:port) in systems.json}"

# hub-link.sh reads the DNS the VPN pushed from this copy of the log.
export VPN_LOG=/run/openfortivpn.log
: > "$VPN_LOG"
hub-link.sh ppp0 &

# Claim the Mac's SAML callback port (8020, served by the gateway) for this login; hub-link.sh releases it once
# the tunnel is up. The last system to start its login gets the port.
printf '%s\n' "$VPN_NAME" > /state/saml-target

# openfortivpn's SAML listener binds 127.0.0.1 only; expose it on 8021 for the gateway.
socat TCP-LISTEN:8021,fork,reuseaddr TCP:127.0.0.1:8020 &

# --set-dns=0: never touch DNS. Routes only change inside this container's own network namespace.
# exec keeps openfortivpn as PID 1 (it gets docker stop's signal); tee copies its output to the log file.
exec openfortivpn "$VPN_HOST" --saml-login --set-dns=0 ${VPN_EXTRA_ARGS:-} > >(tee -a "$VPN_LOG") 2>&1
