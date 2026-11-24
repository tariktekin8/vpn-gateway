# Adding a VPN client

Read this before you add a login method (a new `METHODS` entry in `src/cli.py`) or a new VPN client image under
`docker/`, for example another GlobalProtect or FortiGate flow, Cisco AnyConnect, Pulse or WireGuard. Every rule
below is already in place for `gp-password`, `gp-saml` and `forti-saml`. A new client must keep all of them. Check
each box in the pull request.

## What a VPN container must do

A VPN container runs one VPN client and shares its tunnel with the gateway:

- It runs `docker/common/hub-link.sh <tunnel interface>` in the background. This script adds NAT, drops
  connections from the VPN side, maps `as` ranges and publishes `/state/<name>.routes` and `/state/<name>.dns`.
- It tells `hub-link.sh` where the pushed DNS is. That is either the container's `resolv.conf` written by
  vpnc-script, or a log line that `vpn_dns()` parses. Add a branch to `vpn_dns()` if the client reports DNS
  another way.
- It adds a `METHODS` entry with `image`, `required` fields, `iface` and `asks_password`.

## Security checklist

### Isolation

- [ ] `render()` gives the container only its own network `net-<name>` and its own volume `state-<name>:/state`.
      Do not add a shared network, a shared volume or `network_mode`.
- [ ] The container never needs to reach another VPN container or open a connection to the gateway. The gateway's
      `INPUT` chain drops such connections, and its `FORWARD` chain drops everything except WireGuard to the listed
      subnets.
- [ ] The container writes only under its own `/state/`. The gateway reads it read-only at `/state/<name>/` and
      trusts the folder name, not the file content, to know whose data it is. The gateway, the page and
      `./vpn status` read those files only as regular files, never through a symlink; keep it that way.
- [ ] A subnet the container publishes is used only if `systems.json` lists it for that system
      (`data/gateway/route-owners`). Do not bypass that check.
- [ ] `cap_drop: ALL`, then only `cap_add: NET_ADMIN, NET_RAW`, `security_opt: no-new-privileges:true` and the
      devices the client needs (`/dev/net/tun`, `/dev/ppp`). No `privileged`. If the client needs another
      capability, say why in the pull request.
- [ ] `hub-link.sh` keeps writing `/state/<name>.routes` every 5 seconds. The gateway treats a container whose file
      is older than 15 seconds as stopped and never looks its name up.
- [ ] A sign-in that arrives without the user's action on the page (a helper, a redirect to a local port) never
      starts a tunnel by itself: it is held in memory, shown to the user, and expires after 5 minutes. The one
      exception is FortiGate's own redirect to port 8020, which openfortivpn takes directly; the gateway passes it
      on only for 5 minutes after **Connect**.
- [ ] If the client needs a callback port (like FortiGate SAML on 8020), it goes through the gateway and is
      published on `127.0.0.1` only. The computer picks which VPN gets it (`cli.claim_saml_port` writes
      `data/gateway/saml-target`), never a VPN container; the container only says that its login waits
      (`/state/saml-target`).

### Secrets

- [ ] No password, cookie, token or key is stored: not in `systems.json`, `data/secrets/`, the compose file, an
      environment variable, an argument list, an image or a log. `validate()` refuses `SECRET_KEYS`. Keep that list
      complete if the client has new secret names.
- [ ] Ask for the password at every connect (`asks_password: True`). The page shows a password box, and
      `./vpn login` shows a hidden prompt.
- [ ] Deliver every login line (password, SAML cookie, MFA code) through the FIFO `/run/vpn/in` on a tmpfs
      (`tmpfs: /run/vpn:mode=0700`) with `cli.send_lines()` (`docker exec -i ... cat > /run/vpn/in`). The entrypoint
      creates the FIFO with mode 600 and holds it open read-write, so a later prompt waits for the next line.
