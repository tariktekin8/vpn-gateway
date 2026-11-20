# Changelog

What each version of VPN Gateway added or changed. The newest version is on top.

## Unreleased

### Security

- **Each VPN is kept apart.** Each VPN container has its own Docker network (`net-<name>`) and its own state volume
  (`state-<name>`). Only the gateway joins every network. A VPN container cannot reach another one, send packets
  into another organization's network, open connections to the gateway, claim another VPN's subnets or take over
  another VPN's FortiGate SAML callback. A subnet is routed only to the VPN that lists it in `systems.json`.
- **Passwords are never stored.** `gp-password` asks for the password at every connect, on the page or with
  `./vpn login`. Passwords, SAML cookies and MFA codes go into the VPN container through a pipe in memory
  (`/run/vpn/in` on a tmpfs). They never reach a file, the compose file, an argument list, the environment or a
  log. `systems.json` refuses `password` and other secret fields. MFA codes use the same pipe (the page's **Code**
  box or `./vpn type <name>`), so they also work on Windows.
- **The page opens with a one-time link.** `./vpn web` prints `http://127.0.0.1:<port>/#token=...` and opens it.
  The page itself holds no token. The page keeps the token for its tab only and removes it from the address bar.
- **Port limits per address.** An address may end with `ports 443,3389` (up to 15 ports). The gateway then forwards
  only those TCP and UDP ports.
- **Disconnect when idle.** `idle_minutes` (1 to 1440), or **Disconnect when idle** on the page, stops a VPN when
  nothing went into its tunnel for that long.
- **DNS domains need your approval.** A VPN's domains are used only after you allow them in the VPN's **Names** tab
  (`domains` in `systems.json`). A domain cannot belong to two VPNs. Only DNS servers inside the VPN's own subnets
  are used.
- **Internal names do not leak while a VPN is off.** The gateway runs dnsmasq on its WireGuard address only, with no
  upstream. While a VPN is off, its allowed domains point there and get "no such name" at once, instead of going
  to the computer's normal DNS.
- **Login cookies are hidden in the logs.** The VPN clients' output goes through `hide-cookies.sh`, which replaces
  every `...cookie=value` with `<hidden>`.
- **Pinned images.** Every image starts from one fixed `debian:bookworm-slim` digest, and openfortivpn is built from
  one fixed commit.

### Added

- Security sections in `README.md` and `docs/technical.md`: what VPN Gateway gives you and what it cannot give you.
- `docs/adding-a-vpn-client.md`: the security checklist for a new VPN client or login method.

### Fixed

- Windows: the page stayed on "Loading..." because the registry mapped `.js` to `text/plain`. Web files are now
  served with fixed content types.
- Windows: secret and route files were written with CRLF and the local code page, and a VPN container read
  `password\r`. These files are now written as UTF-8 with LF.
- Windows: the **Local** stop stayed "not checked". It now checks each address's interface.
- Mac: with the automatic tunnel installed, the page no longer offers the WireGuard profile download. Importing it
  into the WireGuard app starts a second client with the same key, and the two break each other.

### Upgrading from 1.0.0

- Needs Docker 28 or later with Compose 2.33 or later (`gw_priority`).
- Disconnect and connect each VPN once. Containers from 1.0.0 still use the old shared network and volume, and
  their `/run/secrets` mount still holds the password.
- At the first start, a stored `password` is removed from `data/systems.json`, and password and cookie files in
  `data/secrets/` are deleted.
- Open the printed link to use the page. A bookmark of `http://127.0.0.1:<port>/` shows "This page has no valid
  key".
- Names of the organization stop working until you allow their domains: open each VPN's **Names** tab and press
  **Use** next to each domain.
- Mac with the automatic tunnel: run `sudo handlers/macos/install-tunnel.sh` again.
- The old volume `vpn-gateway_state` is no longer used. Remove it with `docker volume rm vpn-gateway_state`.

## 1.0.0 (2026-11-18)

First release.

- Several organization VPNs open at the same time on one computer: Palo Alto GlobalProtect with a password
  (`gp-password`) or single sign-on (`gp-saml`), and Fortinet FortiGate with single sign-on (`forti-saml`).
- Each VPN runs in its own Docker container. A gateway container joins them and the computer reaches it through
  WireGuard.
- One web page (`./vpn web`) to start the gateway, add VPNs, connect, sign in and change addresses.
- Only the listed addresses go through a VPN. Two VPNs with the same addresses: map one to another range with `as`.
- Each address shows three checks, from the computer through the gateway into the VPN.
- Address changes apply while a VPN is connected.
- Mac: an automatic tunnel service that applies profile changes and writes `/etc/resolver` files for the VPNs' DNS.
- Mac and Windows: a sign-in helper for `gp-saml` callbacks.
- Command line: `./vpn` (`.\vpn.cmd` on Windows) for every step of the page.
