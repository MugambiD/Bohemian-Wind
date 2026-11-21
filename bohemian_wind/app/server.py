"""The Bohemian Wind application: a small web server on this computer and the page it serves.

It listens on 127.0.0.1 only. Every request to the programming interface must carry the session token that the
page receives when it loads, and the Host header must be this computer: another website open in the browser can
neither read data from the application nor start a run. Nothing is sent anywhere except the two downloads a
project may ask for (NASA POWER reference data and SRTM terrain tiles).
"""
from __future__ import annotations

import json
import mimetypes
import os
import secrets
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from .. import APP_NOTICE, __version__
from ..project import STEPS, Project, ProjectError, describe
from .early import Server, bind

STATIC = Path(__file__).resolve().parent / "static"
CONFIG = Path.home() / ".bohemian-wind" / "config.json"
TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".json": "application/json; charset=utf-8", ".geojson": "application/geo+json; charset=utf-8",
         ".png": "image/png", ".jpg": "image/jpeg", ".svg": "image/svg+xml", ".ico": "image/x-icon",
         ".woff2": "font/woff2", ".csv": "text/csv; charset=utf-8", ".txt": "text/plain; charset=utf-8",
         ".log": "text/plain; charset=utf-8", ".kml": "application/vnd.google-earth.kml+xml",
         ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
         ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
         ".kmz": "application/vnd.google-earth.kmz", ".pdf": "application/pdf"}
CSP = "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; frame-ancestors 'none'"
PICKER = r'''
import sys, tkinter as tk
from tkinter import filedialog
kind, title, start = sys.argv[1], sys.argv[2], sys.argv[3]
root = tk.Tk(); root.withdraw()
try:
    root.attributes("-topmost", True)
except Exception:
    pass
if kind == "folder":
    p = filedialog.askdirectory(title=title, initialdir=start or None, mustexist=True, parent=root)
else:
    p = filedialog.askopenfilename(title=title, initialdir=start or None, parent=root)
sys.stdout.write(p or "")
'''


def projects_home() -> Path:
    """Where new projects and the demo go unless another folder is chosen."""
    docs = Path.home() / "Documents"
    return (docs if docs.is_dir() else Path.home()) / "Bohemian Wind" / "projects"


class Job:
    def __init__(self, steps: list):
        self.id = secrets.token_hex(6)
        self.steps, self.current, self.status = steps, None, "running"
        self.log, self.error, self.done = [], "", []
        self.started = time.time()
        self.finished = None
        self.halt = threading.Event()                # set by "Stop": a long step (CFD) ends at its next check

    def say(self, msg: str) -> None:
        self.log.append(str(msg))

    def view(self, since: int = 0) -> dict:
        return {"id": self.id, "steps": self.steps, "current": self.current, "status": self.status, "error": self.error,
                "done": self.done, "log": self.log[since:], "log_total": len(self.log),
                "seconds": round((self.finished or time.time()) - self.started, 1)}


