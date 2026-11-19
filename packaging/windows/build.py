#!/usr/bin/env python3
r"""Builds the Windows installer of Bohemian Wind. Run on 64-bit Windows with the Python that is to be shipped:

    python packaging\windows\build.py bundle     [--work build\windows] [--commit SHA]
    python packaging\windows\build.py installer  [--work build\windows] [--out dist]

"bundle" makes <work>\bundle, the folder the installer copies to the user's computer:

    python\                      this Python (python.org's build, with tcl/tk), told to see nothing outside the bundle
    python\Lib\site-packages\    the packages pinned in requirements.txt beside this file, and Bohemian Wind itself
    licenses\                    the licence of Bohemian Wind, of Python, of every package and of the libraries in them
    icon.ico, README.txt, BUILD.txt

"installer" turns that folder into <out>\BohemianWind-<version>-Setup.exe with Inno Setup 6 (ISCC.exe). Between the
two the build server runs every test inside the bundle (check.py), so what is tested is what is shipped.

The build stops when the bundle holds a package or a library file that notices.json does not list, when a package
declares a GPL licence that the list does not expect, or when the bundle's Python cannot import everything.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import importlib.metadata as md
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
LEFT_OUT_TOP = {"doc", "scripts", "share", "include", "libs", "tools", "news.txt"}
LEFT_OUT_LIB = {"site-packages", "test", "idlelib", "turtledemo", "ensurepip", "venv"}
MUST_IMPORT = ("tkinter", "_tkinter", "ssl", "sqlite3", "ctypes", "bohemian_wind", "bohemian_wind.app.desktop", "numpy", "scipy",
               "pandas", "matplotlib", "xarray", "py_wake", "h5py", "netCDF4", "rasterio", "openpyxl", "PIL")
LICENCE_FILE = re.compile(r"(?i)(licen[cs]e|copying|notice|authors|copyright)")
NOTICES = json.loads((HERE / "notices.json").read_text(encoding="utf-8"))


class BuildError(SystemExit):
    def __init__(self, msg: str):
        super().__init__(f"BUILD STOPPED: {msg}")


def say(msg: str) -> None:
    print(msg, flush=True)


def run(cmd: list, **kw) -> subprocess.CompletedProcess:
    say("  $ " + " ".join(str(c) for c in cmd)[:400])
    return subprocess.run([str(c) for c in cmd], check=True, **kw)  # noqa: S603


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def about() -> tuple:
    """(version, the notice shown at the foot of the application), read from the package itself."""
    sys.path.insert(0, str(REPO))
    try:
        from bohemian_wind import APP_NOTICE, __version__  # noqa: PLC0415
    finally:
        sys.path.remove(str(REPO))
    return __version__, APP_NOTICE


def app_version() -> str:
    return about()[0]


def numeric_version(version: str) -> str:
    """0.2.0-dev -> 0.2.0.0: the four numbers Windows keeps for a program."""
    nums = [int(n) for n in re.findall(r"\d+", version)[:3]]
    return ".".join(str(n) for n in (nums + [0, 0, 0, 0])[:4])


# ---------- the bundle ----------

def copy_python(dest: Path) -> Path:
    base = Path(sys.base_prefix)
    if not (base / "python.exe").is_file() or not (base / "Lib" / "os.py").is_file():
        raise BuildError(f"{base} is not a Python installed from python.org (no python.exe with Lib beside it).")

    def left_out(folder: str, names: list) -> list:
        rel = Path(folder).resolve().relative_to(base.resolve())
        out = [n for n in names if n == "__pycache__" or n.lower().endswith((".pdb", ".pyc"))]
        if rel == Path("."):
            out += [n for n in names if n.lower() in LEFT_OUT_TOP]
        elif rel == Path("Lib"):
            out += [n for n in names if n.lower() in LEFT_OUT_LIB]
        return out

    shutil.copytree(base, dest, ignore=left_out)
    (dest / "Lib" / "site-packages").mkdir(parents=True)
    for exe in ("python.exe", "pythonw.exe"):
        if not (dest / exe).is_file():
            raise BuildError(f"{exe} is missing from {base}.")
    pth = dest / f"python{sys.version_info.major}{sys.version_info.minor}._pth"
    # With this file Python looks nowhere else: not in PYTHONPATH, not in the registry, not in the user's own packages.
    pth.write_text("Lib\nDLLs\nLib\\site-packages\nimport site\n", encoding="ascii")
    return dest


def install_packages(site: Path, work: Path) -> None:
    pip = [sys.executable, "-m", "pip", "--disable-pip-version-check", "--no-input"]
    run(pip + ["install", "--no-compile", "--only-binary=:all:", "--no-warn-script-location", "--target", site,
               "-r", HERE / "requirements.txt"])
    wheels = work / "wheel"
    shutil.rmtree(wheels, ignore_errors=True)
    run(pip + ["wheel", "--no-deps", "--wheel-dir", wheels, REPO])
    wheel = next(wheels.glob("bohemian_wind-*.whl"))
    run(pip + ["install", "--no-compile", "--no-deps", "--no-warn-script-location", "--target", site, wheel])
    for extra in ("bin", "Scripts", "share"):                # start files that name this build machine's Python
        shutil.rmtree(site / extra, ignore_errors=True)


def probe(python: Path, bundle: Path) -> dict:
    """What the bundle's own Python sees, asked with a hostile environment to show that it looks nowhere else."""
    code = ("import importlib, json, sys\n"
            f"mods = {list(MUST_IMPORT)!r}\n"
            "out = {'path': sys.path, 'isolated': sys.flags.isolated, 'version': sys.version.split()[0], 'modules': {}}\n"
            "for m in mods:\n"
            "    mod = importlib.import_module(m)\n"
            "    out['modules'][m] = [str(getattr(mod, '__version__', '')), str(getattr(mod, '__file__', ''))]\n"
            "import tkinter\n"
            "out['tcl'] = tkinter.Tcl().eval('info patchlevel')      # the Tcl library is found and starts\n"
            "print('PROBE ' + json.dumps(out))\n")
    env = {**os.environ, "PYTHONPATH": str(REPO), "PYTHONHOME": str(REPO), "PYTHONUSERBASE": str(REPO)}
    r = subprocess.run([str(python), "-c", code], capture_output=True, text=True, env=env, cwd=REPO)  # noqa: S603
    line = next((x for x in r.stdout.splitlines() if x.startswith("PROBE ")), None)
    if r.returncode or not line:
        raise BuildError(f"the bundle's Python cannot import everything:\n{r.stdout[-1500:]}\n{r.stderr[-3000:]}")
    out = json.loads(line[6:])
    inside = str(bundle.resolve()).lower()
    stray = [p for p in out["path"] if p and not str(Path(p).resolve()).lower().startswith(inside)]
    if stray:
        raise BuildError(f"the bundle's Python looks outside the bundle: {stray}")
    for m, (_, where) in out["modules"].items():
        if where and not str(Path(where).resolve()).lower().startswith(inside):
            raise BuildError(f"{m} was imported from outside the bundle: {where}")
    if not (bundle / "python" / "tcl").is_dir():
        raise BuildError("the tcl folder is missing: the file windows of the application would not open.")
    return out


