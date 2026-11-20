#!/usr/bin/env python3
"""VPN Gateway CLI: reads systems.json, writes docker-compose.generated.json, runs one container per system.

Usage:
  ./vpn check            validate systems.json
  ./vpn up [name...]     start containers (all systems if no name)
  ./vpn login <name>     start one system and show its login step
  ./vpn type <name>      send one more answer (an MFA code) to a system that waits for it
  ./vpn status           container and tunnel state per system
  ./vpn down [name...]   stop containers (all if no name)
  ./vpn web [--port N]   start the web page on 127.0.0.1 (default port 8765)

The gateway container always runs with them: it is the WireGuard server for the Mac and writes the
Mac profile to data/wireguard/mac.conf (import it in the WireGuard app; re-import when subnets change).

A gp-saml system logs in through the Mac browser: login opens the portal's SAML page and you paste
the globalprotectcallback: URL it ends on. A gp-password system asks for the password at every login.

Passwords and SAML cookies are never stored. They go from memory into the container's login pipe
(see send_lines), which holds no data on disk.
"""
import base64
import getpass
import ipaddress
import json
import os
import re
import ssl
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"  # everything VPN Gateway reads or writes at run time; git-ignored
SYSTEMS = DATA / "systems.json"
COMPOSE = DATA / "docker-compose.generated.json"  # JSON is valid YAML, so compose reads it as is
SECRETS_DIR = DATA / "secrets"
WG_PROFILE = DATA / "wireguard" / "mac.conf"  # holds the Mac private key: 600
# Paths inside the compose file are relative to its folder, data/.
GATEWAY = "gateway"
PROJECT = "vpn-gateway"

# Login methods. Each one is an image under docker/, the fields it needs and its tunnel interface.
METHODS = {
    "gp-password": {"image": "openconnect", "required": ["host", "user"], "iface": "tun0", "asks_password": True},
    "forti-saml": {"image": "openfortivpn", "required": ["host"], "iface": "ppp0", "asks_password": False},
    "gp-saml": {"image": "openconnect", "required": ["host"], "iface": "tun0", "asks_password": False},
}
# Secrets never go in systems.json; a password is asked at every login.
SECRET_KEYS = {"password", "pass", "passwd", "secret", "cookie", "token", "key", "private_key"}
# The openconnect container reads its login answers from this pipe (docker/openconnect/entrypoint.sh).
INPUT_FIFO = "/run/vpn/in"
NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,30}$")


MAX_IDLE_MINUTES = 1440
# A DNS domain: labels of letters, digits and "-", at least two labels. Lower case, as systems.json stores it.
DOMAIN_RE = re.compile(r"^(?=.{1,253}$)[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$")


