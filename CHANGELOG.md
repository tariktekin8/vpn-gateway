# Changelog

What each version of VPN Gateway added or changed. The newest version is on top.

Versions follow [Semantic Versioning](https://semver.org): `MAJOR.MINOR.PATCH`. MAJOR changes when an upgrade
needs you to do something (the version has an "Upgrading" part), MINOR adds features that keep working as before,
and PATCH only fixes bugs. Each version is a git tag, for example `v2.0.0`, and a GitHub release with the same
text as here.

## Unreleased

Nothing yet.

## 2.0.0 (2026-11-24)

### Security

- **Each VPN is kept apart.** Each VPN container has its own Docker network (`net-<name>`) and its own state volume
  (`state-<name>`). Only the gateway joins every network. A VPN container cannot reach another one, send packets
  into another organization's network, open connections to the gateway, claim another VPN's subnets or take over
  another VPN's FortiGate SAML callback. A subnet is routed only to the VPN that lists it in `systems.json`.
- **Passwords are never stored.** `gp-password` asks for the password at every connect, on the page or with
  `./vpn login`. Passwords, SAML cookies and MFA codes go into the VPN container through a pipe in memory
  (`/run/vpn/in` on a tmpfs). They never reach a file, the compose file, an argument list, the environment or a
  log. A `password` in `systems.json` is deleted, and other secret fields are refused. MFA codes use the same pipe
  (the page's **Code** box or `./vpn type <name>`), so they also work on Windows.
- **The page opens with a link that holds its key.** `./vpn web` prints `http://127.0.0.1:<port>/#token=...` and
  opens it. The page itself holds no token. The page keeps the token for its tab only and removes it from the
  address bar.
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
- **The Mac tunnel service runs nothing your user can change.** It no longer uses `wg-quick` or Homebrew's folders:
  the installer copies `wg` and `wireguard-go` into root-owned `/Library/PrivilegedHelperTools/vpn-gateway/`, and
  the service's `PATH` holds only system folders. It reads its inputs only from regular files you own (no links),
  refuses a `/0` in `AllowedIPs`, and logs only checked values.
- **The computer picks the FortiGate SAML callback target.** `data/gateway/saml-target` names the login started
  last; a VPN container can no longer claim another organization's callback.
- **Sign-in links are checked.** The FortiGate "Authenticate at" link opens only if it is `https://` on the VPN's
  own host and port. A `gp-saml` callback is refused if its cookie or user name holds a line break or other control
  characters.
- **The Mac tunnel service trusts only what you pinned with sudo.** The installer pins the gateway's public key,
  the address ranges and the DNS domains in a root-owned folder. The service refuses a profile with another key,
  endpoint port or address, uses only addresses inside the pinned ranges, writes resolver files only for pinned
  domains and only with DNS servers inside the tunnel. A program running as your user can no longer pull the
  computer's other traffic or other names through the tunnel. Its log is readable by root only.
- **The gateway never asks the local network's DNS.** Its DNS has no upstream, so a stopped VPN's name cannot be
  answered by someone on the local network.
- **Stopped VPN containers are ignored.** The gateway routes to a VPN container, looks its name up and passes it
  the FortiGate callback only while it writes its state file, and only to an address on its own Docker networks.
- **Tighter containers.** VPN containers keep only `NET_ADMIN` and `NET_RAW`, run with `no-new-privileges`, and drop
  connections from the VPN side to the container itself (IPv4 and IPv6). `curl` is no longer in the images.
- **Stricter input.** `host` must be a host name or IPv4 address with an optional port. Subnets must be IPv4, at
  most a `/8`, and not loopback, link-local, multicast or reserved.
- The sign-in helper's `gp-saml` callback is taken only in the first 5 minutes of a login. The page's
  Content-Security-Policy adds `base-uri 'none'` and `form-action 'self'`, and the page opens only `https://`
  FortiGate links.
- The gateway, the page and `./vpn status` never read a VPN's state file through a symlink.
- **The tunnel installer shows what it pins and asks first.** It lists the gateway key, every address range and
  domain (new ones marked `+`), and the path and SHA-256 of `tunnel-service`, `wg` and `wireguard-go`, then asks
  `[y/N]` (`--yes` skips it). It runs only system programs, even though `sudo` keeps your `PATH`.
- **Sign-ins from the helper wait for your yes.** The page shows `Connect as <user>?` before a `gp-saml` sign-in
  handed over by the sign-in helper starts, and refuses a sign-in for another user than the VPN's `user`. The
  gateway passes the FortiGate port 8020 on only for 5 minutes after **Connect**.
- **Tighter gateway.** The gateway keeps only `NET_ADMIN`, `NET_RAW`, `NET_BIND_SERVICE`, `SETUID` and `SETGID`.
  What the computer writes for it moved to `data/gateway/`, mounted read-only; `data/wireguard/` keeps only what the
  gateway writes.
- The page link stops working after 12 hours without an API request; the terminal then prints a new one.
- Login cookies in XML form (`<...cookie>value</...cookie>`) are hidden in the logs too.
- Domain names follow one rule everywhere: each label starts and ends with a letter or digit.
- Windows: `data\secrets`, `data\wireguard` and `data\gateway` allow only your user (`icacls`). `./vpn login`
  deletes the portal's login form from `data/secrets/` once the login is done.

### Added

- `./vpn rotate-keys`: stop everything and make new WireGuard keys at the next gateway start.
- CI (`.github/workflows/ci.yml`): web page build, Python syntax and shellcheck on every push.
- Security sections in `README.md` and `docs/technical.md`: what VPN Gateway gives you and what it cannot give you.
- Setup flowcharts for Mac and Windows in `docs/technical.md`. `README.md` is now a short summary.
- `docs/adding-a-vpn-client.md`: the security checklist for a new VPN client or login method.
- **Update notice.** At each start, `./vpn web` asks GitHub for the newest release. When it is newer than your
  version, the terminal and the page say so, with a link to what changed. Nothing is updated by itself: you run
  `git pull`. `VPN_GATEWAY_NO_UPDATE_CHECK=1` turns the check off.

### Fixed

- FortiGate SAML: after the sign-in the browser waited about a minute, unless you clicked the "Authenticate at"
  link. A spare connection the browser opened early held openfortivpn's listener. The gateway now passes a
  connection on only once its request arrives.
- Windows: the page stayed on "Loading..." because the registry mapped `.js` to `text/plain`. Web files are now
  served with fixed content types.
- Windows: secret and route files were written with CRLF and the local code page, and a VPN container read
  `password\r`. These files are now written as UTF-8 with LF.
- Windows: the **Local** stop stayed "not checked". It now checks each address's interface.
- `data/` and the compose file are readable by your user only (modes 700 and 600), also after a first start with
  `./vpn web`. Another account on the computer could read hosts and user names. An older folder is corrected at the
  next start.
- A `user` set by hand for a `gp-saml` VPN stays when you save its **Settings**. It was deleted, and with it the check
  that refuses another user's sign-in.
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
- Mac with the automatic tunnel: run `sudo handlers/macos/install-tunnel.sh` again. It moves the service to
  `/Library/PrivilegedHelperTools/vpn-gateway/`, pins the gateway's key and removes
  `/usr/local/libexec/vpn-gateway-tunnel`. Until you do, the new service refuses the profile. From now on, run it
  again after you add a VPN, an address outside the pinned ranges or a DNS domain.
- A subnet wider than `/8`, an IPv6 subnet or a `host` with a path or user name is refused now. `./vpn check` names
  each one.
- The files the computer writes for the gateway move from `data/wireguard/` to `data/gateway/` by themselves. The
  old gateway does not read them there: until a **Connect** (or `./vpn up`) has replaced it, no VPN passes
  traffic.
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
