#!/usr/bin/env python3
r"""Checks of the Windows bundle and of the installer, run by the build server after build.py (and by hand, on Windows):

    python packaging\windows\check.py tests      <work>   every test of the repository, run by the bundle's own Python
    python packaging\windows\check.py installer  <out>    install, run the demo and the installed start, uninstall

"tests" needs BW_DEMO_CSV for the synthetic-mast tests (see .github/workflows/tests.yml) and the internet for pytest.
"installer" works on a copy of the user folder made for the check, so nothing of the person running it is touched,
except that the program is installed and removed again (it refuses to run where Bohemian Wind is already installed).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))
APP_ID = "{6C0E7B5B-9B7E-4C55-A1B7-4E0F5C6F2D31}_is1"


class CheckError(SystemExit):
    def __init__(self, msg: str):
        super().__init__(f"CHECK FAILED: {msg}")


def say(msg: str) -> None:
    print(msg, flush=True)


def run(cmd: list, **kw) -> subprocess.CompletedProcess:
    say("  $ " + " ".join(str(c) for c in cmd)[:300])
    return subprocess.run([str(c) for c in cmd], **kw)  # noqa: S603


# ---------- every test, inside the bundle ----------

def tests(work: Path) -> None:
    python = work / "bundle" / "python" / "python.exe"
    if not python.is_file():
        raise CheckError(f"no bundle in {work}: run 'build.py bundle' first.")
    tools = work / "pytest"
    shutil.rmtree(tools, ignore_errors=True)
    r = run([sys.executable, "-m", "pip", "--disable-pip-version-check", "install", "--quiet", "--target", tools, "pytest"])
    if r.returncode:
        raise CheckError("pytest could not be fetched.")
    # pytest comes from a folder outside the bundle, put last on the path: every package the tests use is the bundle's.
    code = (f"import sys; sys.path.append({str(tools)!r}); import pytest; "
            f"raise SystemExit(pytest.main([{str(REPO / 'tests')!r}, '-q', '-rs', '-p', 'no:cacheprovider', '--rootdir', {str(REPO)!r}]))")
    env = {**os.environ, "BW_TEST_INSTALLED": "1", "MPLBACKEND": "Agg"}
    r = run([python, "-c", code], env=env, cwd=REPO)
    if r.returncode:
        raise CheckError(f"the tests failed inside the bundle (exit {r.returncode}).")
    say("Every test passed inside the bundle.")
    demo = work / "demo-project"
    shutil.rmtree(demo, ignore_errors=True)
    r = run([python, "-m", "bohemian_wind", "demo", demo], cwd=work)
    if r.returncode or not any((demo / "permitting").glob("pack_*/index.html")):
        raise CheckError("the demo project did not run through all six steps inside the bundle.")
    shutil.rmtree(demo, ignore_errors=True)
    say("The demo project ran through all six steps inside the bundle.")


# ---------- the installer ----------

def ping(port: int) -> bool:
    try:
        with LOCAL.open(f"http://127.0.0.1:{port}/ping", timeout=2) as r:
            return json.loads(r.read()).get("app") == "bohemian-wind"
    except (OSError, ValueError):
        return False


def wait_for(what, seconds: float, step: float = 0.5):
    t0 = time.time()
    while time.time() - t0 < seconds:
        got = what()
        if got:
            return got
        time.sleep(step)
    return None


def registered() -> str | None:
    """Where Windows says Bohemian Wind is installed for this user, or None."""
    import winreg  # noqa: PLC0415
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{APP_ID}") as k:
            return str(winreg.QueryValueEx(k, "InstallLocation")[0])
    except OSError:
        return None


def installer(out: Path) -> None:
    exes = sorted(out.glob("BohemianWind-*-Setup.exe"))
    if len(exes) != 1:
        raise CheckError(f"expected one setup program in {out}, found {len(exes)}.")
    exe = exes[0]
    if registered():
        raise CheckError(f"Bohemian Wind is already installed here ({registered()}): this check installs and removes it.")
    scratch = Path(tempfile.mkdtemp(prefix="bw-check-")).resolve()      # the long form: Windows may hand out C:\Users\RUNNER~1\...
    target = scratch / "Bohemian Wind"                 # a folder name with a space, as a user's would have
    person = scratch / "user folder"                   # the application's settings and log go here, not to the real user
    person.mkdir()
    start_menu = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Bohemian Wind.lnk"
    env = {**os.environ, "USERPROFILE": str(person), "HOME": str(person), "BOHEMIAN_WIND_CLOSE_AFTER_S": "2",
           "PYTHONPATH": str(REPO), "PYTHONHOME": str(REPO)}      # hostile on purpose: the installed Python must ignore both
    log = out / "install.log"
    say(f"1. Install {exe.name} ({exe.stat().st_size / 1e6:,.0f} MB) into {target}")
    t0 = time.time()
    r = run([exe, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", "/CURRENTUSER", f"/DIR={target}", f"/LOG={log}"])
    if r.returncode:
        raise CheckError(f"the setup program ended with {r.returncode}; see {log}.")
    python, pythonw = target / "python" / "python.exe", target / "python" / "pythonw.exe"
    for f in (python, pythonw, target / "icon.ico", target / "README.txt", target / "licenses" / "PACKAGES.txt",
              target / "licenses" / "LIBRARIES.txt", target / "licenses" / "Python-LICENSE.txt", start_menu):
        if not f.is_file():
            raise CheckError(f"after the installation {f} is missing.")
    n = sum(1 for f in target.rglob("*") if f.is_file())
    say(f"   installed in {time.time() - t0:.0f} s: {n:,} files; registered at {registered()}")
    if not registered() or Path(registered()).resolve() != target.resolve():
        raise CheckError("Windows does not list the installation where it was made.")
    try:
        say("2. The installed copy looks nowhere but in its own folder")
        code = ("import sys, bohemian_wind, numpy, py_wake, rasterio, tkinter; "
                "print('SEEN', bohemian_wind.__file__, '|', bohemian_wind.__version__, '|', sys.flags.isolated, '|', ';'.join(sys.path))")
        r = run([python, "-c", code], env=env, cwd=REPO, capture_output=True, text=True)
        seen = next((x for x in r.stdout.splitlines() if x.startswith("SEEN ")), "")
        if r.returncode or not seen:
            raise CheckError(f"the installed Python does not start cleanly:\n{r.stdout}\n{r.stderr[-2000:]}")
        where, version, _, path = [x.strip() for x in seen[5:].split("|")]
        inside = target.resolve()
        stray = [p for p in [where, *path.split(";")] if p and inside not in [Path(p).resolve(), *Path(p).resolve().parents]]
        if stray:
            raise CheckError(f"the installed copy looks outside its folder {inside}: {stray}")
        say(f"   Bohemian Wind {version} from {where}")

        say("3. The demo project, all six steps, by the installed copy")
        demo = scratch / "demo project"
        r = run([python, "-m", "bohemian_wind", "demo", demo], env=env, cwd=scratch)
        if r.returncode or not any((demo / "permitting").glob("pack_*/index.html")):
            raise CheckError("the demo project did not run through all six steps in the installed copy.")

        say("4. The installed start, as the Start-menu entry runs it (no console), then a second start, then stop")
        port = 8765
        first = subprocess.Popen([str(pythonw), "-m", "bohemian_wind", "desktop", "--no-window"], env=env, cwd=target)  # noqa: S603
        try:
            state_file = person / ".bohemian-wind" / "app.json"
            if not wait_for(state_file.is_file, 120):
                raise CheckError("the installed start did not come up (no app.json after two minutes).")
            port = int(json.loads(state_file.read_text(encoding="utf-8"))["port"])
            if not wait_for(lambda: ping(port), 30):
                raise CheckError(f"the installed start wrote its state but does not answer on port {port}.")
            with LOCAL.open(f"http://127.0.0.1:{port}/", timeout=10) as page:
                html = page.read().decode("utf-8")
            token = re.search(r'name="bw-token" content="([^"]+)"', html).group(1)
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/state", headers={"X-BW-Token": token, "X-BW-Page": "check"})
            with LOCAL.open(req, timeout=30) as st:
                state = json.loads(st.read())
            if state["app"]["version"] != version:
                raise CheckError(f"the page answers as version {state['app']['version']}, the package is {version}.")
            for asset in ("/static/app.js", "/static/app.css", "/static/img/icon.png", "/static/fonts/lato-latin-400-normal.woff2"):
                with LOCAL.open(f"http://127.0.0.1:{port}{asset}", timeout=10) as a:
                    if a.status != 200 or not a.read(16):
                        raise CheckError(f"{asset} is not served by the installed copy.")
            second = run([pythonw, "-m", "bohemian_wind", "desktop", "--no-window"], env=env, cwd=target, timeout=120)
            if second.returncode or first.poll() is not None:
                raise CheckError("a second start did not leave the first copy running and end by itself.")
            log_text = (person / ".bohemian-wind" / "app.log").read_text(encoding="utf-8")
            if "is running at" not in log_text:
                raise CheckError(f"without a console the messages should be in app.log; it holds:\n{log_text[-800:]}")
            r = run([python, "-m", "bohemian_wind", "desktop", "--quit"], env=env, cwd=target, capture_output=True, text=True, timeout=120)
            if r.returncode or "has stopped" not in r.stdout or first.wait(timeout=60) != 0:
                raise CheckError(f"the running copy did not stop when asked: {r.stdout} {r.stderr[-500:]}")
            say(f"   ran on port {port}, served its page, kept one copy, wrote its log, stopped when asked")
            # and the end by itself when its last window is closed
            third = subprocess.Popen([str(pythonw), "-m", "bohemian_wind", "desktop", "--no-window"], env=env, cwd=target)  # noqa: S603
            try:
                if not wait_for(state_file.is_file, 120):
                    raise CheckError("the installed start did not come up a second time.")
                port = int(json.loads(state_file.read_text(encoding="utf-8"))["port"])
                if not wait_for(lambda: ping(port), 30):
                    raise CheckError(f"the installed start does not answer on port {port} the second time.")
                with LOCAL.open(f"http://127.0.0.1:{port}/", timeout=10) as page:
                    token = re.search(r'name="bw-token" content="([^"]+)"', page.read().decode("utf-8")).group(1)
                for path, body in (("/api/state", None), ("/api/bye", json.dumps({"token": token, "page": "w"}).encode())):
                    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body,
                                                 headers={"X-BW-Token": token, "X-BW-Page": "w", "Content-Type": "application/json"})
                    LOCAL.open(req, timeout=30).close()
                if third.wait(timeout=60) != 0:
                    raise CheckError("the installed start did not end cleanly after its last window was closed.")
                say("   ended by itself after its last window was closed")
            finally:
                if third.poll() is None:
                    third.kill()
        finally:
            if first.poll() is None:
                first.kill()
    finally:
        say("5. Uninstall")
        unins = target / "unins000.exe"
        if unins.is_file():
            run([unins, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"])
            # the uninstaller works from a copy of itself and returns at once: wait until it has finished
            wait_for(lambda: not target.exists() and not registered() and not start_menu.exists(), 180)
    left = [str(f.relative_to(target)) for f in target.rglob("*")] if target.exists() else []
    if left or registered() or start_menu.exists():
        raise CheckError(f"after the removal: {len(left)} files left ({left[:8]}), registered: {registered()}, "
                         f"Start-menu entry still there: {start_menu.exists()}.")
    shutil.rmtree(scratch, ignore_errors=True)
    say("The installer installs, the installed copy works, and the removal leaves nothing behind.")


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2 or argv[0] not in ("tests", "installer"):
        print(__doc__)
        return 2
    if sys.platform != "win32":
        raise CheckError("these checks run on Windows.")
    (tests if argv[0] == "tests" else installer)(Path(argv[1]).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
