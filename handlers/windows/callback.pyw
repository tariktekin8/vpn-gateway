"""VPN Gateway Callback for Windows: the handler for globalprotectcallback: links.

Windows starts it as `pythonw callback.pyw "<link>"` (registered by handlers/windows/install.ps1). It forwards the
link to the running VPN Gateway web app (POST /handler/callback on 127.0.0.1) and exits. It never prints, logs or
stores the link or the token. It shows a message box only when something goes wrong.
"""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

SCHEME = "globalprotectcallback:"
HANDLER_FILE = Path(__file__).resolve().parents[2] / "data" / "secrets" / "web-handler.json"
NOT_RUNNING = "VPN Gateway web app is not running. Start vpn web, or paste the link into the VPN Gateway page."
UNREACHABLE = "Could not reach the VPN Gateway web app. Paste the link into the VPN Gateway page instead."


def read_handler_file(path=HANDLER_FILE):
    """Return (port, token) from the file ./vpn web writes, or None."""
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    port, token = obj.get("port"), obj.get("token")
    if isinstance(port, int) and 1 <= port <= 65535 and isinstance(token, str) and token:
        return port, token
    return None


def callback_link(argv):
    """The first argument that is a globalprotectcallback: link, or None."""
    for arg in argv:
        if isinstance(arg, str) and arg.lower().startswith(SCHEME):
            return arg
    return None


def forward(link, handler):
    """POST the link to the web app. Return None on success, else a message for the user."""
    port, token = handler
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/handler/callback",
        data=json.dumps({"url": link}).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Gateway-Handler-Token": token},
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never through a proxy
    try:
        with opener.open(req, timeout=10):
            return None
    except urllib.error.HTTPError as e:
        try:
            error = json.loads(e.read().decode("utf-8")).get("error")  # the server's text never holds the link
        except (ValueError, AttributeError, OSError):
            error = None
        return error if isinstance(error, str) and error else UNREACHABLE
    except (urllib.error.URLError, OSError):
        return UNREACHABLE


def alert(message):
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, "VPN Gateway Callback", 0x40)  # MB_ICONINFORMATION
    except (ImportError, AttributeError, OSError):
        print(message, file=sys.stderr)


def main(argv):
    link = callback_link(argv)
    if not link:
        return 0  # started without a link: nothing to do
    handler = read_handler_file()
    message = forward(link, handler) if handler else NOT_RUNNING
    if message:
        alert(message)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