def signatures(python_dir: Path) -> list:
    """Who signed the Python programs that are passed on. They must be python.org's own."""
    files = [python_dir / n for n in ("python.exe", "pythonw.exe", f"python{sys.version_info.major}{sys.version_info.minor}.dll")]
    ps = ("$ErrorActionPreference='Stop'; " + "; ".join(
        f"$s = Get-AuthenticodeSignature -LiteralPath '{f}'; Write-Output ('{f.name}|' + $s.Status + '|' + $s.SignerCertificate.Subject)"
        for f in files))
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True, text=True)  # noqa: S603, S607
    rows = [x.split("|", 2) for x in r.stdout.splitlines() if x.count("|") >= 2]
    if r.returncode or len(rows) != len(files):
        raise BuildError(f"the signatures of the Python programs could not be read:\n{r.stdout}\n{r.stderr}")
    for name, status, subject in rows:
        if status != "Valid" or "Python Software Foundation" not in subject:
            raise BuildError(f"{name} is not signed by the Python Software Foundation ({status}; {subject}).")
    return rows


# ---------- licences ----------

def declared(dist) -> str:
    m = dist.metadata
    first = ((m.get("License") or "").strip().splitlines() or [""])[0]
    classifiers = "; ".join(c.split("::")[-1].strip() for c in m.get_all("Classifier", []) if c.startswith("License ::"))
    return " | ".join(x for x in (m.get("License-Expression") or "", first[:80], classifiers) if x)