class App:
    def __init__(self):
        self.token = secrets.token_urlsafe(24)
        self.project: Project | None = None
        self.jobs: dict = {}
        self.lock = threading.Lock()                 # one piece of work on the project at a time
        self.watch_stop: threading.Event | None = None
        self.watch_log: list = []
        self.pages: dict = {}                        # the windows that are open: page id -> when last heard from
        self.had_page = False
        self.empty_since: float | None = None        # when the last window was closed
        self.stop = threading.Event()                # set to end the application (the installed start watches it)

    # ---------- windows: the installed start stops the application when the last one is closed ----------

    def seen(self, page: str | None) -> None:
        if page:
            self.pages[str(page)[:64]] = time.time()
            self.had_page, self.empty_since = True, None

    def bye(self, page: str | None) -> dict:
        self.pages.pop(str(page or "")[:64], None)
        if not self.pages:
            self.empty_since = time.time()
        return {"ok": True}

    def closed_for(self) -> float | None:
        """Seconds since the last window was closed. None while one is open, and before the first one has loaded."""
        if self.pages or not self.had_page or self.empty_since is None:
            return None
        return time.time() - self.empty_since

    def quit(self) -> dict:
        self.stop.set()
        return {"ok": True}

    # ---------- recent projects ----------

    def recent(self) -> list:
        try:
            items = json.loads(CONFIG.read_text(encoding="utf-8")).get("recent", [])
        except (OSError, json.JSONDecodeError):
            items = []
        return [r for r in items if (Path(r["folder"]) / "project.json").is_file()][:8]

    def remember(self) -> None:
        if not self.project:
            return
        items = [r for r in self.recent() if r["folder"] != str(self.project.folder)]
        items.insert(0, {"folder": str(self.project.folder), "name": self.project.s["name"]})
        try:
            CONFIG.parent.mkdir(parents=True, exist_ok=True)
            CONFIG.write_text(json.dumps({"recent": items[:8]}, indent=1), encoding="utf-8")
        except OSError:
            pass

    # ---------- state ----------

    def state(self) -> dict:
        from ..intake import watch  # noqa: PLC0415
        running = next((j.view(len(j.log)) for j in self.jobs.values() if j.status == "running"), None)
        out = {"app": dict(describe(), projects_home=str(projects_home())), "recent": self.recent(), "project": None,
               "job": running}
        if self.project:
            out["project"] = self.project.state()
            st = watch.load_status(self.project)
            st["watching"] = bool(self.watch_stop and not self.watch_stop.is_set())
            out["project"]["intake"] = st
        return out

    # ---------- projects ----------

    def set_project(self, project: Project | None) -> None:
        self.stop_watch()
        self.project = project
        self.remember()
        if project and project.s["data"].get("watch"):
            self.start_watch()

    def project_action(self, body: dict) -> dict:
        action = body.get("action")
        if self.busy():
            raise ProjectError("A run is in progress. Wait for it to finish.")
        if action == "new":
            folder = (body.get("folder") or "").strip()
            name = (body.get("name") or "").strip()
            if not name:
                raise ProjectError("Give the project a name.")
            if not folder:
                safe = "".join(c if c.isalnum() or c in " -_()" else "_" for c in name).strip() or "project"
                folder = str(projects_home() / safe)
            self.set_project(Project.create(folder, name, body.get("lat"), body.get("lon"), body.get("elevation_m")))
        elif action == "open":
            self.set_project(Project.open((body.get("folder") or "").strip()))
        elif action == "demo":
            from .. import demo  # noqa: PLC0415
            self.set_project(demo.open_or_create(projects_home() / "Demo Ridge"))
        elif action == "close":
            self.set_project(None)
        else:
            raise ProjectError("Unknown action.")
        return self.state()

    def need_project(self) -> Project:
        if not self.project:
            raise ProjectError("Open or create a project first.")
        return self.project

    # ---------- runs ----------

    def halt(self) -> dict:
        """Ask the running step to stop. Only long steps look (the CFD runs); the others finish."""
        job = next((j for j in self.jobs.values() if j.status == "running"), None)
        if job is None:
            raise ProjectError("Nothing is running.")
        if not job.halt.is_set():
            job.halt.set()
            job.say("Stopping at the next check ...")
        return job.view()

    def busy(self) -> bool:
        return any(j.status == "running" for j in self.jobs.values())

    def start(self, steps: list) -> dict:
        project = self.need_project()
        steps = [s for s in steps if s in Project.RUNNERS]       # the six steps, and fetching the buildings for step 4
        if not steps:
            raise ProjectError("Nothing to run.")
        if self.busy():
            raise ProjectError("A run is already in progress. Wait for it to finish.")
        job = Job(steps)
        self.jobs = {job.id: job}                    # keep only the latest

        def work():
            with self.lock:
                project.halt = job.halt.is_set
                for step in steps:
                    job.current = step
                    try:
                        project.run(step, log=job.say)
                        job.done.append(step)
                    except ProjectError as e:
                        job.error, job.status = str(e), "error"
                        break
                    except Exception as e:  # noqa: BLE001  (shown to the user with the details kept in the log)
                        job.say(traceback.format_exc())
                        job.error = f"Something went wrong in this step ({type(e).__name__}: {e}). The details are in the working log."
                        job.status = "error"
                        break
                else:
                    job.status = "done"
                project.halt = None
                job.current, job.finished = None, time.time()

        threading.Thread(target=work, daemon=True).start()
        return job.view()

    # ---------- daily intake ----------

    def start_watch(self) -> None:
        from ..intake import watch  # noqa: PLC0415
        project = self.need_project()
        self.stop_watch()
        stop = threading.Event()
        self.watch_stop = stop
        minutes = float(project.s["data"].get("watch_minutes") or 60)

        def say(msg):
            self.watch_log.append(f"{datetime.now():%H:%M} {msg}")
            del self.watch_log[:-200]

        threading.Thread(target=watch.watch, kwargs={"project": project, "minutes": minutes, "stop": stop, "log": say,
                                                     "lock": self.lock}, daemon=True).start()

    def stop_watch(self) -> None:
        if self.watch_stop:
            self.watch_stop.set()
        self.watch_stop = None

    def set_watch(self, body: dict) -> dict:
        project = self.need_project()
        on = bool(body.get("on"))
        vals = {"watch": on}
        if body.get("minutes") not in (None, ""):
            vals["watch_minutes"] = max(5.0, min(1440.0, float(body["minutes"])))
        if body.get("stale_hours") not in (None, ""):
            vals["stale_hours"] = max(1.0, min(24.0 * 60, float(body["stale_hours"])))
        project.update("data", vals)
        if on:
            if not project.s["data"].get("folder"):
                project.update("data", {"watch": False})
                raise ProjectError("Choose the data folder first.")
            self.start_watch()
        else:
            self.stop_watch()
        return self.state()

    def change(self, fn) -> dict:
        """A settings change: refused while a run or a data check holds the project."""
        if self.busy() or not self.lock.acquire(timeout=0.3):
            raise ProjectError("The project is busy (a run or a data check is in progress). Try again in a moment.")
        try:
            fn(self.need_project())
        finally:
            self.lock.release()
        return self.state()

    # ---------- small services ----------

    def pick(self, body: dict) -> dict:
        kind = "folder" if body.get("kind") == "folder" else "file"
        start = body.get("start") or (str(self.project.folder) if self.project else str(Path.home()))
        try:
            r = subprocess.run([sys.executable, "-c", PICKER, kind, str(body.get("title") or "Choose"), start],  # noqa: S603
                               capture_output=True, text=True, timeout=900)
        except (OSError, subprocess.TimeoutExpired) as e:
            return {"path": None, "error": f"The file window could not be opened ({e}). Type or paste the path instead."}
        if r.returncode != 0:
            return {"path": None, "error": "The file window is not available on this computer. Type or paste the path instead."}
        return {"path": r.stdout.strip() or None}

    def reveal(self, body: dict) -> dict:
        project = self.need_project()
        target = (project.folder / str(body.get("path") or "")).resolve()
        if not target.is_relative_to(project.folder.resolve()) or not target.exists():
            raise ProjectError("That file is not in the project folder.")
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(target))  # noqa: S606
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(target)])  # noqa: S603, S607
        except OSError as e:
            raise ProjectError(f"Could not open {target} ({e}).") from e
        return {"ok": True}


