"""Bohemian Wind as an installed program: no console window, one copy at a time, a window of its own.

    pythonw -m bohemian_wind desktop           start, or show the copy that is already running
    python  -m bohemian_wind desktop --quit    stop the copy that is running (the uninstaller does this)

Started this way the application
  * keeps its messages in ~/.bohemian-wind/app.log, because there is no console window to print them in;
  * opens in a window of its own where the computer has Microsoft Edge or Google Chrome, and in the default
    browser otherwise (BOHEMIAN_WIND_WINDOW=browser asks for the default browser in every case);
  * opens that window at once, on a page that says it is starting, and loads behind it (see early.py);
  * stops by itself a few seconds after its last window is closed, unless a step is still running.

A second start does not start a second copy: it opens a window on the one that is running.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path

from .. import __version__

CLOSE_AFTER_S = float(os.environ.get("BOHEMIAN_WIND_CLOSE_AFTER_S") or 8.0)   # a reload of the page takes a second or two
FAILED_SHOWN_S = 60.0               # how long a start that failed keeps its window able to say why
NO_WINDOW_S = 600.0                 # no window ever loaded the page: nothing is using this copy
LOG_KEEP = 1_000_000                # the log starts again from its last fifth when it passes this size (bytes)
PORTS = range(8765, 8795)
_LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))       # this computer is never reached through a proxy


def home() -> Path:
    return Path.home() / ".bohemian-wind"


def say(msg: str) -> None:
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}", flush=True)


def keep_a_log() -> bool:
    """Started without a console (pythonw), Python has nowhere to print: messages and errors go to a file.
    True when they do."""
    if sys.stdout is not None and sys.stderr is not None:
        return False
    try:
        home().mkdir(parents=True, exist_ok=True)
        log = home() / "app.log"
        if log.is_file() and log.stat().st_size > LOG_KEEP:
            tail = log.read_bytes()[-LOG_KEEP // 5:]
            log.write_bytes(tail[tail.find(b"\n") + 1:])
        fh = open(log, "a", encoding="utf-8", buffering=1)  # noqa: SIM115  (kept open for the life of the program)
    except OSError:
        fh = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    sys.stdout = sys.stderr = fh
    try:
        import faulthandler  # noqa: PLC0415
        faulthandler.enable(fh)
    except (ImportError, OSError, ValueError, AttributeError):
        pass
    return True


# ---------- one copy at a time ----------

class _Held:
    """The hold on being the one copy. It ends with close(), and in any case when the program ends."""

    def __init__(self, release):
        self._release = release

    def close(self) -> None:
        release, self._release = self._release, None
        if release:
            release()


def single_copy():
    """Something to hold for as long as this copy runs, or None when another copy (of this user) holds it already."""
    home().mkdir(parents=True, exist_ok=True)
    if sys.platform.startswith("win"):
        import ctypes  # noqa: PLC0415
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
        k.CreateMutexW.restype = ctypes.c_void_p
        k.CloseHandle.argtypes = (ctypes.c_void_p,)
        name = "Local\\BohemianWind." + hashlib.sha1(str(home()).lower().encode("utf-8")).hexdigest()[:16]  # noqa: S324
        handle = k.CreateMutexW(None, 0, name)
        if not handle:
            return _Held(None)               # the lock itself failed: better two copies than none
        if ctypes.get_last_error() == 183:   # ERROR_ALREADY_EXISTS
            k.CloseHandle(handle)
            return None
        return _Held(lambda: k.CloseHandle(handle))
    import fcntl  # noqa: PLC0415
    fh = open(home() / "app.lock", "w")  # noqa: SIM115  (the lock lasts as long as the file stays open)
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return _Held(fh.close)


def _ping(port: int, timeout: float) -> dict:
    try:
        with _LOCAL.open(f"http://127.0.0.1:{port}/ping", timeout=timeout) as r:
            answer = json.loads(r.read())
        return answer if isinstance(answer, dict) and answer.get("app") == "bohemian-wind" else {}
    except (OSError, ValueError):
        return {}


def ping(port: int, timeout: float = 1.5) -> bool:
    """A copy of the application holds this port: loaded, or still starting."""
    return bool(_ping(port, timeout))


def loaded(port: int, timeout: float = 1.5) -> bool:
    """The copy on this port has finished starting: it shows its own page and takes requests."""
    answer = _ping(port, timeout)
    return bool(answer) and not answer.get("starting")


def running_port() -> int | None:
    """The port of the copy that is running: the one it wrote down, else whichever of its usual ports answers."""
    try:
        port = int(json.loads((home() / "app.json").read_text(encoding="utf-8"))["port"])
        if ping(port):
            return port
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return next((p for p in PORTS if ping(p, 0.4)), None)


def wait_for_running(seconds: float) -> int | None:
    t0 = time.time()
    while True:
        port = running_port()
        if port is not None or time.time() - t0 > seconds:
            return port
        time.sleep(0.5)


def quit_running(wait: float = 15.0, starting: float = 180.0) -> bool:
    """Ask the running copy to stop and wait until it has. True when one was running and is gone.
    A copy that is still starting is waited for first: only the loaded application can be asked to stop."""
    port = running_port()
    if port is None:
        return False
    t0 = time.time()
    while ping(port, 0.5) and not loaded(port, 0.5) and time.time() - t0 < starting:
        time.sleep(0.3)
    try:
        with _LOCAL.open(f"http://127.0.0.1:{port}/", timeout=5) as r:
            token = re.search(r'name="bw-token" content="([^"]+)"', r.read().decode("utf-8", "replace")).group(1)
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/quit", data=b"{}", method="POST",
                                     headers={"X-BW-Token": token, "Content-Type": "application/json"})
        _LOCAL.open(req, timeout=5).close()
    except (OSError, AttributeError):
        return False
    t0 = time.time()
    while ping(port, 0.5):
        if time.time() - t0 > wait:
            return False
        time.sleep(0.3)
    return True


# ---------- the window ----------

def find_browser() -> Path | None:
    """Microsoft Edge or Google Chrome on Windows, the one the user made the default first: both can show a page in
    a window of its own, without tabs or an address bar. None anywhere else."""
    if not sys.platform.startswith("win"):
        return None
    roots = [os.environ.get(k) for k in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA")]
    edge = [Path(r) / "Microsoft" / "Edge" / "Application" / "msedge.exe" for r in roots if r]
    chrome = [Path(r) / "Google" / "Chrome" / "Application" / "chrome.exe" for r in roots if r]
    default = ""
    try:
        import winreg  # noqa: PLC0415
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice") as key:
            default = str(winreg.QueryValueEx(key, "ProgId")[0])
    except (ImportError, OSError):
        pass
    order = chrome + edge if default.lower().startswith("chrome") else edge + chrome
    return next((p for p in order if p.is_file()), None)


def open_window(url: str) -> str:
    """Show the application. Returns "app" for a window of its own, "browser" for a tab in the default browser."""
    exe = None if (os.environ.get("BOHEMIAN_WIND_WINDOW") or "").lower() == "browser" else find_browser()
    if exe is not None:
        try:
            subprocess.Popen([str(exe), f"--app={url}"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,  # noqa: S603
                             stderr=subprocess.DEVNULL)
            return "app"
        except OSError as e:
            say(f"{exe.name} could not be started ({e}); the default browser is used.")
    webbrowser.open(url)
    return "browser"


# ---------- the program ----------

def watch(server, app, started: float) -> None:
    """Ends the program when it is asked to stop, or when its last window has been closed for a few seconds and no
    step is running."""
    while True:
        time.sleep(0.25)
        if app.stop.is_set():
            why = "asked to stop"
            break
        if app.busy():
            continue
        closed = app.closed_for()
        if closed is not None and closed >= CLOSE_AFTER_S:
            why = "its last window was closed"
            break
        if not app.had_page and time.time() - started > NO_WINDOW_S:
            why = "no window ever opened it"
            break
    say(f"Stopping: {why}.")
    server.shutdown()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="bohemian-wind desktop", description=__doc__.split("\n", 1)[0])
    ap.add_argument("--port", type=int, default=PORTS[0])
    ap.add_argument("--project", help="project folder to open")
    ap.add_argument("--no-window", action="store_true", help="start without opening a window")
    ap.add_argument("--quit", action="store_true", help="stop the copy that is running")
    args = ap.parse_args(argv)
    in_file = keep_a_log()
    if args.quit:
        say("Bohemian Wind was running and has stopped." if quit_running() else "Bohemian Wind was not running.")
        return 0

    held = single_copy()
    if held is None:                         # another copy is starting or running: show that one
        port = wait_for_running(20.0)
        if port is not None:
            if not args.no_window:
                open_window(f"http://127.0.0.1:{port}/")
            return 0
        say("Another copy holds the lock but does not answer: starting this one.")

    # The port is taken and the window opened before the application is loaded: loading takes seconds, and the
    # first time after installing up to a minute. Until then the window shows the starting page (early.py).
    from . import early  # noqa: PLC0415
    try:
        server = early.bind(args.port)
    except OSError as e:
        say(f"{e} Close the other copy of Bohemian Wind and start again.")
        return 1
    server.log_file = str(home() / "app.log") if in_file else ""
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    serving = threading.Thread(target=server.serve_forever, name="serving", daemon=True)
    serving.start()
    t0 = time.time()
    if not args.no_window:
        say(f"Window: {open_window(url)}.")
    state = home() / "app.json"
    try:
        try:
            from .server import serve  # noqa: PLC0415  (numpy, pandas and the rest: the slow part)
            _, app, url = serve(open_browser=False, project=args.project, quiet=True, server=server)
        except BaseException as e:  # noqa: BLE001  (whatever it is, the open window must be able to say so)
            if isinstance(e, KeyboardInterrupt):
                raise
            traceback.print_exc()
            server.failed = str(e) or type(e).__name__
            say(f"Bohemian Wind could not start: {server.failed}")
            time.sleep(0 if args.no_window else FAILED_SHOWN_S)
            return 1
        try:
            state.write_text(json.dumps({"port": server.server_address[1], "pid": os.getpid(),
                                         "started": f"{datetime.now():%Y-%m-%d %H:%M:%S}", "version": __version__}), encoding="utf-8")
        except OSError:
            pass
        say(f"Bohemian Wind v{__version__} is running at {url} (Python {sys.version.split()[0]}, {sys.platform}; "
            f"loaded in {time.time() - t0:.1f} s).")
        threading.Thread(target=watch, args=(server, app, time.time()), daemon=True).start()
        while serving.is_alive():
            serving.join(0.5)
    except KeyboardInterrupt:
        say("Stopped.")
    finally:
        server.shutdown()
        server.server_close()
        try:
            state.unlink()
        except OSError:
            pass
        if held is not None:
            held.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
