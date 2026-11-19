"""VPN Gateway web API: a small local HTTP server over the CLI. Python standard library only.

The routing and logic live in App.handle(method, path, headers, body) -> (status, headers, body), so tests can
call it without sockets. make_server() wraps it in a ThreadingHTTPServer bound to 127.0.0.1.
Every docker call goes through one Docker object, so tests can replace it with a fake.
"""
import argparse
import hashlib
import hmac
import http.client
import importlib.util
import ipaddress
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.machinery import SourceFileLoader
from pathlib import Path


HERE = Path(__file__).resolve().parent

# Load the CLI next to this file as a module, so validate/render/write_secrets/... are reused as is.
_loader = SourceFileLoader("cli", str(HERE / "cli.py"))
cli = importlib.util.module_from_spec(importlib.util.spec_from_loader("cli", _loader))
_loader.exec_module(cli)

DEFAULT_PORT = 8765
MAX_BODY = 64 * 1024
MAX_INPUT = 1024
STATUS_TIMEOUT = 5  # seconds; status, inspect and logs calls
RM_TIMEOUT = 120  # stopping one container before a delete
LONG_TIMEOUT = 1800  # compose up --build can take minutes
TOKEN_HEADER = "x-gateway-token"
HANDLER_TOKEN_HEADER = "x-gateway-handler-token"
STATIC_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
                ".json": "application/json; charset=utf-8"}
HANDLER_FILE = "web-handler.json"  # in data/secrets/: port and handler token for the macOS callback handler
# Present when handlers/macos/install-tunnel.sh runs the tunnel with wg-quick: profile changes then apply by themselves.
TUNNEL_SERVICE = Path("/Library/LaunchDaemons/com.athenayazilim.vpn-gateway.tunnel.plist")
SAML_PAGE_TTL = 300  # seconds a one-time /saml/<nonce> page lives
GATEWAY = cli.GATEWAY
AUTH_RE = re.compile(r"Authenticate at\s+(\S+)")
COMMON_HEADERS = [
    ("Cache-Control", "no-store"),
    ("X-Content-Type-Options", "nosniff"),
    ("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'"),
]
# The SAML page HTML comes from the VPN gateway. On the app's own origin it could read the page token, so the
# sandbox (without allow-same-origin) gives it an opaque origin; it can still auto-submit its form to the IdP.
SAML_PAGE_HEADERS = [
    ("Cache-Control", "no-store"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("Content-Security-Policy", "sandbox allow-scripts allow-forms; default-src 'none'; "
                                "script-src 'unsafe-inline'; form-action https:; frame-ancestors 'none'"),
    ("Content-Type", "text/html; charset=utf-8"),
]


class HttpError(Exception):
    def __init__(self, status, payload):
        super().__init__(status)
        self.status = status
        self.payload = payload


class DockerError(Exception):
    """A docker step failed. The message is ours and never holds user input."""


class Docker:
    """The only place that runs docker. argv is always a list; no shell."""

    def run(self, argv, timeout, env=None, merge_stderr=False):
        """Return (returncode, stdout, stderr). Raises FileNotFoundError without docker, TimeoutExpired on timeout."""
        _check_argv(argv)
        p = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE,
                           timeout=timeout, env=env, text=True, errors="replace")
        return p.returncode, p.stdout or "", p.stderr or ""

    def send_lines(self, container, lines):
        """Write login lines into the container's login pipe (cli.send_lines). Raises DockerError on failure."""
        error = cli.send_lines(container, lines)
        if error:
            raise DockerError(error)


def _check_argv(argv):
    if not isinstance(argv, list) or not argv or argv[0] != "docker" or not all(isinstance(a, str) for a in argv):
        raise ValueError("docker argv must be a list of strings starting with 'docker'")


def parse_dns(text):
    """The DNS a VPN pushed, from its /state/<name>/<name>.dns ("nameserver <ip>" and "domain <name>" lines)."""
    servers, domains = [], []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == "nameserver" and re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", parts[1]):
            servers.append(parts[1])
        elif len(parts) == 2 and parts[0] == "domain" and re.fullmatch(r"[A-Za-z0-9.-]{1,253}", parts[1]):
            domains.append(parts[1].lower())
    return {"servers": servers, "domains": domains} if servers or domains else None


def read_profile():
    try:
        return cli.WG_PROFILE.read_text()
    except (FileNotFoundError, NotADirectoryError):
        return None