def validate(data):
    """Return a list of error strings; empty means valid."""
    systems = data.get("systems") if isinstance(data, dict) else None
    if not isinstance(systems, list):  # an empty list is fine: then only the gateway runs
        return ["'systems' must be a list"]
    errors, names, nets, domains = [], set(), [], []
    for i, s in enumerate(systems):
        where = f"systems[{i}]"
        if not isinstance(s, dict):
            errors.append(f"{where}: must be an object")
            continue
        name = s.get("name")
        if not isinstance(name, str) or not NAME_RE.match(name):
            errors.append(f"{where}: 'name' must match {NAME_RE.pattern}")
        elif name == GATEWAY:
            errors.append(f"{where}: name {GATEWAY!r} is reserved")
        elif name in names:
            errors.append(f"{where}: duplicate name {name!r}")
        else:
            names.add(name)
            where = f"{where} ({name})"
        secrets = sorted(SECRET_KEYS & {str(k).lower() for k in s})
        if secrets:
            errors.append(f"{where}: remove {secrets}; secrets never go in systems.json")
        method = METHODS.get(s.get("method"))
        if not method:
            errors.append(f"{where}: unknown method {s.get('method')!r}; use one of {sorted(METHODS)}")
            continue
        for field in method["required"]:
            value = s.get(field)
            if not isinstance(value, str) or not value.strip() or value == "TODO":
                errors.append(f"{where}: '{field}' is required")
        for field in ("host", "user"):
            value = s.get(field)
            # A leading "-" would become a VPN client option; "$" is expanded by compose; spaces split arguments.
            if isinstance(value, str) and (value.startswith("-") or re.search(r"[\s$]", value)):
                errors.append(f"{where}: '{field}' must not start with '-' or contain spaces or '$'")
        listed = s.get("domains", [])
        if not isinstance(listed, list) or not all(isinstance(d, str) and DOMAIN_RE.match(d) for d in listed):
            errors.append(f"{where}: 'domains' must be a list of lower-case domain names such as corp.example.com")
        else:
            domains += [(d, name) for d in listed]
        idle = s.get("idle_minutes")
        if idle is not None and (type(idle) is not int or not 1 <= idle <= MAX_IDLE_MINUTES):
            errors.append(f"{where}: 'idle_minutes' must be a whole number from 1 to {MAX_IDLE_MINUTES}")
        subnets = s.get("subnets", [])
        if not isinstance(subnets, list):
            errors.append(f"{where}: 'subnets' must be a list")
            continue
        for net in subnets:
            if net == "TODO":
                continue
            try:
                real, shown = parse_subnet(net)
            except ValueError as e:
                errors.append(f"{where}: bad subnet {net!r}{': ' + str(e) if str(e) else ''}")
                continue
            # A wider range (10.0.0.0/8) is fine: the gateway's own 10.200.0.0/24 is narrower and wins there.
            if shown.version == 4 and shown.subnet_of(WG_NET):
                errors.append(f"{where}: {shown} is inside the WireGuard network {WG_NET}; pick another range")
            nets.append((shown, name))
    for i, (a, a_name) in enumerate(domains):
        for b, b_name in domains[i + 1:]:
            if a == b or a.endswith("." + b) or b.endswith("." + a):
                errors.append(f"domains overlap: {a} ({a_name}) and {b} ({b_name}); give each domain to one VPN")
    for i, (a, a_name) in enumerate(nets):
        for b, b_name in nets[i + 1:]:
            if a.version == b.version and a.overlaps(b):
                # 100.64.0.0/10 (shared address space) is rarely used inside company networks.
                example = f'"{b} as 100.64.0.0/{b.prefixlen}"' if b.prefixlen >= 10 else '"<range> as <free range>"'
                errors.append(f"subnets overlap: {a} ({a_name}) and {b} ({b_name}); reach one of them under a free "
                              f"range of the same size instead, for example {example}")
    return errors


