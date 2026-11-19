"""The installed start of the application (python -m bohemian_wind desktop): one copy at a time, a log in place
of a console, a window of its own, and an end when the last window is closed.

Run: python -m pytest tests/test_desktop.py -q
"""
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bohemian_wind import __version__  # noqa: E402
from bohemian_wind.app import desktop, early  # noqa: E402
from bohemian_wind.app import server as srv  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def call(port, path, body=None, token=None, page=None):
    """(status, answer) of one request to the application on this computer."""
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-BW-Token"] = token
    if page:
        headers["X-BW-Page"] = page
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers,
                                 data=None if body is None else json.dumps(body).encode())
    try:
        with LOCAL.open(req, timeout=30) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw[:1] in (b"{", b"[") else raw.decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def started(home: Path, port: int, *args) -> subprocess.Popen:
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home), "BOHEMIAN_WIND_CLOSE_AFTER_S": "1"}
    return subprocess.Popen([sys.executable, "-m", "bohemian_wind", "desktop", "--no-window", "--port", str(port), *args],
                            cwd=REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def wait_until_up(proc: subprocess.Popen, port: int, seconds: float = 90.0) -> None:
    """Until the application has loaded: the port answers earlier, with the starting page."""
    t0 = time.time()
    while time.time() - t0 < seconds:
        if proc.poll() is not None:
            raise AssertionError(f"the application ended early ({proc.returncode}): {proc.stdout.read()[-2000:]}")
        try:
            status, answer = call(port, "/ping")
            if status == 200 and not answer.get("starting"):
                return
        except OSError:
            pass
        time.sleep(0.2)
    raise AssertionError("the application did not come up")


def token_of(port: int) -> str:
    status, page = call(port, "/")
    assert status == 200
    return re.search(r'name="bw-token" content="([^"]+)"', page).group(1)


def test_one_copy_at_a_time_and_an_end_when_the_last_window_closes(tmp_path):
    port = free_port()
    first = started(tmp_path, port)
    try:
        wait_until_up(first, port)
        state_file = tmp_path / ".bohemian-wind" / "app.json"
        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert state["port"] == port and state["pid"] == first.pid and state["version"] == __version__

        # a second start does not start a second copy: it finds the first and ends
        second = started(tmp_path, free_port())
        assert second.wait(timeout=90) == 0, second.stdout.read()
        assert first.poll() is None and json.loads(state_file.read_text(encoding="utf-8"))["port"] == port

        # a window loads the page and works; nobody else can say goodbye for it
        token = token_of(port)
        assert call(port, "/api/state", token=token, page="window-1")[0] == 200
        assert call(port, "/api/bye", {"token": "not the token", "page": "window-1"})[0] == 403
        time.sleep(2.5)
        assert first.poll() is None, "a goodbye without the token ended the application"

        # a reload: the window says goodbye and is back before the application has stopped
        assert call(port, "/api/bye", {"token": token, "page": "window-1"}) == (200, {"ok": True})
        assert call(port, "/api/state", token=token, page="window-2")[0] == 200
        time.sleep(2.5)
        assert first.poll() is None, "the application stopped although a window was open again"

        # the last window closes: the application ends by itself and tidies up
        assert call(port, "/api/bye", {"token": token, "page": "window-2"})[0] == 200
        assert first.wait(timeout=60) == 0
        assert not state_file.exists()
        assert "its last window was closed" in first.stdout.read()
    finally:
        if first.poll() is None:
            first.kill()


def test_quit_stops_the_running_copy_and_is_harmless_when_none_runs(tmp_path):
    port = free_port()
    first = started(tmp_path, port)
    env = {**os.environ, "HOME": str(tmp_path), "USERPROFILE": str(tmp_path)}
    quit_cmd = [sys.executable, "-m", "bohemian_wind", "desktop", "--quit"]
    try:
        wait_until_up(first, port)
        r = subprocess.run(quit_cmd, cwd=REPO, env=env, capture_output=True, text=True, timeout=120)
        assert r.returncode == 0 and "has stopped" in r.stdout, r.stdout + r.stderr
        assert first.wait(timeout=60) == 0
        assert "asked to stop" in first.stdout.read()
    finally:
        if first.poll() is None:
            first.kill()


def test_ping_gives_nothing_away_and_only_the_page_can_stop_the_application():
    server, app, url = srv.serve(free_port(), open_browser=False, quiet=True)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert call(port, "/ping") == (200, {"app": "bohemian-wind", "version": __version__})
        assert call(port, "/api/state")[0] == 403
        assert call(port, "/api/quit", {})[0] == 403 and not app.stop.is_set()
        assert app.closed_for() is None                       # no window has loaded the page yet
        assert call(port, "/api/state", token=app.token, page="a")[0] == 200
        assert call(port, "/api/state", token=app.token, page="b")[0] == 200
        app.bye("a")
        assert app.closed_for() is None                       # one window is still open
        app.bye("b")
        assert app.closed_for() is not None and app.closed_for() < 5
        assert call(port, "/api/quit", {}, token=app.token) == (200, {"ok": True}) and app.stop.is_set()
    finally:
        server.shutdown()
        server.server_close()


def test_the_port_is_held_and_a_starting_page_shown_until_the_application_has_loaded():
    """Loading takes seconds, and up to a minute on a PC that has just been set up. The window is opened at once, so
    there must be something for it to show, and a second start must find the copy that is on its way."""
    server = early.bind(free_port())
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        # while it loads: the starting page, a ping that says so, and nothing else
        assert call(port, "/ping") == (200, {"app": "bohemian-wind", "version": __version__, "starting": True})
        status, page = call(port, "/")
        assert status == 200 and "Bohemian Wind is starting" in page and "bw-token" not in page and __version__ in page
        assert "{{" not in page
        assert call(port, "/api/state")[0] == 503 and call(port, "/api/quit", {})[0] == 503
        assert desktop.ping(port) and not desktop.loaded(port)
        req = urllib.request.Request(f"http://127.0.0.1:{port}/", headers={"Host": "example.com"})
        with pytest.raises(urllib.error.HTTPError) as refused:        # another website's name for this computer
            LOCAL.open(req, timeout=10)
        assert refused.value.code == 403

        # a start that fails says why, for the window that is already open
        server.failed = "No project found in C:\\nowhere"
        assert call(port, "/ping")[1]["failed"] == "No project found in C:\\nowhere"
        server.failed = ""

        # the application takes the port over: the very next request reaches it
        same, app, url = srv.serve(open_browser=False, quiet=True, server=server)
        assert same is server and url == f"http://127.0.0.1:{port}/"
        assert call(port, "/ping") == (200, {"app": "bohemian-wind", "version": __version__})
        assert desktop.loaded(port) and token_of(port) == app.token
        assert call(port, "/api/state", token=app.token)[0] == 200
    finally:
        server.shutdown()
        server.server_close()


def test_the_starting_page_needs_nothing_but_itself():
    """It is shown before the application can serve its own files: no style sheet, script or font from elsewhere."""
    page = (early.STATIC / "starting.html").read_text(encoding="utf-8")
    assert not re.search(r'(?:src|href)="(?!/favicon\.ico")[^"]*"', page), "the starting page loads another file"
    assert "{{VERSION}}" in page and "{{LOG}}" in page and "/ping" in page
    assert "<script>alert(1)</script>" not in early._html("<script>alert(1)</script>")


def test_a_copy_that_is_still_starting_is_waited_for_and_then_stopped(tmp_path, monkeypatch):
    """The uninstaller asks the running copy to stop. Asked in the first seconds of a start, it must not conclude
    that nothing is running and go on to remove files that are in use."""
    home = tmp_path / ".bohemian-wind"
    home.mkdir()
    monkeypatch.setattr(desktop, "home", lambda: home)
    server = early.bind(free_port())
    port = server.server_address[1]
    (home / "app.json").write_text(json.dumps({"port": port}), encoding="utf-8")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    box = {}

    def load():
        time.sleep(1.2)
        box["app"] = srv.serve(open_browser=False, quiet=True, server=server)[1]
        box["app"].stop.wait(60)
        server.shutdown()

    threading.Thread(target=load, daemon=True).start()
    try:
        t0 = time.time()
        assert desktop.quit_running() is True
        assert time.time() - t0 >= 1.0 and box["app"].stop.is_set()
    finally:
        server.shutdown()
        server.server_close()


def test_two_copies_never_answer_on_one_port():
    """On Windows a port that is taken can be taken again unless the program refuses: the second copy must move on."""
    port = free_port()
    a = srv.serve(port, open_browser=False, quiet=True)[0]
    try:
        b = srv.serve(port, open_browser=False, quiet=True)[0]
        try:
            assert a.server_address[1] == port and b.server_address[1] != port
        finally:
            b.server_close()
    finally:
        a.server_close()


def test_holding_the_place_of_the_one_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop, "home", lambda: tmp_path / ".bohemian-wind")
    first = desktop.single_copy()
    assert first is not None and desktop.single_copy() is None
    first.close()
    again = desktop.single_copy()
    assert again is not None
    again.close()
    again.close()                                             # closing twice is harmless