def profile_version(text):
    """A short fingerprint, so the page can tell the profile changed. A hash, never the key itself."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def respond(status, payload):
    body = json.dumps(payload).encode("utf-8")
    return status, COMMON_HEADERS + [("Content-Type", "application/json")], body


def not_found_static():
    return 404, COMMON_HEADERS + [("Content-Type", "text/plain; charset=utf-8")], b"not found\n"


def safe_path(path):
    """The request path as logged: no query string, and never a /saml/ nonce."""
    path = str(path or "").split("?", 1)[0]
    return "/saml/-" if path.startswith("/saml/") else path[:200]


def same_token(given, expected):
    return hmac.compare_digest(given.encode("utf-8", "surrogateescape"), expected.encode("utf-8"))


def json_body(method, hdrs, body):
    """Body checks shared by /api/ and /handler/: size cap, JSON Content-Type on POST/PUT, valid JSON."""
    if len(body) > MAX_BODY:
        raise HttpError(413, {"error": "body too large"})
    if method not in ("POST", "PUT"):
        return None
    ctype = hdrs.get("content-type", "").split(";")[0].strip().lower()
    if ctype != "application/json":
        raise HttpError(415, {"error": "Content-Type must be application/json"})
    if not body.strip():
        return {}
    try:
        return json.loads(body.decode("utf-8"))
    except ValueError:
        raise HttpError(400, {"error": "body must be valid JSON"})


def short_error(text, fallback):
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    msg = " | ".join(lines[-3:]) or fallback
    return msg[-400:]


def compose_argv(*args):
    return ["docker", "compose", "-f", str(cli.COMPOSE), *args]


def container_of(name):
    return f"vpn-{name}"


FORTI_LOG = "/run/openfortivpn.log"  # docker/openfortivpn/entrypoint.sh copies openfortivpn's output here
# PIDs of the hub-link.sh loops in a VPN container (docker/common/hub-link.sh).
HUB_LINK_PIDS = ('for p in /proc/[0-9]*; do c=$(tr "\\0" " " < $p/cmdline 2>/dev/null); '
                 'case "$c" in "/bin/sh /usr/local/bin/hub-link.sh "*) echo ${p#/proc/};; esac; done')


def mac_interface(ip):
    """The interface this computer sends `ip` out of ("utun7", "en0"; "if12" on Windows), or None if it cannot tell."""
    if sys.platform == "win32":
        # GetBestInterface takes the IPv4 address in network byte order and returns the interface index.
        import ctypes
        index = ctypes.c_ulong()
        addr = int.from_bytes(ipaddress.IPv4Address(ip).packed, "little")
        try:
            rc = ctypes.windll.iphlpapi.GetBestInterface(ctypes.c_ulong(addr), ctypes.byref(index))
        except (OSError, AttributeError):
            return None
        return f"if{index.value}" if rc == 0 else None
    if sys.platform != "darwin":
        return None
    try:
        out = subprocess.run(["route", "-n", "get", ip], capture_output=True, text=True, timeout=STATUS_TIMEOUT).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"interface:\s*(\S+)", out)
    return m.group(1) if m else None


def gateway_routes(text):
    """Parse the gateway's `ip -4 route show`: network -> next hop IP, or "unreachable"."""
    out = {}
    for line in text.splitlines():
        parts = line.split()
        try:
            if len(parts) >= 2 and parts[0] == "unreachable":
                out[str(ipaddress.ip_network(parts[1]))] = "unreachable"
            elif len(parts) >= 3 and parts[1] == "via":
                out[str(ipaddress.ip_network(parts[0]))] = parts[2]
        except ValueError:
            continue
    return out


def view(s):
    """A system as the API shows it."""
    def text(key):
        value = s.get(key)
        return value if isinstance(value, str) else None
    subnets = s.get("subnets", [])
    return {
        "name": text("name"),
        "method": text("method"),
        "host": text("host"),
        "user": text("user"),
        "subnets": [str(n) for n in subnets] if isinstance(subnets, list) else [],
    }


def methods_view():
    return {k: {"required": list(m["required"]), "asks_password": m["asks_password"], "iface": m["iface"]}
            for k, m in cli.METHODS.items()}


def as_object(payload):
    if not isinstance(payload, dict):
        raise HttpError(400, {"error": "body must be a JSON object"})
    return payload


def check_keys(body, allowed):
    unknown = sorted(str(k) for k in body if k not in allowed)
    if unknown:
        raise HttpError(400, {"error": f"unknown field(s): {unknown}"})


def build_entry(name, body, existing):
    """Merge a create/update body into a systems.json entry. Type checks only; validate() does the rest."""
    errors = []
    method, host = body.get("method"), body.get("host")
    user = body.get("user")
    subnets = body.get("subnets", [])
    if not isinstance(method, str):
        errors.append("'method' must be a string")
    if not isinstance(host, str):
        errors.append("'host' must be a string")
    if user is not None and not isinstance(user, str):
        errors.append("'user' must be a string")
    if not isinstance(subnets, list) or not all(isinstance(n, str) for n in subnets):
        errors.append("'subnets' must be a list of strings")
    if errors:
        raise HttpError(400, {"errors": errors})
    entry = dict(existing) if existing else {}
    entry["name"] = name
    entry["method"] = method
    entry["host"] = host.strip()
    if user and user.strip():
        entry["user"] = user.strip()
    else:
        entry.pop("user", None)
    entry.pop("password", None)  # older versions stored it; it is asked at every login now
    entry["subnets"] = [n.strip() for n in subnets]
    return entry


def forti_auth_url(log_text):
    """The last 'Authenticate at <url>' URL in openfortivpn's output, only if it is http(s)."""
    url = None
    for line in log_text.splitlines():
        m = AUTH_RE.search(line)
        if m:
            url = m.group(1).strip("'\"")
    if url and (url.startswith("http://") or url.startswith("https://")):
        return url
    return None