def render(data):
    """Build the compose file as a dict. It holds no password or cookie."""
    services = {}
    for s in data["systems"]:
        method = METHODS[s["method"]]
        env = {"VPN_NAME": s["name"], "VPN_HOST": s["host"], "VPN_SUBNETS": " ".join(subnets_env(s))}
        if "user" in s:
            env["VPN_USER"] = s["user"]
        if s.get("idle_minutes"):
            env["VPN_IDLE_MINUTES"] = str(s["idle_minutes"])  # docker/common/hub-link.sh disconnects when idle
        svc = {
            "build": {"context": "../docker", "dockerfile": f"{method['image']}/Dockerfile"},
            "image": f"vpn-{method['image']}",
            "container_name": f"vpn-{s['name']}",
            "environment": env,
            "cap_add": ["NET_ADMIN"],
            "devices": ["/dev/net/tun"],
            "sysctls": {"net.ipv4.ip_forward": "1"},
            # Own network and own state volume: no other VPN container can reach it or change what it publishes.
            "networks": [f"net-{s['name']}"],
            "volumes": [f"state-{s['name']}:/state"],  # tunnel routes and DNS for the gateway
            "restart": "no",
        }
        if method["image"] == "openconnect":
            # The login pipe lives in memory; the password or cookie written to it never reaches a disk.
            svc["tmpfs"] = ["/run/vpn:mode=0700"]
        if s["method"] == "gp-saml":
            # Set by ./vpn login from the SAML callback; empty otherwise, and the entrypoint stops.
            var = "SAML_" + s["name"].upper().replace("-", "_")
            env["VPN_USER"] = "${" + var + "_USER:-}"
            env["VPN_EXTRA_ARGS"] = "${" + var + "_ARGS:-}"
        if s["method"] == "forti-saml":
            svc["devices"].append("/dev/ppp")  # the SAML callback reaches it through the gateway's port 8020
        services[s["name"]] = svc
    services[GATEWAY] = {
        "build": {"context": "../docker", "dockerfile": "gateway/Dockerfile"},
        "image": "vpn-gateway",
        "container_name": f"vpn-{GATEWAY}",
        "cap_add": ["NET_ADMIN"],
        "sysctls": {"net.ipv4.ip_forward": "1"},
        # Each VPN's state at /state/<name>/, read-only: the folder name, not the file, says which VPN wrote it.
        "volumes": [f"state-{s['name']}:/state/{s['name']}:ro" for s in data["systems"]] + ["./wireguard:/keys"],
        # "default" holds the published ports and the default route; one more network per VPN.
        "networks": {"default": {"gw_priority": 1}, **{f"net-{s['name']}": {} for s in data["systems"]}},
        # 51820: WireGuard. 8020: FortiGate SAML callbacks, passed to the forti-saml system that is logging in.
        "ports": ["127.0.0.1:51820:51820/udp", "127.0.0.1:8020:8020"],
        "restart": "unless-stopped",
    }
    names = [s["name"] for s in data["systems"]]
    return {"name": PROJECT, "services": services,
            "volumes": {f"state-{n}": {} for n in names}, "networks": {f"net-{n}": {} for n in names}}


def write_secrets(data):
    """Prepare data/ for the containers: folders and the gateway's static routes. No password or cookie."""
    SECRETS_DIR.mkdir(mode=0o700, exist_ok=True)
    WG_PROFILE.parent.mkdir(mode=0o700, exist_ok=True)  # gateway keys and Mac profile
    write_static_routes(data)