def test_the_window_is_its_own_where_edge_or_chrome_exists_and_a_browser_tab_otherwise(tmp_path, monkeypatch):
    opened, launched = [], []
    monkeypatch.setattr(desktop.webbrowser, "open", lambda url: opened.append(url))
    monkeypatch.delenv("BOHEMIAN_WIND_WINDOW", raising=False)
    url = "http://127.0.0.1:8765/"

    monkeypatch.setattr(desktop, "find_browser", lambda: None)
    assert desktop.open_window(url) == "browser" and opened == [url]

    exe = tmp_path / "msedge.exe"
    monkeypatch.setattr(desktop, "find_browser", lambda: exe)
    monkeypatch.setattr(desktop.subprocess, "Popen", lambda args, **kw: launched.append(args))
    assert desktop.open_window(url) == "app" and launched == [[str(exe), f"--app={url}"]] and len(opened) == 1

    monkeypatch.setenv("BOHEMIAN_WIND_WINDOW", "browser")          # the user asks for the default browser
    assert desktop.open_window(url) == "browser" and len(opened) == 2 and len(launched) == 1
    monkeypatch.delenv("BOHEMIAN_WIND_WINDOW")

    def broken(args, **kw):
        raise OSError("blocked by policy")
    monkeypatch.setattr(desktop.subprocess, "Popen", broken)       # the browser cannot be started: fall back
    monkeypatch.setattr(desktop, "say", lambda msg: None)
    assert desktop.open_window(url) == "browser" and len(opened) == 3


