"""What the application can do before it has loaded: hold its port, show a page that says it is starting, and tell
a second start that a copy is on its way.

Loading the application means loading numpy, pandas and the rest. On a computer that has just been set up this
takes half a minute or more, because the virus scanner reads every new file first. An installed program that shows
nothing for that long looks broken, so the installed start (desktop.py) opens the window at once on the page served
from here, and the application takes the port over when it has loaded (server.serve(server=...)).

Only the standard library is used here: this module must load in a moment.
"""
from __future__ import annotations

import json
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .. import __version__

STATIC = Path(__file__).resolve().parent / "static"
PORTS = 30                          # how many ports are tried, from the one asked for
CSP = ("default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; "
       "img-src 'self' data:; frame-ancestors 'none'")


class Server(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows "reuse address" lets a second program listen on a port that is already taken, so two copies of the
    # application would answer on one port in turn. Switched off there, a taken port is refused and the next is tried.
    allow_reuse_address = not sys.platform.startswith("win")
    failed = ""                     # why the application could not load, for the starting page to show
    log_file = ""                   # where the messages of the installed start are kept, named on that page


class StartingHandler(BaseHTTPRequestHandler):
    """Answers until the application has loaded.

    HTTP/1.0 on purpose: every request comes on a connection of its own, so the first request after the application
    has taken the port over reaches the application and not this class.
    """
    server_version = "BohemianWind"

    def log_message(self, fmt, *args):   # quiet, like the application
        pass

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj: dict, code: int = 200, extra: dict | None = None) -> None:
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8", extra)

    def _local(self) -> bool:
        port = self.server.server_address[1]
        return (self.headers.get("Host") or "").lower() in (f"127.0.0.1:{port}", f"localhost:{port}")

    def do_GET(self):  # noqa: N802
        if not self._local():
            self._json({"error": "This application answers only on this computer."}, 403)
            return
        path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        if path == "/ping":               # a second start, and the starting page itself, ask here
            out = {"app": "bohemian-wind", "version": __version__, "starting": True}
            if self.server.failed:
                out["failed"] = self.server.failed
            self._json(out)
        elif path in ("/", "/index.html"):
            page = (STATIC / "starting.html").read_text(encoding="utf-8")
            page = page.replace("{{VERSION}}", __version__).replace("{{LOG}}", _html(self.server.log_file))
            self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
        elif path in ("/favicon.ico", "/static/img/icon.png"):
            try:
                self._send(200, (STATIC / "img" / "icon.png").read_bytes(), "image/png")
            except OSError:
                self._json({"error": "Not found."}, 404)
        else:
            self._json({"error": "Bohemian Wind is starting. Try again in a moment."}, 503, {"Retry-After": "1"})

    do_HEAD = do_GET  # noqa: N815

    def do_POST(self):  # noqa: N802
        try:                              # what was sent is read before the answer, or the sender sees a broken line
            self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 1 << 20))
        except (ValueError, OSError):
            pass
        self._json({"error": "Bohemian Wind is starting. Try again in a moment."}, 503, {"Retry-After": "1"})


def _html(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


def bind(port: int = 8765, handler=StartingHandler) -> Server:
    """The first free port from `port` on, held from now on. Raises OSError when none of them is free."""
    for p in range(port, port + PORTS):
        try:
            return Server(("127.0.0.1", p), handler)
        except OSError:
            continue
    raise OSError(f"No free port between {port} and {port + PORTS - 1}.")