class App:
    def __init__(self, port, token, docker=None, handler_token=None):
        self.token = token
        self.handler_token = handler_token or secrets.token_urlsafe(32)  # only for /handler/callback
        self.docker = docker or Docker()
        self.save_lock = threading.Lock()
        self.state_lock = threading.Lock()
        self.ops = {}  # target -> {"busy": None|"starting"|"stopping", "error": None|str}
        self.awaiting = set()  # gp-saml systems waiting for the callback URL
        self.saml_pages = {}  # nonce -> (html bytes, expiry, system name); memory only, one use
        self.clock = time.monotonic
        self.threads = []
        self.set_port(port)

    def set_port(self, port):
        self.port = port
        self.hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        self.origins = {f"http://{h}" for h in self.hosts}

    # ---- request entry point ----

    def handle(self, method, path, headers, body):
        try:
            return self._handle(method, path, headers, body or b"")
        except HttpError as e:
            return respond(e.status, e.payload)
        except Exception as e:  # never leak details; never log request data
            sys.stderr.write(f"internal error: {type(e).__name__}\n")
            return respond(500, {"error": "internal error"})

    def _handle(self, method, path, headers, body):
        hdrs = {}
        for key, value in (headers.items() if hasattr(headers, "items") else headers):
            key = key.lower()
            if key in hdrs and key in ("host", "origin", TOKEN_HEADER, HANDLER_TOKEN_HEADER, "content-type"):
                raise HttpError(403, {"error": f"duplicate {key} header"})
            hdrs[key] = value
        if hdrs.get("host") not in self.hosts:
            raise HttpError(403, {"error": "forbidden host"})
        if "origin" in hdrs and hdrs["origin"] not in self.origins:
            raise HttpError(403, {"error": "forbidden origin"})
        route, _, query = path.partition("?")
        if route == "/api" or route.startswith("/api/"):
            if not same_token(hdrs.get(TOKEN_HEADER, ""), self.token):
                raise HttpError(403, {"error": "missing or wrong token"})
            return self.api(method, route, query, json_body(method, hdrs, body))
        if route == "/handler/callback":
            if not same_token(hdrs.get(HANDLER_TOKEN_HEADER, ""), self.handler_token):
                raise HttpError(403, {"error": "missing or wrong token"})
            if method != "POST":
                return respond(405, {"error": "method not allowed"})
            return self.handler_callback(json_body(method, hdrs, body))
        if method != "GET":
            return respond(405, {"error": "method not allowed"})
        if route.startswith("/saml/"):  # a popup cannot send the token; the one-time nonce takes its place
            return self.serve_saml_page(route[len("/saml/"):])
        return self.static(route)

    def api(self, method, route, query, payload):
        parts = route.split("/")[2:]
        routes = None
        if parts == ["systems"]:
            routes = {"GET": self.list_systems, "POST": lambda: self.create(payload)}
        elif parts == ["status"]:
            routes = {"GET": self.status}
        elif parts == ["stop-all"]:
            routes = {"POST": self.stop_all}
        elif parts == ["wireguard", "profile"]:
            routes = {"GET": self.wireguard_profile}
        elif parts == ["routes"]:
            routes = {"GET": self.routes}
        elif len(parts) == 2 and parts[0] == "gateway":
            routes = {"start": {"POST": self.gateway_start},
                      "stop": {"POST": self.gateway_stop},
                      "logs": {"GET": lambda: self.logs(GATEWAY, query)}}.get(parts[1])
        elif len(parts) == 2 and parts[0] == "systems":
            name = parts[1]
            routes = {"PUT": lambda: self.update(name, payload), "DELETE": lambda: self.delete(name)}
        elif len(parts) == 3 and parts[0] == "systems":
            name = parts[1]
            routes = {"start": {"POST": lambda: self.start(name, payload)},
                      "stop": {"POST": lambda: self.stop(name)},
                      "callback": {"POST": lambda: self.callback(name, payload)},
                      "input": {"POST": lambda: self.send_input(name, payload)},
                      "logs": {"GET": lambda: self.logs_system(name, query)}}.get(parts[2])
        if routes is None:
            return respond(404, {"error": "not found"})
        if method not in routes:
            return respond(405, {"error": "method not allowed"})
        return routes[method]()

    # ---- systems.json ----

    def read_doc(self):
        """Return (data, file_error). A missing file is an empty list; nothing is cached."""
        name = cli.SYSTEMS.name
        try:
            text = cli.SYSTEMS.read_text()
        except FileNotFoundError:
            return {"systems": []}, None
        except (OSError, UnicodeDecodeError) as e:
            return {"systems": []}, f"{name}: cannot read: {type(e).__name__}"
        try:
            data = json.loads(text)
        except ValueError as e:
            return {"systems": []}, f"{name}: invalid JSON: {e}"
        errors = cli.validate(data)
        if errors:
            return data, f"{name} errors: " + "; ".join(errors)
        return data, None

    def shown_systems(self, data):
        systems = data.get("systems") if isinstance(data, dict) else None
        return [s for s in systems if isinstance(s, dict)] if isinstance(systems, list) else []

    def doc_for_write(self):
        data, err = self.read_doc()
        if err:
            raise HttpError(400, {"error": f"fix {cli.SYSTEMS.name} by hand first; {err}"})
        return data

    def valid_systems(self):
        data, err = self.read_doc()
        if err:
            raise HttpError(400, {"error": err})
        return {s["name"]: s for s in data["systems"]}

    def require_system(self, name):
        systems = self.valid_systems()
        if name not in systems:
            raise HttpError(404, {"error": "unknown system"})
        return systems[name]

    def regenerate(self):
        """Write secrets and the compose file exactly as the CLI does on every run."""
        with self.save_lock:
            try:
                return cli.load()
            except SystemExit as e:
                raise HttpError(400, {"error": str(e.code)})

    def save(self, data):
        """Validate, write systems.json atomically with mode 600, then secrets and compose. Caller holds save_lock."""
        errors = cli.validate(data)
        if errors:
            raise HttpError(400, {"errors": errors})
        path = cli.SYSTEMS
        path.parent.mkdir(mode=0o700, exist_ok=True)  # data/ on a fresh checkout
        fd, tmp = tempfile.mkstemp(prefix=".systems.", suffix=".tmp", dir=str(path.parent))
        try:
            if hasattr(os, "fchmod"):  # not on Windows
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(json.dumps(data, indent=2) + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, str(path))
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        os.chmod(str(path), 0o600)
        cli.write_secrets(data)
        cli.COMPOSE.write_text(json.dumps(cli.render(data), indent=2) + "\n")

    def list_systems(self):
        data, err = self.read_doc()
        return respond(200, {"systems": [view(s) for s in self.shown_systems(data)],
                             "methods": methods_view(), "file_error": err})

    def create(self, payload):
        body = as_object(payload)
        check_keys(body, {"name", "method", "host", "user", "subnets"})
        with self.save_lock:
            data = self.doc_for_write()
            entry = build_entry(body.get("name"), body, None)
            self.save({**data, "systems": list(data["systems"]) + [entry]})
        return respond(201, {"system": view(entry)})

    def update(self, name, payload):
        body = as_object(payload)
        with self.save_lock:
            data = self.doc_for_write()
            systems = list(data["systems"])
            idx = next((i for i, s in enumerate(systems) if s["name"] == name), None)
            if idx is None:
                raise HttpError(404, {"error": "unknown system"})
            check_keys(body, {"name", "method", "host", "user", "subnets"})
            if "name" in body and body["name"] != name:
                raise HttpError(400, {"error": "a system cannot be renamed; delete it and add a new one"})
            entry = build_entry(name, body, systems[idx])
            subnets_changed = entry.get("subnets", []) != systems[idx].get("subnets", [])
            systems[idx] = entry
            self.save({**data, "systems": systems})
        note = self.apply_subnets(entry) if subnets_changed else None
        return respond(200, {"system": view(entry), **({"note": note} if note else {})})

    def apply_subnets(self, entry):
        """Give a running VPN container its new subnets now, without ending its VPN session.

        The container's hub-link.sh loop is started again with the new VPN_SUBNETS, then the old loop is stopped.
        Returns None, or a note for the page when it could not be done (the change then applies on the next connect).
        """
        container = container_of(entry["name"])
        later = "Saved. Disconnect and connect this VPN to use the new addresses."
        try:
            rc, out, _ = self.docker.run(["docker", "inspect", "-f", "{{.State.Status}}", container],
                                         timeout=STATUS_TIMEOUT)
            if rc != 0 or out.strip() != "running":
                return None  # not running: it reads the new subnets when it starts
            rc, out, _ = self.docker.run(["docker", "exec", container, "sh", "-c", HUB_LINK_PIDS], timeout=STATUS_TIMEOUT)
            old = [p for p in out.split() if p.isdigit()]
            argv = ["docker", "exec", "-d", "-e", "VPN_SUBNETS=" + " ".join(cli.subnets_env(entry))]
            if entry["method"] == "forti-saml":
                argv += ["-e", f"VPN_LOG={FORTI_LOG}"]
            argv += [container, "hub-link.sh", cli.METHODS[entry["method"]]["iface"]]
            rc, _, _ = self.docker.run(argv, timeout=STATUS_TIMEOUT)
            if rc != 0:
                return later
            if old:  # the openconnect image has no kill binary, so use the shell's
                self.docker.run(["docker", "exec", container, "sh", "-c", "kill " + " ".join(old)], timeout=STATUS_TIMEOUT)
            return None
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return later

    def routes(self):
        """Each subnet of each system, and whether the Mac, the gateway and the VPN container use it."""
        systems = self.valid_systems()
        docker_ok = [True]

        def run(argv):
            if not docker_ok[0]:
                return None
            try:
                return self.docker.run(argv, timeout=STATUS_TIMEOUT)
            except FileNotFoundError:
                docker_ok[0] = False
            except subprocess.TimeoutExpired:
                pass
            return None

        gw = container_of(GATEWAY)
        r = run(["docker", "exec", gw, "ip", "-4", "route", "show"])
        table = gateway_routes(r[1]) if r and r[0] == 0 else {}
        tunnel = mac_interface("10.200.0.1")
        out = []
        for name, s in systems.items():
            r = run(["docker", "inspect", "-f", "{{.State.Status}}", container_of(name)])
            running = bool(r) and r[0] == 0 and r[1].strip() == "running"
            hop = published = None
            if running:
                r = run(["docker", "exec", gw, "getent", "hosts", name])
                hop = r[1].split()[0] if r and r[0] == 0 and r[1].split() else None
                r = run(["docker", "exec", gw, "cat", f"/state/{name}/{name}.routes"])
                published = set(r[1].split()) if r and r[0] == 0 else set()
            entries = []
            for entry in s.get("subnets", []):
                if entry == "TODO":
                    continue
                real, shown = cli.parse_subnet(entry)
                via = table.get(str(shown))
                probe = str(shown.network_address if shown.prefixlen >= 31 else next(shown.hosts()))
                iface = mac_interface(probe) if tunnel else None
                entries.append({
                    "entry": entry, "real": str(real), "shown": str(shown),
                    "mac": None if not tunnel else ("tunnel" if iface == tunnel else "other"),
                    "mac_interface": iface,
                    "gateway": ("this" if via == hop and hop else "blocked" if via == "unreachable"
                                else "missing" if via is None else "other"),
                    "vpn": "off" if not running else ("yes" if str(shown) in published else "no"),
                })
            out.append({"name": name, "method": s.get("method"), "running": running, "entries": entries})
        return respond(200, {"systems": out, "docker": docker_ok[0], "mac_checked": tunnel is not None})

    def delete(self, name):
        with self.save_lock:
            data = self.doc_for_write()
            if name not in {s["name"] for s in data["systems"]}:
                raise HttpError(404, {"error": "unknown system"})
            new = {**data, "systems": [s for s in data["systems"] if s["name"] != name]}
            errors = cli.validate(new)
            if errors:
                raise HttpError(400, {"errors": errors})
            self.claim([name], "stopping")
            try:
                # Same as `./vpn down <name>`; after the save the service is gone from the compose file.
                self.docker.run(compose_argv("rm", "-sf", name), timeout=RM_TIMEOUT)
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass  # no docker or no container: carry on
            finally:
                with self.state_lock:
                    self.ops.pop(name, None)
                    self.awaiting.discard(name)
                    self._drop_pages(name)
            self.save(new)
        return respond(200, {"ok": True})

    # ---- background operations ----

    def claim(self, targets, label):
        with self.state_lock:
            if any(self.ops.get(t, {}).get("busy") for t in targets):
                raise HttpError(409, {"error": "an operation is already running for this target"})
            for t in targets:
                self.ops[t] = {"busy": label, "error": None}

    def release(self, targets, error):
        with self.state_lock:
            for t in targets:
                self.ops[t] = {"busy": None, "error": error}
                if error:
                    self.awaiting.discard(t)

    def spawn(self, targets, steps):
        """Run (argv, env) steps or callables in a thread for already claimed targets; stop at the first failure."""
        self.threads = [t for t in self.threads if t.is_alive()]
        thread = threading.Thread(target=self._run_steps, args=(targets, steps), daemon=True)
        self.threads.append(thread)
        thread.start()

    def launch(self, targets, label, steps):
        self.claim(targets, label)
        self.spawn(targets, steps)

    def _run_steps(self, targets, steps):
        error = None
        try:
            for step in steps:
                if callable(step):  # sends a login; its message never holds the login itself
                    step()
                    continue
                argv, env = step
                rc, out, err = self.docker.run(argv, timeout=LONG_TIMEOUT, env=env)
                if rc:
                    error = short_error(err or out, f"docker compose exited with code {rc}")
                    break
        except DockerError as e:
            error = str(e)
        except FileNotFoundError:
            error = "docker not found on PATH"
        except subprocess.TimeoutExpired:
            error = "docker compose timed out"
        except Exception as e:
            error = f"internal error: {type(e).__name__}"
        finally:
            self.release(targets, error)

    def join(self, timeout=5):
        """Wait for background operations (used by tests)."""
        for t in list(self.threads):
            t.join(timeout)

    # ---- actions ----

    # ---- one-time SAML login pages (caller holds state_lock) ----

    def _purge_pages(self):
        now = self.clock()
        for nonce in [n for n, (_, expiry, _) in self.saml_pages.items() if expiry <= now]:
            del self.saml_pages[nonce]

    def _drop_pages(self, *names):
        for nonce in [n for n, (_, _, owner) in self.saml_pages.items() if not names or owner in names]:
            del self.saml_pages[nonce]

    def serve_saml_page(self, nonce):
        with self.state_lock:
            self._purge_pages()
            entry = self.saml_pages.pop(nonce, None)
        if entry is None:
            return not_found_static()
        return 200, list(SAML_PAGE_HEADERS), entry[0]

    def start(self, name, payload=None):
        s = self.require_system(name)
        password = None
        if s["method"] == "gp-password":  # asked at every connect, kept in memory only until it is sent
            body = as_object(payload if payload is not None else {})
            check_keys(body, {"password"})
            password = body.pop("password", None)
            if not isinstance(password, str) or not password or len(password) > MAX_INPUT \
                    or "\n" in password or "\r" in password:
                raise HttpError(400, {"error": f"'password' must be one line of 1 to {MAX_INPUT} characters"})
        systems = self.regenerate()
        if name not in systems:
            raise HttpError(404, {"error": "unknown system"})
        with self.state_lock:
            self._purge_pages()
        if s["method"] == "gp-saml":
            self.claim([name], "starting")
            error = None
            try:
                method, target = cli.saml_page(systems[name]["host"])
                if method != "POST" and not target.startswith("https://"):
                    error = "the gateway sent an unexpected SAML login address"
                    raise HttpError(502, {"error": error})
            except SystemExit as e:
                error = str(e.code)
                raise HttpError(400, {"error": error})
            except (OSError, ValueError, http.client.HTTPException) as e:
                error = f"could not get the SAML login page: {type(e).__name__}"
                raise HttpError(502, {"error": error})
            finally:
                self.release([name], error)
            with self.state_lock:
                self._drop_pages(name)  # a new start replaces this system's earlier page
                if method == "POST":  # an auto-submitting form: served once from memory, never written to disk
                    nonce = secrets.token_urlsafe(32)
                    self.saml_pages[nonce] = (target.encode("utf-8"), self.clock() + SAML_PAGE_TTL, name)
                    popup_url = f"/saml/{nonce}"
                else:
                    popup_url = target
                self.awaiting.add(name)
            return respond(202, {"ok": True, "login": "gp-saml", "popup_url": popup_url})
        if password is None:
            self.launch([name], "starting", [(compose_argv("up", "-d", "--build", GATEWAY), None),
                                             (compose_argv("up", "-d", "--build", name), None)])
            return respond(202, {"ok": True, "login": None})
        lines = [password, password]  # GlobalProtect asks at the portal and again at the gateway
        del password

        def send():
            try:
                self.docker.send_lines(container_of(name), lines)
            finally:
                lines.clear()
        # A fresh container: a new login pipe, no answers left from an earlier login.
        self.launch([name], "starting", [(compose_argv("up", "-d", "--build", GATEWAY), None),
                                         (compose_argv("up", "-d", "--build", "--force-recreate", name), None),
                                         send])
        return respond(202, {"ok": True, "login": None})

    def callback(self, name, payload):
        s = self.require_system(name)
        if s["method"] != "gp-saml":
            raise HttpError(400, {"error": "callback is only for gp-saml systems"})
        body = as_object(payload)
        if not isinstance(body.get("url"), str) or not body["url"].strip():
            raise HttpError(400, {"error": "'url' is required"})
        try:
            user, usergroup, cookie = cli.parse_callback(body.pop("url"))
        except SystemExit as e:  # the CLI's messages hold no part of the URL
            raise HttpError(400, {"error": str(e.code)})
        except Exception:
            raise HttpError(400, {"error": "could not read the callback URL"})
        systems = self.regenerate()
        if name not in systems:
            raise HttpError(404, {"error": "unknown system"})
        self.claim([name], "starting")
        lines = [cookie, cookie]  # memory only: GlobalProtect asks at the portal and again at the gateway
        del cookie

        def send():
            try:
                self.docker.send_lines(container_of(name), lines)
            finally:
                lines.clear()
        var = "SAML_" + name.upper().replace("-", "_")
        env = {**os.environ, f"{var}_USER": user, f"{var}_ARGS": f"--usergroup={usergroup}"}
        with self.state_lock:
            self.awaiting.discard(name)
        # A SAML cookie is new on every login, so that container must start fresh.
        self.spawn([name], [(compose_argv("up", "-d", "--build", GATEWAY), None),
                            (compose_argv("up", "-d", "--build", "--force-recreate", name), env),
                            send])
        return respond(202, {"ok": True})

    def handler_callback(self, payload):
        """The macOS handler's callback: goes to the one gp-saml system that waits for it, never a guess."""
        body = as_object(payload)
        with self.state_lock:
            waiting = sorted(self.awaiting)
        if not waiting:
            raise HttpError(409, {"error": "no gp-saml login is waiting; press Log in / Start in VPN Gateway first"})
        if len(waiting) > 1:
            raise HttpError(409, {"error": "more than one gp-saml login is waiting; "
                                           "paste the link into the right system's card"})
        self.callback(waiting[0], body)
        return respond(202, {"ok": True, "system": waiting[0]})

    # ---- handler file for the callback handler (macOS and Windows) ----

    def handler_file(self):
        return cli.SECRETS_DIR / HANDLER_FILE

    def write_handler_file(self):
        """Write data/secrets/web-handler.json (600) with the port and handler token."""
        cli.SECRETS_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)  # no O_NOFOLLOW on Windows
        fd = os.open(str(self.handler_file()), flags, 0o600)
        try:
            if hasattr(os, "fchmod"):  # not on Windows
                os.fchmod(fd, 0o600)
            os.write(fd, (json.dumps({"port": self.port, "token": self.handler_token}) + "\n").encode("utf-8"))
        finally:
            os.close(fd)

    def remove_handler_file(self):
        """Delete the handler file if it is still ours (a later ./vpn web may have replaced it)."""
        path = self.handler_file()
        try:
            current = json.loads(path.read_text()).get("token")
        except FileNotFoundError:
            return
        except (OSError, ValueError, AttributeError):
            current = self.handler_token  # unreadable or broken: remove it
        if isinstance(current, str) and same_token(current, self.handler_token):
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    def send_input(self, name, payload):
        s = self.require_system(name)
        if s["method"] != "gp-password":
            raise HttpError(400, {"error": "input is only for gp-password systems"})
        body = as_object(payload)
        text = body.get("text")
        if not isinstance(text, str) or len(text) > MAX_INPUT or "\n" in text or "\r" in text:
            raise HttpError(400, {"error": f"'text' must be one line of at most {MAX_INPUT} characters"})
        container = container_of(name)
        try:
            _, out, _ = self.docker.run(["docker", "inspect", "-f", "{{.State.Status}}", container],
                                        timeout=STATUS_TIMEOUT)
            if out.strip() != "running":
                raise HttpError(409, {"error": "the container is not running; start it first"})
            self.docker.send_lines(container, [text])
        except FileNotFoundError:
            raise HttpError(503, {"error": "docker not found on PATH"})
        except subprocess.TimeoutExpired:
            raise HttpError(504, {"error": "docker did not answer in time"})
        except DockerError as e:
            raise HttpError(502, {"error": str(e)})
        finally:
            del text
            body.pop("text", None)
        return respond(200, {"ok": True})

    def stop(self, name):
        self.require_system(name)
        self.regenerate()
        with self.state_lock:
            self.awaiting.discard(name)
            self._drop_pages(name)
        self.launch([name], "stopping", [(compose_argv("rm", "-sf", name), None)])
        return respond(202, {"ok": True})

    def try_regenerate(self):
        try:
            self.regenerate()
        except HttpError:
            pass  # stopping still works with the compose file already on disk

    def gateway_start(self):
        self.regenerate()
        self.launch([GATEWAY], "starting", [(compose_argv("up", "-d", "--build", GATEWAY), None)])
        return respond(202, {"ok": True})

    def gateway_stop(self):
        self.try_regenerate()
        self.launch([GATEWAY], "stopping", [(compose_argv("rm", "-sf", GATEWAY), None)])
        return respond(202, {"ok": True})

    def wireguard_profile(self):
        """The WireGuard profile the gateway wrote, for the page to save as a file. It holds a private key."""
        text = read_profile()
        if text is None:
            return respond(404, {"error": "start the gateway first; it writes the profile"})
        return respond(200, {"filename": "vpn-gateway.conf", "content": text, "version": profile_version(text)})

    def stop_all(self):
        data, err = self.read_doc()
        names = [] if err else [s["name"] for s in data["systems"]]
        self.try_regenerate()
        self.launch([GATEWAY, *names], "stopping", [(compose_argv("down"), None)])
        with self.state_lock:
            self.awaiting.clear()
            self._drop_pages()
        return respond(202, {"ok": True})

    # ---- status and logs ----

    def status(self):
        data, err = self.read_doc()
        systems = {} if err else {s["name"]: s for s in data["systems"]}
        docker_ok = [True]

        def run(argv, merge=False):
            if not docker_ok[0]:
                return None
            try:
                return self.docker.run(argv, timeout=STATUS_TIMEOUT, merge_stderr=merge)
            except FileNotFoundError:
                docker_ok[0] = False
            except subprocess.TimeoutExpired:
                pass
            return None

        def inspect(container):
            r = run(["docker", "inspect", "-f", "{{.State.Status}}", container])
            if r is None:
                return "unknown"
            return r[1].strip() or "not created"

        with self.state_lock:
            ops = {k: dict(v) for k, v in self.ops.items()}
            awaiting = set(self.awaiting)
        gw_container = container_of(GATEWAY)
        gw_state = inspect(gw_container)
        mac = False
        if gw_state == "running":
            r = run(["docker", "exec", gw_container, "wg", "show", "wg0", "latest-handshakes"])
            if r and r[0] == 0:
                seen = r[1].split()[-1:] or ["0"]
                mac = seen[0] != "0"
        gw_op = ops.get(GATEWAY, {})
        profile = read_profile()
        out = {"gateway": {"state": gw_state, "running": gw_state == "running", "mac_connected": mac,
                           "busy": gw_op.get("busy"), "error": gw_op.get("error"),
                           "profile": {"ready": profile is not None,
                                       "version": profile_version(profile) if profile else None,
                                       "auto": TUNNEL_SERVICE.exists()}},
               "systems": {}}
        for name, s in systems.items():
            container = container_of(name)
            iface = cli.METHODS[s["method"]]["iface"]
            state = inspect(container)
            up, routes, login, dns = False, 0, None, None
            if state == "running":
                r = run(["docker", "exec", container, "ip", "link", "show", iface])
                up = bool(r) and r[0] == 0
                if up:
                    r = run(["docker", "exec", gw_container, "cat", f"/state/{name}/{name}.routes"])
                    routes = len(r[1].split()) if r and r[0] == 0 else 0
                    r = run(["docker", "exec", gw_container, "cat", f"/state/{name}/{name}.dns"])
                    dns = parse_dns(r[1]) if r and r[0] == 0 else None
            if s["method"] == "gp-saml" and name in awaiting:
                login = {"kind": "gp-saml", "awaiting_callback": True}
            elif s["method"] == "forti-saml" and state == "running" and not up:
                r = run(["docker", "logs", "--tail", "50", container], merge=True)
                url = forti_auth_url(r[1]) if r else None
                if url:
                    login = {"kind": "forti-saml", "auth_url": url}
                    # The Mac's callback port 8020 serves one FortiGate login at a time (the last one started).
                    r = run(["docker", "exec", gw_container, "sh", "-c", cli.SAML_TARGET_CMD])
                    holder = cli.saml_holder(r[1]) if r and r[0] == 0 else ""
                    if holder and holder != name and re.fullmatch(r"[a-z][a-z0-9-]{0,30}", holder):
                        login["port_used_by"] = holder
            elif s["method"] == "gp-password" and state == "running" and not up:
                login = {"kind": "gp-password", "awaiting_input": True}
            op = ops.get(name, {})
            out["systems"][name] = {"method": s["method"], "state": state, "iface": iface, "tunnel_up": up,
                                    "routes": routes, "busy": op.get("busy"), "error": op.get("error"),
                                    "login": login, "dns": dns}
        out = {"docker": docker_ok[0], **out}
        return respond(200, out)

    def logs_system(self, name, query):
        self.require_system(name)
        return self.logs(name, query)

    def logs(self, name, query):
        values = urllib.parse.parse_qs(query, keep_blank_values=True).get("tail", ["100"])
        if len(values) != 1 or not re.fullmatch(r"-?\d{1,9}", values[0]):
            raise HttpError(400, {"error": "tail must be one integer"})
        tail = min(max(int(values[0]), 1), 500)
        try:
            rc, out, _ = self.docker.run(["docker", "logs", "--tail", str(tail), container_of(name)],
                                         timeout=STATUS_TIMEOUT, merge_stderr=True)
        except FileNotFoundError:
            raise HttpError(503, {"error": "docker not found on PATH"})
        except subprocess.TimeoutExpired:
            raise HttpError(504, {"error": "docker did not answer in time"})
        if rc:
            raise HttpError(502, {"error": short_error(out, f"docker logs exited with code {rc}")})
        return respond(200, {"lines": out.splitlines()})

    # ---- static files ----

    def static(self, route):
        if route in ("/", "/index.html"):
            name = "index.html"
        elif route.startswith("/") and "/" not in route[1:]:
            name = route[1:]
        else:
            return not_found_static()
        web = cli.ROOT / "web"
        if not name or name.startswith("."):
            return not_found_static()
        try:
            if name not in os.listdir(str(web)):
                return not_found_static()
            path = web / name
            if not stat.S_ISREG(os.lstat(str(path)).st_mode):  # no symlinks, no directories
                return not_found_static()
            data = path.read_bytes()
        except OSError:
            return not_found_static()
        # Fixed types: on Windows, mimetypes reads the registry, which often maps .js to text/plain;
        # with nosniff the browser then refuses app.js and the page stays on "Loading…".
        ctype = STATIC_TYPES.get(Path(name).suffix.lower(), "application/octet-stream")
        return 200, COMMON_HEADERS + [("Content-Type", ctype)], data


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = "vpn-gateway"
        sys_version = ""

        def serve(self):
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length < 0 or length > MAX_BODY:
                status, headers, body = respond(413 if length > 0 else 400, {"error": "bad or too large body"})
                self.close_connection = True
            else:
                data = self.rfile.read(length) if length else b""
                status, headers, body = app.handle(self.command, self.path, list(self.headers.items()), data)
            self.send_response(status)
            for key, value in headers:
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = do_PUT = do_DELETE = serve

        def _safe_path(self):
            return safe_path(getattr(self, "path", ""))

        def log_request(self, code="-", size="-"):
            sys.stderr.write(f"{getattr(self, 'command', '-')} {self._safe_path()} {code}\n")

        def log_message(self, format, *args):  # never log query strings, bodies or raw request lines
            sys.stderr.write(f"{getattr(self, 'command', None) or '-'} {self._safe_path()} error\n")

    return Handler


def make_server(app, port):
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    server.daemon_threads = True
    app.set_port(server.server_address[1])
    return server


def main(argv):
    parser = argparse.ArgumentParser(prog="vpn web", description="Local web page for VPN Gateway.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"port on 127.0.0.1 (default {DEFAULT_PORT})")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if cli.forget_secrets():
        print("Removed stored passwords and cookies: VPN Gateway now asks for them at every login.", flush=True)
    app = App(args.port, secrets.token_urlsafe(32))
    server = make_server(app, args.port)
    if not (cli.ROOT / "web" / "index.html").exists():
        print("note: web/index.html does not exist yet; only the API answers")
    try:
        app.write_handler_file()  # after make_server: the real port is known
        # The token travels in the fragment: the browser never sends it to the server, a log or a Referer.
        link = f"http://127.0.0.1:{app.port}/#token={app.token}"
        print(f"VPN Gateway web app (this link is the key; a new one at every start; Ctrl-C stops it):\n{link}",
              flush=True)
        webbrowser.open(link)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        app.remove_handler_file()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