def test_messages_go_to_a_file_when_there_is_no_console(tmp_path, monkeypatch):
    home = tmp_path / ".bohemian-wind"
    home.mkdir()
    log = home / "app.log"
    log.write_text("".join(f"old line {i:06d}\n" for i in range(90_000)), encoding="utf-8")        # 1.4 MB
    monkeypatch.setattr(desktop, "home", lambda: home)
    monkeypatch.setattr("faulthandler.enable", lambda *a, **k: None)
    monkeypatch.setattr(sys, "stdout", None)                       # what pythonw.exe gives a program
    monkeypatch.setattr(sys, "stderr", None)
    desktop.keep_a_log()
    try:
        assert sys.stdout is sys.stderr and sys.stdout is not None
        desktop.say("started")
        print("a library complains", file=sys.stderr)
    finally:
        fh = sys.stdout
        monkeypatch.undo()
        fh.close()
    text = log.read_text(encoding="utf-8")
    assert text.endswith("a library complains\n") and "  started\n" in text
    assert log.stat().st_size < 300_000 and text.startswith("old line ")                            # trimmed at a line


@pytest.mark.parametrize("stream", ["a console"])
def test_with_a_console_nothing_is_redirected(stream, monkeypatch):
    before = (sys.stdout, sys.stderr)
    desktop.keep_a_log()
    assert (sys.stdout, sys.stderr) == before


def test_a_folder_given_without_a_command_goes_to_the_data_checks(tmp_path, capsys):
    """How the data checks were started before the application existed: python main.py <folder>."""
    from bohemian_wind.__main__ import main
    empty = tmp_path / "mast"
    empty.mkdir()
    (empty / "notes.txt").write_text("nothing a logger wrote\n", encoding="utf-8")
    assert main([str(empty), "-o", str(tmp_path / "out")]) == 2
    err = capsys.readouterr().err
    assert "No logger files were recognised here" in err and "1 .txt" in err
