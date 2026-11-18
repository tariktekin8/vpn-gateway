#!/bin/bash
# GlobalProtect via openconnect. Without VPN_PASSWORD_FILE, openconnect asks for the password on the terminal.
set -eu

: "${VPN_HOST:?set VPN_HOST in systems.json}"
: "${VPN_USER:?set VPN_USER in systems.json}"

hub-link.sh tun0 &

# Default vpnc-script changes routes and DNS only inside this container's own network namespace.
if [ -n "${VPN_PASSWORD_FILE:-}" ]; then
  # Password comes from a Docker secret file; it never appears in env, args or logs.
  # Sent twice: GlobalProtect asks once at the portal and again at the gateway (after its 512 reply).
  exec openconnect --protocol=gp --user="$VPN_USER" --interface=tun0 --passwd-on-stdin \
    "$VPN_HOST" ${VPN_EXTRA_ARGS:-} < <(cat "$VPN_PASSWORD_FILE" "$VPN_PASSWORD_FILE")
fi
exec openconnect --protocol=gp --user="$VPN_USER" --interface=tun0 "$VPN_HOST" ${VPN_EXTRA_ARGS:-}
