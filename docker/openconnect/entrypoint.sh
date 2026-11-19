#!/bin/bash
# GlobalProtect via openconnect. The login (password or SAML cookie, then any MFA answers) arrives through a pipe.
set -eu

: "${VPN_HOST:?set VPN_HOST in systems.json}"
: "${VPN_USER:?set VPN_USER in systems.json}"

hub-link.sh tun0 &

# VPN Gateway writes the login lines with `docker exec -i ... cat > /run/vpn/in` (src/cli.py send_lines).
# /run/vpn is a tmpfs and a FIFO holds no data on disk, so the password or cookie is never stored.
# Opened read-write: the open does not wait for a writer, and openconnect never sees end of input,
# so a later prompt (an MFA code) waits for the next line instead of failing.
rm -f /run/vpn/in
mkfifo -m 600 /run/vpn/in
exec 3<>/run/vpn/in

# The first line answers the first prompt; GlobalProtect asks again at the gateway, so the login is sent twice.
# Default vpnc-script changes routes and DNS only inside this container's own network namespace.
exec openconnect --protocol=gp --user="$VPN_USER" --interface=tun0 --passwd-on-stdin \
  "$VPN_HOST" ${VPN_EXTRA_ARGS:-} <&3