def collect_package_notices(site: Path, licenses: Path) -> list:
    """The licence files of every package, copied where they are easy to find, and the list of what is inside.
    Stops at a package that notices.json does not know, and at a declared GPL licence it does not expect."""
    rows, unknown, surprises = [], [], []
    for dist in sorted(md.distributions(path=[str(site)]), key=lambda d: norm(d.metadata["Name"])):
        name, version = norm(dist.metadata["Name"]), dist.version
        expected = NOTICES["packages"].get(name)
        says = declared(dist)
        if expected is None:
            unknown.append(f"{name} {version} (declares: {says or 'nothing'})")
            continue
        if "GPL" in says.upper() and "GPL" not in expected.upper():
            surprises.append(f"{name} {version} declares '{says}', the list expects '{expected}'")
        dest = licenses / "packages" / f"{name}-{version}"
        n = 0
        for f in dist.files or []:
            parts = Path(str(f)).parts
            if not parts or not parts[0].endswith(".dist-info") or not LICENCE_FILE.search(parts[-1]):
                continue
            src = Path(dist.locate_file(f))
            if src.is_file():
                dest.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dest / "__".join(parts[1:]))
                n += 1
        rows.append((name, version, expected, says, n))
    if unknown:
        raise BuildError("packages in the bundle that notices.json does not list (read their licences, then add them):\n  "
                         + "\n  ".join(unknown))
    if surprises:
        raise BuildError("licences that changed:\n  " + "\n  ".join(surprises))
    missing = sorted(set(NOTICES["packages"]) - {r[0] for r in rows})
    if missing:
        raise BuildError(f"notices.json lists packages that are not in the bundle: {', '.join(missing)}")
    lines = ["Packages inside this installation, with the licence each is passed on under.",
             "Their own licence files are in the folders beside this file; a package with 0 files states its licence",
             "in its description only.", "",
             f"{'package':22s} {'version':18s} {'files':>5s}  licence", "-" * 100]
    lines += [f"{n:22s} {v:18s} {k:5d}  {lic}" + (f"\n{'':48s}(the package says: {says})" if says else "")
              for n, v, lic, says, k in rows]
    (licenses / "PACKAGES.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return rows


def library_of(filename: str) -> dict | None:
    plain = re.sub(r"-[0-9a-f]{32}(?=\.dll$)", "", filename, flags=re.I)      # wheels add a mark to the names of their libraries
    for lib in NOTICES["libraries"]:
        if any(fnmatch.fnmatchcase(plain.lower(), pat.lower()) for pat in lib["files"]):
            return lib
    return None


def collect_library_notices(bundle: Path, licenses: Path) -> list:
    """Every .dll in the bundle against the list of libraries. Stops at one the list does not know."""
    found, unknown = {}, []
    for f in sorted(bundle.rglob("*.dll")):
        lib = library_of(f.name)
        rel = f.relative_to(bundle).as_posix()
        if lib is None:
            unknown.append(rel)
        else:
            found.setdefault(lib["name"], []).append(rel)
    if unknown:
        raise BuildError("library files in the bundle that notices.json does not list (find out what they are and under "
                         "which licence, then add them):\n  " + "\n  ".join(unknown))
    texts = licenses / "libraries"
    texts.mkdir(parents=True, exist_ok=True)
    for f in sorted((HERE / "licenses").glob("*.txt")):
        shutil.copyfile(f, texts / f.name)
    lines = ["Libraries inside the packages and inside Python (the .dll files of this installation), each with its licence,",
             "where its source is, and where its licence text is in this folder. All are passed on unchanged.", "",
             NOTICES["copyleft"], ""]
    rows = []
    for lib in NOTICES["libraries"]:
        files = found.get(lib["name"])
        if not files:
            continue
        if lib.get("text") and not (texts / lib["text"]).is_file():
            raise BuildError(f"the licence text {lib['text']} of {lib['name']} is missing from packaging/windows/licenses.")
        where = f"libraries/{lib['text']}" if lib.get("text") else lib.get("where", "")
        lines += [lib["name"], f"    licence: {lib['licence']}", f"    source:  {lib['source']}", f"    text:    {where}",
                  f"    files:   {len(files)}: " + ", ".join(sorted({Path(x).name for x in files})), ""]
        rows.append((lib["name"], lib["licence"], len(files)))
    (licenses / "LIBRARIES.txt").write_text("\n".join(lines), encoding="utf-8")
    return rows


def write_readme(bundle: Path, version: str) -> None:
    notice = about()[1]
    (bundle / "README.txt").write_text(f"""Bohemian Wind {version}

From met mast data to a permitting pack: data checks, long-term correction, wind map, layout and energy, noise and
shadow flicker, and the pack.

{notice}

Start it from the Start menu (Bohemian Wind). It opens in a window of its own and stops when that window is closed.
Projects are kept in Documents\\Bohemian Wind unless you choose another folder. Removing the program (Settings,
Apps) leaves your projects where they are.

It runs on this computer only. It sends nothing anywhere, and downloads only what a project asks for: reference
wind data (NASA POWER), terrain heights (SRTM) and, when you press "Get buildings", building outlines
(OpenStreetMap and Microsoft).

If something goes wrong, the messages are in the file app.log in the folder .bohemian-wind of your user folder.

Licences: Bohemian Wind is under the MIT licence (licenses\\Bohemian-Wind-LICENSE.txt). It comes with Python and
open-source packages under their own licences; all of them are in the folder "licenses" (start with PACKAGES.txt
and LIBRARIES.txt).

Source and help: https://github.com/MugambiD/bohemian-wind.
""", encoding="utf-8", newline="\r\n")


def build_bundle(work: Path, commit: str) -> None:
    if sys.platform != "win32" or sys.maxsize < 2 ** 32:
        raise BuildError("the bundle is built on 64-bit Windows only.")
    version = app_version()
    bundle = work / "bundle"
    if bundle.exists():
        shutil.rmtree(bundle)
    say(f"Bohemian Wind {version}: bundle in {bundle}")
    say(f"1. Python {sys.version.split()[0]} from {sys.base_prefix}")
    python_dir = copy_python(bundle / "python")
    signed = signatures(python_dir)
    say("   signed by: " + "; ".join(f"{n}: {s.split(',')[0]}" for n, _, s in signed))
    say("2. Packages")
    site = python_dir / "Lib" / "site-packages"
    install_packages(site, work)
    say("3. What the bundle's own Python sees")
    seen = probe(python_dir / "python.exe", bundle)
    say("   " + ", ".join(f"{m} {v[0]}" for m, v in seen["modules"].items() if v[0]) + f", Tcl {seen['tcl']}")
    say("4. Licences")
    licenses = bundle / "licenses"
    licenses.mkdir()
    shutil.copyfile(REPO / "LICENSE", licenses / "Bohemian-Wind-LICENSE.txt")
    shutil.copyfile(REPO / "THIRD_PARTY_NOTICES.md", licenses / "THIRD_PARTY_NOTICES.md")
    shutil.copyfile(python_dir / "LICENSE.txt", licenses / "Python-LICENSE.txt")
    packages = collect_package_notices(site, licenses)
    libraries = collect_library_notices(bundle, licenses)
    say(f"   {len(packages)} packages, {len(libraries)} libraries in {sum(n for _, _, n in libraries)} files, every one on the list")
    shutil.copyfile(REPO / "icon.ico", bundle / "icon.ico")
    write_readme(bundle, version)
    files = [f for f in bundle.rglob("*") if f.is_file()]
    (bundle / "BUILD.txt").write_text("\n".join([
        f"Bohemian Wind {version}", f"commit: {commit or 'not given'}",
        f"built:  {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC",
        f"Python: {sys.version.split()[0]} (python.org build; python.exe, pythonw.exe and the Python library signed by the "
        "Python Software Foundation, checked at build time)",
        "", "Packages:"] + [f"  {n}=={v}" for n, v, *_ in packages] + [""]), encoding="utf-8")
    say(f"Bundle ready: {len(files):,} files, {sum(f.stat().st_size for f in files) / 1e6:,.0f} MB")


# ---------- the installer ----------

def find_iscc() -> Path:
    hit = shutil.which("ISCC") or shutil.which("iscc")
    if hit:
        return Path(hit)
    for root in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
        if root and (Path(root) / "Inno Setup 6" / "ISCC.exe").is_file():
            return Path(root) / "Inno Setup 6" / "ISCC.exe"
    raise BuildError("Inno Setup 6 was not found (ISCC.exe). Install it from https://jrsoftware.org/isdl.php")


def build_installer(work: Path, out: Path) -> Path:
    bundle = work / "bundle"
    if not (bundle / "BUILD.txt").is_file():
        raise BuildError(f"no bundle in {bundle}: run 'build.py bundle' first.")
    version = app_version()
    out.mkdir(parents=True, exist_ok=True)
    run([find_iscc(), "/Qp", f"/DAppVersion={version}", f"/DNumericVersion={numeric_version(version)}",
         f"/DBundle={bundle.resolve()}", f"/DOutDir={out.resolve()}", f"/DRepo={REPO}", HERE / "installer.iss"])
    exe = out / f"BohemianWind-{version}-Setup.exe"
    if not exe.is_file():
        raise BuildError(f"Inno Setup finished without writing {exe}.")
    digest = hashlib.sha256(exe.read_bytes()).hexdigest()
    (out / (exe.name + ".sha256")).write_text(f"{digest}  {exe.name}\n", encoding="ascii")
    shutil.copyfile(bundle / "BUILD.txt", out / "BUILD.txt")
    shutil.copyfile(bundle / "licenses" / "PACKAGES.txt", out / "PACKAGES.txt")
    shutil.copyfile(bundle / "licenses" / "LIBRARIES.txt", out / "LIBRARIES.txt")
    say(f"Installer: {exe} ({exe.stat().st_size / 1e6:,.0f} MB)\nSHA-256:   {digest}")
    return exe


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("stage", choices=("bundle", "installer"))
    ap.add_argument("--work", default=str(REPO / "build" / "windows"))
    ap.add_argument("--out", default=str(REPO / "dist"))
    ap.add_argument("--commit", default="")
    a = ap.parse_args(argv)
    if a.stage == "bundle":
        build_bundle(Path(a.work), a.commit)
    else:
        build_installer(Path(a.work), Path(a.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