- [ ] A connect that uses the pipe always recreates the container (`--force-recreate`), so each login gets a fresh
      pipe.
- [ ] If a SAML page or a cookie passes through the server, it is kept in memory only and served once.
      `/saml/<nonce>` is random, valid for 5 minutes, and served with a sandbox CSP and `no-referrer`.

### Logs

- [ ] The client's stdout and stderr go through `/usr/local/bin/hide-cookies.sh`, with the client still PID 1:
      `exec client ... > >(/usr/local/bin/hide-cookies.sh) 2>&1`. If the client also writes a log file the scripts
      read, put `| tee -a "$VPN_LOG"` after the filter.
- [ ] Log in once for real and scan `docker logs` and any log file without printing values. Count lines that
      match cookie, password, known cookie keys, long tokens, URL queries and `Authorization`, and print only
      masked line shapes. If the client prints a secret that is not `...cookie=value`, extend `hide-cookies.sh` and
      test it with synthetic input in the built image.
- [ ] Check that prompts without a line end (`Password:`, an MFA code) still show at once in the log.
- [ ] Do not run the client in a verbose or debug mode by default. Verbose output often prints cookies or headers.

### DNS

- [ ] Domains the VPN pushes are only suggestions. The Mac uses a domain only after the user approves it for that
      system (`domains`), and a domain cannot belong to two systems.
- [ ] Only DNS servers inside the system's own subnets are used. Never point a pushed domain at a server outside
      them.
- [ ] Do not change the container's DNS handling so that it reaches the computer's default DNS. The client may
      change DNS only inside its own network namespace (for example `--set-dns=0` for openfortivpn).
- [ ] While the VPN is off, its approved domains point at the gateway's dnsmasq (`10.200.0.1`), which answers
      "no such name". A new client must not need a change here. If it does, keep internal names away from the
      normal DNS.

### Routing and limits

- [ ] Only the subnets from `systems.json` go through the tunnel. A subnet with `ports` is limited to those TCP
      and UDP ports by the gateway's `ALLOWED` chain.
- [ ] Nothing from the VPN side can open a connection towards the gateway, the computer or the container itself
      (`hub-link.sh` FORWARD and INPUT rules, IPv4 and IPv6). Only replies pass. A listener in the container (like
      the FortiGate relay on 8021) is then reachable only from the gateway's network.
- [ ] `idle_minutes` works. `hub-link.sh` watches the tunnel's `tx_packets` and stops PID 1. Check that the new
      client exits cleanly on `SIGTERM`.
- [ ] A VPN that pushes a default route changes routing only inside its own container.

### Images

- [ ] The image starts from the pinned `debian:bookworm-slim@sha256:...` digest that the other Dockerfiles use.
- [ ] A client built from source is pinned to a tag and to its commit. The build stops if the tag moved:
      `test "$(git rev-parse HEAD)" = "$COMMIT"`.
- [ ] Install with `--no-install-recommends` and remove `/var/lib/apt/lists/*`. Add only the packages the client
      needs.
- [ ] Scripts get `chmod 755` in the Dockerfile, because a Windows checkout has no executable bit.

### Honesty towards the organization

- [ ] Do not fake a device health report (HIP), a managed device or another client's identity. VPN Gateway does
      not deceive the organization's security controls.
- [ ] Keep the "Before you start" warning in `README.md` and `docs/technical.md` true for the new client.

## Tests and documentation

- [ ] Unit tests for the new method: `render()` output (own network, own volume, no secret, tmpfs if it uses the
      pipe), validation of its required fields, and the login flow through the pipe.
- [ ] The image builds on Mac (amd64 and arm64) and on Windows with Docker Desktop.
- [ ] A real login test, then the log scan above.
- [ ] `docs/technical.md` updated: what to fill in, the login steps, and Security notes if anything differs.
- [ ] Before every commit, scan the diff for real hosts, addresses, names, passwords, cookies, tokens and keys.
      Stop on any hit.