class Handler(BaseHTTPRequestHandler):
    app: App = None            # set by serve()
    port: int = 0
    server_version = "BohemianWind"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):   # quiet: the application has its own working log
        pass

    # ---------- plumbing ----------

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

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, default=str).encode("utf-8"), TYPES[".json"])

    def _fail(self, code: int, message: str) -> None:
        self._json({"error": message}, code)

    def _local(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        return host in (f"127.0.0.1:{self.port}", f"localhost:{self.port}")

    def _authorised(self) -> bool:
        return secrets.compare_digest(self.headers.get("X-BW-Token") or "", self.app.token)

    def _file(self, path: Path, root: Path) -> None:
        try:
            full = path.resolve()
            if not full.is_relative_to(root.resolve()) or not full.is_file():
                raise FileNotFoundError
            body = full.read_bytes()
        except (OSError, ValueError):
            self._fail(404, "Not found.")
            return
        ctype = TYPES.get(full.suffix.lower()) or mimetypes.guess_type(full.name)[0] or "application/octet-stream"
        extra = {}
        if full.suffix.lower() in (".csv", ".xlsx", ".kml", ".geojson"):
            extra["Content-Disposition"] = f'attachment; filename="{full.name}"'
        self._send(200, body, ctype, extra)

    # ---------- routes ----------

    def do_GET(self):  # noqa: N802
        if not self._local():
            self._fail(403, "This application answers only on this computer.")
            return
        url = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(url.path)
        if path in ("/", "/index.html"):
            page = (STATIC / "index.html").read_text(encoding="utf-8").replace("{{TOKEN}}", self.app.token)
            self._send(200, page.replace("{{VERSION}}", __version__).encode("utf-8"), TYPES[".html"])
        elif path.startswith("/static/"):
            self._file(STATIC / path[len("/static/"):], STATIC)
        elif path == "/favicon.ico":
            self._file(STATIC / "img" / "icon.png", STATIC)
        elif path.startswith("/files/"):
            parts = path.split("/", 3)
            if len(parts) < 4 or not secrets.compare_digest(parts[2], self.app.token) or not self.app.project:
                self._fail(403, "Not allowed.")
                return
            self._file(self.app.project.folder / parts[3], self.app.project.folder)
        elif path == "/ping":             # how a second start finds the copy that is running; it says nothing else
            self._json({"app": "bohemian-wind", "version": __version__})
        elif path == "/api/state":
            self._api(lambda: self.app.state())
        elif path == "/api/job":
            q = urllib.parse.parse_qs(url.query)
            job = self.app.jobs.get((q.get("id") or [""])[0])
            self._api(lambda: job.view(int((q.get("since") or ["0"])[0])) if job else {"status": "gone"})
        else:
            self._fail(404, "Not found.")

    do_HEAD = do_GET  # noqa: N815

    def do_POST(self):  # noqa: N802
        if not self._local():
            self._fail(403, "This application answers only on this computer.")
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n).decode("utf-8") or "{}") if n else {}
            if not isinstance(body, dict):
                raise ValueError
        except (ValueError, UnicodeDecodeError):
            self._fail(400, "The request could not be read.")
            return
        app = self.app
        if urllib.parse.urlsplit(self.path).path == "/api/bye":
            # sent by the page as its window closes, when it can set no header: the token travels in the body
            if secrets.compare_digest(str(body.get("token") or ""), app.token):
                self._json(app.bye(body.get("page")))
            else:
                self._fail(403, "Not allowed.")
            return
        routes = {
            "/api/project": lambda: app.project_action(body),
            "/api/settings": lambda: app.change(lambda p: p.update(str(body.get("section")), body.get("values") or {})),
            "/api/attach": lambda: app.change(lambda p: p.attach(str(body.get("kind")), body.get("path") or "")),
            "/api/run": lambda: app.start(body.get("steps") or [body.get("step")]),
            "/api/stop": lambda: app.halt(),
            "/api/watch": lambda: app.set_watch(body),
            "/api/pick": lambda: app.pick(body),
            "/api/reveal": lambda: app.reveal(body),
            "/api/quit": lambda: app.quit(),
        }
        fn = routes.get(urllib.parse.urlsplit(self.path).path)
        if not fn:
            self._fail(404, "Not found.")
            return
        self._api(fn)

    def _api(self, fn) -> None:
        if not self._authorised():
            self._fail(403, "The page is out of date. Reload it.")
            return
        self.app.seen(self.headers.get("X-BW-Page"))
        try:
            self._json(fn())
        except ProjectError as e:
            self._fail(400, str(e))
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._fail(500, f"{type(e).__name__}: {e}")


def serve(port: int = 8765, open_browser: bool = True, project=None, quiet: bool = False, server: Server | None = None):
    """Start the application. Returns (server, app, url); call server.serve_forever() to run it.

    `server`: a port that is held already and shows the starting page (early.bind): the application takes it over,
    and the next request is answered by the application. Without one, the first free port from `port` on is taken.
    """
    app = App()
    if project:
        app.set_project(Project.open(project))
    if server is None:
        try:
            server = bind(port)
        except OSError as e:
            raise SystemExit(f"{e} Close the other copy of Bohemian Wind and start again.") from None
    p = server.server_address[1]
    server.RequestHandlerClass = type("BoundHandler", (Handler,), {"app": app, "port": p})
    url = f"http://127.0.0.1:{p}/"
    if not quiet:
        print(f"Bohemian Wind v{__version__} is running at {url}\n{APP_NOTICE}\nKeep this window open while you work. "
              "Close it (or press Ctrl+C) to stop.", flush=True)
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    return server, app, url