def forget_secrets():
    """Remove what older versions stored: "password" in systems.json, password and cookie files.

    Returns the number of things removed. systems.json is rewritten in place of the old file (mode 600).
    """
    removed = 0
    for old in [*SECRETS_DIR.glob("*.password"), *SECRETS_DIR.glob("*.cookie")]:
        old.unlink()
        removed += 1
    try:
        data = json.loads(SYSTEMS.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return removed
    systems = data.get("systems") if isinstance(data, dict) else None
    holders = [s for s in systems if isinstance(s, dict) and "password" in s] if isinstance(systems, list) else []
    if holders:
        for s in holders:
            del s["password"]
        tmp = SYSTEMS.with_name(f".systems.{os.getpid()}.tmp")
        write_private(tmp, json.dumps(data, indent=2) + "\n")
        tmp.replace(SYSTEMS)
        removed += len(holders)
    return removed


def send_lines(container, lines, wait=30):
    """Write lines into the container's login pipe. Return None, or an error message without the lines.

    The lines go from memory to `docker exec -i` and into a FIFO in the container's tmpfs: nothing is written
    to a disk, an argument list or the environment. The entrypoint creates the FIFO when the container starts.
    """
    deadline = time.monotonic() + wait
    while True:
        try:
            ready = subprocess.run(["docker", "exec", container, "test", "-p", INPUT_FIFO],
                                   capture_output=True, timeout=10).returncode == 0
        except subprocess.TimeoutExpired:
            ready = False
        if ready:
            break
        if time.monotonic() > deadline:
            return "the VPN container is not waiting for a login; connect again"
        time.sleep(0.5)
    data = "".join(line + "\n" for line in lines).encode("utf-8")
    try:
        p = subprocess.run(["docker", "exec", "-i", container, "sh", "-c", f"cat > {INPUT_FIFO}"],
                           input=data, capture_output=True, timeout=10)
    except subprocess.TimeoutExpired:
        return "docker did not answer in time"
    finally:
        del data
    return None if p.returncode == 0 else "could not send the login to the VPN container"


WG_NET = ipaddress.ip_network("10.200.0.0/24")  # the gateway's WireGuard network (docker/gateway/entrypoint.sh)


def parse_subnet(entry):
    """Return (real, shown) networks for a subnets entry.

    "10.10.0.0/16" is reached as itself. "10.10.0.0/16 as 100.64.10.0/16" is reached under another range of the
    same size: the system's container maps each address one to one (NETMAP), so two VPNs that use the same range
    can both be reached.
    """
    if not isinstance(entry, str):
        raise ValueError("")
    parts = entry.split()
    if subnet_ports(entry):
        parts = parts[:-2]
    if len(parts) == 1:
        real = shown = ipaddress.ip_network(parts[0])
    elif len(parts) == 3 and parts[1] == "as":
        real, shown = ipaddress.ip_network(parts[0]), ipaddress.ip_network(parts[2])
        if real.version != 4 or shown.version != 4 or real.prefixlen != shown.prefixlen:
            raise ValueError("both ranges must be IPv4 and the same size, for example /16 as /16")
    else:
        raise ValueError('write a range, or "<range> as <other range>"')
    return real, shown


def subnet_ports(entry):
    """Ports at the end of a subnets entry ("10.20.0.15 ports 443,3389"); [] means every port.

    The gateway then forwards only TCP and UDP to those ports of that range (docker/gateway/entrypoint.sh).
    """
    parts = entry.split() if isinstance(entry, str) else []
    if "ports" not in parts:
        return []
    if parts.index("ports") != len(parts) - 2:
        raise ValueError('write "ports" last, for example "10.20.0.15 ports 443,3389"')
    ports = set()
    for p in parts[-1].split(","):
        if not p.isdigit() or not 1 <= int(p) <= 65535:
            raise ValueError(f"bad port {p!r}; use numbers from 1 to 65535, separated by commas")
        ports.add(int(p))
    if len(ports) > 15:  # iptables multiport takes at most 15
        raise ValueError("at most 15 ports per address")
    return sorted(ports)


def configured_subnets(system):
    """The ranges the computer reaches for this system (the shown side of any "as" mapping)."""
    return [str(parse_subnet(n)[1]) for n in system.get("subnets", []) if n != "TODO"]


def subnets_env(system):
    """VPN_SUBNETS for the container: "real" or "real>shown" per entry (docker/common/hub-link.sh reads it)."""
    out = []
    for n in system.get("subnets", []):
        if n == "TODO":
            continue
        real, shown = parse_subnet(n)
        out.append(str(real) if real == shown else f"{real}>{shown}")
    return out


# The Mac's FortiGate SAML callback port (8020) goes to the forti-saml login started last. This side picks it, not
# the VPN containers: a container that is taken over could otherwise claim another organization's login.
# data/wireguard/saml-target is /keys/saml-target in the gateway (docker/gateway/saml-forward.sh).
def claim_saml_port(systems, names):
    """Give port 8020 to the last forti-saml system in names, if any."""
    forti = [n for n in names if systems[n]["method"] == "forti-saml"]
    if forti:
        path = WG_PROFILE.with_name("saml-target")
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(f"{forti[-1]}\n".encode("utf-8"))
        tmp.replace(path)


def saml_port_holder():
    """The system that holds port 8020, or ""."""
    try:
        name = WG_PROFILE.with_name("saml-target").read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return name if NAME_RE.fullmatch(name) else ""


def write_static_routes(data):
    """List every subnet from systems.json for the gateway (mounted at /keys/static-routes).

    The Mac profile always holds these, connected or not, so it changes only when systems.json does.
    Systems without subnets still add the routes their VPN pushes while connected.
    """
    nets = sorted({n for s in data["systems"] for n in configured_subnets(s)})
    path = WG_PROFILE.with_name("static-routes")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes("".join(n + "\n" for n in nets).encode("utf-8"))  # LF, read by the Linux gateway
    tmp.replace(path)
    # "subnet name": the gateway routes a subnet only to the VPN it belongs to, whatever another VPN publishes.
    owners = sorted(f"{n} {s['name']}" for s in data["systems"] for n in configured_subnets(s))
    path = WG_PROFILE.with_name("route-owners")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes("".join(line + "\n" for line in owners).encode("utf-8"))
    tmp.replace(path)
    # "domain name": the only domains the computer resolves through that VPN's DNS (docker/gateway/entrypoint.sh).
    owners = sorted(f"{d} {s['name']}" for s in data["systems"] for d in s.get("domains", []))
    path = WG_PROFILE.with_name("dns-owners")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes("".join(line + "\n" for line in owners).encode("utf-8"))
    tmp.replace(path)
    # "subnet port,port": the gateway forwards only to these ports there. Subnets not listed: every port.
    limits = sorted(f"{parse_subnet(n)[1]} {','.join(map(str, subnet_ports(n)))}"
                    for s in data["systems"] for n in s.get("subnets", []) if n != "TODO" and subnet_ports(n))
    path = WG_PROFILE.with_name("port-limits")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes("".join(line + "\n" for line in limits).encode("utf-8"))
    tmp.replace(path)


def write_private(path, text):
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    # Bytes, not write_text: on Windows that writes CRLF and the locale codepage, so a container would
    # read "password\r" and the VPN rejects it.
    path.write_bytes(text.encode("utf-8"))


def parse_callback(url):
    """Return (username, usergroup, cookie) from a globalprotectcallback: URL."""
    payload = url.strip().split("globalprotectcallback:", 1)[-1].strip()
    if "%" in payload:  # a browser or the handler may hand it over percent-encoded; base64 has no '%'
        payload = urllib.parse.unquote(payload)
    if payload.startswith("cas-as="):
        sys.exit("this portal uses CAS callbacks, which VPN Gateway does not handle yet")
    try:
        text = base64.b64decode(payload + "=" * (-len(payload) % 4)).decode("utf-8", "replace")
    except ValueError:
        sys.exit("could not decode the callback URL; copy it whole, starting with globalprotectcallback:")

    def field(tag):
        m = re.search(rf"<{tag}>([^<]+)</{tag}>", text)
        return m.group(1).strip() if m else ""

    user = field("saml-username")
    for tag in ("prelogin-cookie", "portal-userauthcookie"):
        cookie = field(tag)
        if cookie and cookie != "empty":
            if not user:
                sys.exit("callback has a cookie but no saml-username")
            # Anyone can make a browser open a callback link. Each login line goes to openconnect's stdin, so a line
            # break here would answer its next prompts; the user name becomes --user.
            if not re.fullmatch(r"[\x21-\x7e]{1,4096}", cookie):
                sys.exit("the callback's cookie has characters a SAML cookie never has")
            if not re.fullmatch(r"[^\x00-\x1f\x7f]{1,256}", user) or user.startswith("-"):
                sys.exit("the callback's saml-username has characters a user name never has")
            return user, f"gateway:{tag}", cookie
    sys.exit("callback has no prelogin-cookie; the SAML login did not finish")


def saml_page(host):
    """Ask the gateway for its SAML login page. Return (method, target).

    target is a URL for REDIRECT and an auto-submitting HTML form for POST.
    The gateway, not the portal: a SAML cookie works once, and the gateway asks for its own after the portal.
    """
    url = f"https://{host}/ssl-vpn/prelogin.esp?tmp=tmp&clientVer=4100&clientos=Linux&default-browser=1"
    req = urllib.request.Request(url, headers={"User-Agent": "PAN GlobalProtect"})
    with urllib.request.urlopen(req, timeout=20, context=ssl.create_default_context()) as r:
        xml = r.read().decode("utf-8", "replace")
    method = re.search(r"<saml-auth-method>([^<]*)<", xml)
    request = re.search(r"<saml-request>([^<]+)<", xml)
    if not method or not request:
        sys.exit(f"{host} did not ask for SAML login; use method gp-password instead")
    return method.group(1), base64.b64decode(request.group(1)).decode("utf-8", "replace")


def saml_start(host):
    """Open the gateway's SAML login page in the default browser."""
    method, target = saml_page(host)
    if method == "POST":  # target is an auto-submitting HTML form
        page = SECRETS_DIR / "saml-login.html"
        write_private(page, target)
        target = page.as_uri()
    if not webbrowser.open(target):
        print(f"Open this address in your browser: {target}")


def load():
    if not SYSTEMS.exists() and (ROOT / "systems.json").exists():
        sys.exit("VPN Gateway now keeps its files in data/. Move them once, from the project folder:\n"
                 "  mkdir -m 700 data && mv systems.json docker-compose.generated.json data/ && "
                 "mv .secrets data/secrets && mv wireguard data/wireguard")
    DATA.mkdir(mode=0o700, exist_ok=True)
    if forget_secrets():
        print("Removed stored passwords and cookies: VPN Gateway now asks for them at every login.", file=sys.stderr)
    try:
        data = json.loads(SYSTEMS.read_text())
    except FileNotFoundError:
        data = {"systems": []}  # first run: add systems with ./vpn web or by hand
    except json.JSONDecodeError as e:
        sys.exit(f"{SYSTEMS.name}: invalid JSON: {e}")
    errors = validate(data)
    if errors:
        sys.exit("systems.json errors:\n  " + "\n  ".join(errors))
    write_secrets(data)
    COMPOSE.write_text(json.dumps(render(data), indent=2) + "\n")
    return {s["name"]: s for s in data["systems"]}


def old_containers():
    """Containers of this tool from before the project was renamed (compose project "vpnhub")."""
    r = subprocess.run(["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project=vpnhub"],
                       capture_output=True, text=True)
    return r.stdout.split() if r.returncode == 0 else []


def compose(*args, env=None):
    return subprocess.call(["docker", "compose", "-f", str(COMPOSE), *args], env=env)


def pick(systems, names):
    unknown = [n for n in names if n not in systems]
    if unknown:
        sys.exit(f"unknown system(s): {unknown}; defined: {sorted(systems)}")
    return list(names)


def with_gateway(names):
    return [GATEWAY, *names] if names else []  # no names means all services, gateway included


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd, args = argv[0], argv[1:]
    systems = load()  # validates and regenerates the compose file on every run
    if cmd == "check":
        print(f"ok: {len(systems)} system(s), wrote {COMPOSE.name}")
        if not systems:
            print("No systems yet. Add them in the web app (./vpn web) or in data/systems.json.")
        return 0
    if cmd in ("up", "login") and old_containers():
        sys.exit("Containers from the old name (vpnhub-*) still run and hold the ports. Remove them once with:\n"
                 "  docker rm -f $(docker ps -aq --filter label=com.docker.compose.project=vpnhub)\n"
                 "Your VPN sessions end; log in again afterwards.")
    if cmd == "up":
        names = pick(systems, args)
        claim_saml_port(systems, names)
        rc = compose("up", "-d", "--build", *with_gateway(names))
        if not rc:
            print(f"Mac WireGuard profile: {WG_PROFILE.relative_to(ROOT)}")
        return rc
    if cmd == "down":
        names = pick(systems, args)
        return compose("rm", "-sf", *names) if names else compose("down")
    if cmd == "login":
        if len(args) != 1:
            sys.exit("usage: ./vpn login <name>")
        name = pick(systems, args)[0]
        method = systems[name]["method"]
        env, lines = None, None
        if method == "gp-saml":
            saml_start(systems[name]["host"])
            print("The portal login page is open in the browser. Before logging in, open DevTools (Cmd-Opt-I), Console tab.")
            print("After login the page tries to open a 'globalprotectcallback:...' link. Copy that whole link from")
            print("the Console error or the browser's prompt, and paste it here (input is hidden).")
            try:
                user, usergroup, cookie = parse_callback(getpass.getpass("globalprotectcallback URL: "))
            finally:
                # The portal's login form, if saml_start wrote one: used up, so it does not stay on disk.
                (SECRETS_DIR / "saml-login.html").unlink(missing_ok=True)
            var = "SAML_" + name.upper().replace("-", "_")
            env = {**os.environ, f"{var}_USER": user, f"{var}_ARGS": f"--usergroup={usergroup}"}
            lines = [cookie, cookie]  # GlobalProtect asks at the portal and again at the gateway
            print(f"Logged in as {user}; starting the tunnel.")
        elif method == "gp-password":
            password = getpass.getpass(f"Password for {systems[name]['user']}: ")
            if not password or "\n" in password or "\r" in password:
                sys.exit("the password must be one non-empty line")
            lines = [password, password]
            del password
        claim_saml_port(systems, [name])
        rc = compose("up", "-d", "--build", GATEWAY)
        if not rc:
            # A login starts the container fresh: a new login pipe, no answers left from an earlier one.
            rc = compose("up", "-d", "--build", *(["--force-recreate"] if lines else []), name, env=env)
        if rc:
            return rc
        container = f"vpn-{name}"
        if lines:
            error = send_lines(container, lines)
            lines = None
            if error:
                sys.exit(error)
            print(f"Sent. If the log asks for an MFA code, run: ./vpn type {name}")
        else:
            print("Open the 'Authenticate at' URL in the Mac browser.")
        print("Ctrl-C stops following logs; the tunnel stays up.")
        return subprocess.call(["docker", "logs", "-f", container])
    if cmd == "type":
        if len(args) != 1:
            sys.exit("usage: ./vpn type <name>")
        name = pick(systems, args)[0]
        if METHODS[systems[name]["method"]]["image"] != "openconnect":
            sys.exit(f"{name} logs in through the browser; there is nothing to type")
        answer = getpass.getpass("Answer (hidden): ")
        if "\n" in answer or "\r" in answer:
            sys.exit("the answer must be one line")
        error = send_lines(f"vpn-{name}", [answer], wait=5)
        del answer
        if error:
            sys.exit(error)
        print("Sent.")
        return 0
    if cmd == "status":
        gw = subprocess.run(["docker", "exec", f"vpn-{GATEWAY}", "wg", "show", "wg0", "latest-handshakes"],
                            capture_output=True, text=True)
        if gw.returncode:
            print(f"{GATEWAY:<12} {'wireguard':<12} not running")
        else:
            seen = gw.stdout.split()[-1:] or ["0"]
            mac = "Mac connected" if seen[0] != "0" else "Mac not connected yet"
            print(f"{GATEWAY:<12} {'wireguard':<12} running      {mac}")
        for name, s in systems.items():
            container = f"vpn-{name}"
            state = subprocess.run(["docker", "inspect", "-f", "{{.State.Status}}", container],
                                   capture_output=True, text=True).stdout.strip() or "not created"
            tunnel = "-"
            if state == "running":
                iface = METHODS[s["method"]]["iface"]
                up = subprocess.run(["docker", "exec", container, "ip", "link", "show", iface],
                                    capture_output=True).returncode == 0
                tunnel = f"{iface} up" if up else f"{iface} down"
                if up:
                    shared = subprocess.run(["docker", "exec", f"vpn-{GATEWAY}", "cat", f"/state/{name}/{name}.routes"],
                                            capture_output=True, text=True)
                    count = len(shared.stdout.split()) if shared.returncode == 0 else 0
                    tunnel += f", {count} routes to Mac" if count else f", no routes to Mac: run ./vpn login {name}"
            print(f"{name:<12} {s['method']:<12} {state:<12} {tunnel}")
        return 0
    sys.exit(f"unknown command {cmd!r}; see ./vpn --help")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
