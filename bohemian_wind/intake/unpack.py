"""Archives of logger files, opened by the software itself.

Loggers send a file a day and people keep them a month to an archive: a folder of mast data is often a folder of
ZIP or RAR files. Their logger files are unpacked here, into the output folder (never into the data folder: the
originals stay exactly as they are), and read from there like any others.

    ZIP, tar (.tar, .tgz, .tar.gz, .tar.bz2, .tar.xz)   opened with Python's own library
    RAR, 7z                                               opened with a program the computer already has:
        Windows 10 and 11 bring one (tar.exe, built on libarchive), macOS does too, Linux has it as "bsdtar";
        7-Zip or WinRAR are used where they are installed, and the unrar2-cffi Python package where that is.

Nothing that opens RAR is shipped with Bohemian Wind: the RAR method is not open, and the computer's own program
does the work. An archive is unpacked once; it is unpacked again only when the archive itself has changed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from .. import paths

KINDS = {".zip": "zip", ".rar": "rar", ".7z": "7z", ".tar": "tar", ".tgz": "tar", ".tar.gz": "tar", ".tbz2": "tar",
         ".tar.bz2": "tar", ".txz": "tar", ".tar.xz": "tar"}
SUFFIXES = tuple(KINDS)
LABEL = {"zip": "ZIP", "rar": "RAR", "7z": "7z", "tar": "tar"}
WANTED = (".dat", ".txt", ".csv")           # what the readers take from an unpacked folder
NEED_A_CONVERTER = (".rld", ".rwd")         # logger files a converter must see first: reported, not unpacked
SKIP_DIRS = ("_to_delete", "converted", "runs")
MARK = ".unpacked.json"                     # in every unpacked folder: which archive it came from, and in what state
SCAN_LIMIT = 20000
MAX_BYTES = 8 << 30                         # logger files of one archive: more than this is not mast data
TOOL_SECONDS = 900
STEM_MAX = 32                               # characters of an archive's folder name
PADDING = 1 << 17                           # zeros after a RAR archive handed to the system's program (see _tar_into)
VOLUME = re.compile(r"\.part0*(\d+)\.rar$", re.I)      # an archive in several files: month.part1.rar, month.part2.rar
_NO_WINDOW = 0x08000000 if sys.platform.startswith("win") else 0      # no console window flashing up on Windows


class UnpackError(RuntimeError):
    """Why one archive could not be opened, in words for the user."""


def kind(path) -> str | None:
    """'zip', 'rar', '7z' or 'tar' for an archive this module opens, by its name; None for anything else."""
    name = Path(path).name.lower()
    return next((k for suf, k in KINDS.items() if name.endswith(suf)), None)


def find(folder) -> list:
    """The archives under a data folder, in a fixed order. Output folders kept inside it are left out."""
    out = []
    for dirpath, dirs, names in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d.lower() not in SKIP_DIRS)
        out += [Path(dirpath) / n for n in sorted(names) if kind(n) and (VOLUME.search(n) is None or int(VOLUME.search(n).group(1)) == 1)]
        if len(out) > SCAN_LIMIT:
            break
    return out


# ---------- the programs of the computer ----------

_TOOLS = None


def tools() -> list:
    """(name, program, how) of every program on this computer that opens RAR and 7z archives, best first."""
    global _TOOLS
    if _TOOLS is not None:
        return _TOOLS
    found = []
    if sys.platform.startswith("win"):
        tar = Path(os.environ.get("SystemRoot") or r"C:\Windows") / "System32" / "tar.exe"
        if tar.is_file():
            found.append(("the archive program of Windows", str(tar), "tar"))
    else:
        for name in ("bsdtar", "tar"):
            exe = shutil.which(name)
            if exe and (name == "bsdtar" or "bsdtar" in _said([exe, "--version"])):
                found.append(("bsdtar", exe, "tar"))
                break
    roots = [os.environ.get(k) for k in ("PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432")] if sys.platform.startswith("win") else []
    seven = next((e for e in map(shutil.which, ("7z", "7zz", "7za")) if e), None) or next(
        (str(Path(r) / "7-Zip" / "7z.exe") for r in roots if r and (Path(r) / "7-Zip" / "7z.exe").is_file()), None)
    if seven:
        found.append(("7-Zip", seven, "7z"))
    unrar = shutil.which("unrar") or next(
        (str(Path(r) / "WinRAR" / "UnRAR.exe") for r in roots if r and (Path(r) / "WinRAR" / "UnRAR.exe").is_file()), None)
    if unrar:
        found.append(("UnRAR", unrar, "unrar"))
    _TOOLS = found
    return found


def _said(cmd: list) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=20, creationflags=_NO_WINDOW, stdin=subprocess.DEVNULL)  # noqa: S603
        return (r.stdout + r.stderr).decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError):
        return ""


def _run(cmd: list) -> tuple:
    """(exit code, what it printed, its messages) of one of the computer's programs."""
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=TOOL_SECONDS, creationflags=_NO_WINDOW,  # noqa: S603
                           stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise UnpackError(f"opening it took more than {TOOL_SECONDS // 60} minutes") from None
    except OSError as e:
        raise UnpackError(f"the program that opens it could not be started ({e.strerror or e})") from None
    return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")


def _why(text: str) -> str:
    """The reason in a program's own messages, short enough to show."""
    if re.search(r"passw|encrypt", text, re.I):
        return "it is protected by a password"
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    lines = [ln for ln in lines if not ln.lower().startswith(("7-zip", "unrar ", "scanning")) and "error exit delayed" not in ln.lower()]
    if not lines:
        return "the program gave no reason"
    own = re.compile(r"^(?:bsd)?tar(?:\.exe)?:\s*", re.I)
    # tar says what went wrong first and sums up after; 7-Zip and UnRAR name the file first and the reason last
    return own.sub("", lines[0] if own.match(lines[0]) else lines[-1])[:200]


# ---------- names ----------

def _flat(name: str) -> tuple:
    """(folder part, file name fit for any system) of a name inside an archive."""
    p = PurePosixPath(name.replace("\\", "/"))
    base = re.sub(r'[<>:"|?*\x00-\x1f]', "_", p.name).rstrip(". ") or "file"
    return str(p.parent), base


def _targets(names: list) -> dict:
    """Where each wanted file of an archive goes: its own name, in one folder. Two files of the same name from
    different folders of the archive both get a mark of their folder, so neither depends on which came first."""
    flat = {n: _flat(n) for n in names}
    seen = {}
    for folder, base in flat.values():
        seen.setdefault(base.lower(), set()).add(folder)
    out = {}
    for n, (folder, base) in flat.items():
        if len(seen[base.lower()]) > 1:
            stem, suf = os.path.splitext(base)
            base = f"{stem}~{hashlib.sha1(folder.encode('utf-8')).hexdigest()[:8]}{suf}"  # noqa: S324 (a name, not a secret)
        out[n] = base
    return out


def _wanted(name: str) -> bool:
    return name.lower().endswith(WANTED) and not PurePosixPath(name.replace("\\", "/")).name.startswith(".")


def _tally(names: list, other: dict) -> None:
    for n in names:
        low = n.lower()
        for suf in NEED_A_CONVERTER:
            if low.endswith(suf):
                other[suf] = other.get(suf, 0) + 1
        if kind(n):
            other["archives"] = other.get("archives", 0) + 1


# ---------- one archive ----------

def _copy(src, target: Path, budget: list) -> None:
    with open(target, "wb") as out:
        while True:
            block = src.read(1 << 20)
            if not block:
                break
            budget[0] -= len(block)
            if budget[0] < 0:
                raise UnpackError("it holds more than 8 GB of logger files, which no mast produces")
            out.write(block)


def _zip(archive: Path, out: Path, other: dict) -> list:
    try:
        with zipfile.ZipFile(archive) as z:
            members = [i for i in z.infolist() if not i.is_dir()]
            _tally([i.filename for i in members], other)
            take = [i for i in members if _wanted(i.filename)]
            where = _targets([i.filename for i in take])
            budget = [MAX_BYTES]
            for i in take:
                with z.open(i) as src:
                    _copy(src, out / where[i.filename], budget)
            return sorted(where.values())
    except RuntimeError as e:               # zipfile's word for a password
        raise UnpackError(_why(str(e))) from None
    except (zipfile.BadZipFile, NotImplementedError, OSError, EOFError) as e:
        raise UnpackError(f"it is damaged or not a ZIP file ({e})") from None


def _tar(archive: Path, out: Path, other: dict) -> list:
    try:
        with tarfile.open(archive) as t:
            members = [m for m in t.getmembers() if m.isfile()]
            _tally([m.name for m in members], other)
            take = [m for m in members if _wanted(m.name)]
            where = _targets([m.name for m in take])
            budget = [MAX_BYTES]
            for m in take:
                src = t.extractfile(m)
                if src is not None:
                    with src:
                        _copy(src, out / where[m.name], budget)
            return sorted(where.values())
    except (tarfile.TarError, OSError, EOFError) as e:
        raise UnpackError(f"it is damaged or not a tar file ({e})") from None


def _names(listing: str) -> list:
    return [ln.rstrip("\r") for ln in listing.splitlines() if ln.strip() and not ln.rstrip("\r").endswith(("/", "\\"))]


def _tar_into(exe: str, archive: Path, into: str, other: dict) -> bool:
    """Unpack the logger files of an archive into `into` with the system's archive program. False when it holds none."""
    if kind(archive) == "rar" and not VOLUME.search(archive.name):
        # The program looks at the end of a file before its beginning, and a RAR archive that ends in a ZIP file
        # kept unpacked inside it (WinRAR keeps them so) is then taken for that ZIP file: the archive's own files
        # would be passed over without a word. So the program gets a copy with nothing but zeros after its end,
        # kept in the computer's own folder for passing files (short paths: see _with_tool).
        with tempfile.TemporaryDirectory(prefix="bw-") as side:
            name = os.path.join(side, "copy.rar")
            with open(archive, "rb") as src, open(name, "wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
                dst.write(b"\0" * PADDING)
            return _tar_named(exe, name, into, other)
    return _tar_named(exe, str(archive), into, other)


def _tar_named(exe: str, name: str, into: str, other: dict) -> bool:
    code, listing, err = _run([exe, "-tf", name])
    if code != 0:
        raise UnpackError(_why(err or listing))
    names = _names(listing)
    if not names:                           # an archive of nothing is no archive: another program may read it
        raise UnpackError("the program found nothing in it")
    _tally(names, other)
    take = [n for n in names if _wanted(n)]
    if not take:
        return False
    ends = sorted({os.path.splitext(n)[1] for n in take})          # as spelt in the archive: .dat, .DAT
    code, said, err = _run([exe, "-xf", name, "-C", into, *[a for e in ends for a in ("--include", f"*{e}")]])
    if code != 0:
        raise UnpackError(_why(err or said))
    return True


def _with_tool(archive: Path, out: Path, other: dict, tool: tuple) -> list:
    """Unpack with one of the computer's programs: into a folder of its own first, from which only the logger
    files are taken. Names inside the archive never decide where a file is written. The folder sits beside the
    archive's own and has a short name: Windows allows a path 259 characters, and projects live deep."""
    _, exe, how = tool
    with tempfile.TemporaryDirectory(prefix=".u", dir=out.parent) as into:
        if how == "tar":
            _tar_into(exe, archive, into, other)
        elif how == "7z":
            code, listing, err = _run([exe, "l", "-slt", "-ba", "-p-", str(archive)])
            if code > 1:
                raise UnpackError(_why(err or listing))
            blocks = [dict(ln.split(" = ", 1) for ln in b.splitlines() if " = " in ln) for b in listing.replace("\r", "").split("\n\n")]
            _tally([b["Path"] for b in blocks if b.get("Path") and b.get("Folder") != "+"], other)
            code, said, err = _run([exe, "x", "-y", "-bd", "-ssc-", "-p-", f"-o{into}", str(archive), *[f"-ir!*{s}" for s in WANTED]])
            if code > 1:                    # 1 is a warning: the files are there
                raise UnpackError(_why(err or said))
        else:
            code, listing, err = _run([exe, "lb", "-p-", str(archive)])
            if code not in (0, 1):
                raise UnpackError(_why(err or listing))
            _tally(_names(listing), other)
            code, said, err = _run([exe, "x", "-y", "-o+", "-p-", str(archive), *[f"*{s}" for s in WANTED], into + os.sep])
            if code not in (0, 1):
                raise UnpackError(_why(err or said))
        files = []
        for dirpath, dirs, names in os.walk(into):
            files += [Path(dirpath) / n for n in names if _wanted(n) and not (Path(dirpath) / n).is_symlink()]
        rel = {f.relative_to(into).as_posix(): f for f in files}
        where = _targets(sorted(rel))
        if sum(f.stat().st_size for f in files) > MAX_BYTES:
            raise UnpackError("it holds more than 8 GB of logger files, which no mast produces")
        for name, f in rel.items():
            os.replace(f, out / where[name])
        return sorted(where.values())


def _with_unrar_package(archive: Path, out: Path, other: dict) -> list:
    from unrar.cffi import rarfile  # noqa: PLC0415  (the unrar2-cffi package, where someone installed it)
    try:
        rf = rarfile.RarFile(str(archive))
        names = [n for n in rf.namelist() if not n.endswith(("/", "\\"))]
        _tally(names, other)
        take = [n for n in names if _wanted(n)]
        where = _targets(take)
        for n in take:
            (out / where[n]).write_bytes(rf.read(n))
        return sorted(where.values())
    except Exception as e:  # noqa: BLE001  (the package raises its own kinds)
        raise UnpackError(_why(str(e)) if str(e) else type(e).__name__) from None


def _open(archive: Path, out: Path, other: dict) -> tuple:
    """Unpack the logger files of one archive into `out`. Returns (file names, what opened it, a note or "")."""
    k = kind(archive)
    if k == "zip":
        try:
            return _zip(archive, out, other), "Python", ""
        except UnpackError as e:
            first = e                        # a method Python does not have: the computer's program may
    elif k == "tar":
        return _tar(archive, out, other), "Python", ""
    else:
        first = None
    reasons = []
    for tool in tools():
        if tool[2] == "unrar" and k != "rar":
            continue
        try:
            other.clear()
            files = _with_tool(archive, out, other, tool)
            return files, tool[0], (f"{archive.name}: {reasons[0]}, so {tool[0]} opened it" if reasons else "")
        except UnpackError as e:
            reasons.append(f"{tool[0]} said: {e}")
            for f in out.iterdir():          # what a failed try left behind
                if f.name != MARK and f.is_file():
                    f.unlink()
    if k == "rar":
        try:
            import unrar.cffi  # noqa: F401, PLC0415
        except ImportError:
            pass
        else:
            other.clear()
            return _with_unrar_package(archive, out, other), "the unrar2-cffi package", "; ".join(reasons)
    if first is not None:
        raise first
    if reasons:
        raise UnpackError("; ".join(reasons) if len(reasons) > 1 else reasons[0].split(" said: ", 1)[1])
    raise UnpackError(f"this computer has no program that opens {LABEL[k]} archives (Windows 10 and 11 bring one; "
                      "7-Zip does it everywhere)")


# ---------- a data folder ----------

def _folders(archives: list, base: Path) -> dict:
    """The folder name each archive unpacks to: its own name, and a mark of its place when two share a name."""
    stems = {}
    for a in archives:
        stems.setdefault(stem(a).lower(), []).append(a)
    out = {}
    for a in archives:
        name = stem(a)
        if len(stems[name.lower()]) > 1:
            try:
                place = a.relative_to(base).as_posix()
            except ValueError:
                place = str(a)
            name = f"{name}~{hashlib.sha1(place.encode('utf-8')).hexdigest()[:8]}"  # noqa: S324
        out[a] = name
    return out


def stem(archive: Path) -> str:
    """The archive's name without its ending, fit for a folder name on any system, and short: a long name keeps its
    first part and gets a mark of the whole (Windows allows a path 259 characters, and projects live deep)."""
    name = archive.name
    low = name.lower()
    suf = next(s for s in sorted(KINDS, key=len, reverse=True) if low.endswith(s))
    full = re.sub(r'[<>:"|?*\x00-\x1f]', "_", name[: -len(suf)]).rstrip(". ") or "archive"
    if len(full) <= STEM_MAX:
        return full
    return full[: STEM_MAX - 7].rstrip("._- ") + "~" + hashlib.sha1(full.encode("utf-8")).hexdigest()[:6]  # noqa: S324


def unpack(archives: list, base, dest, log=print, keep=None) -> dict:
    """Unpack the logger files of `archives` (files under the data folder `base`) into dest/<archive name>/.

    keep(path) -> bool: asked for every unpacked .txt and .csv; a file it turns down is removed again (notes and
    tables that are no logger export). Returns the counts for the messages, with 'failed': [(archive name, why)].
    """
    base, dest = Path(base), paths.tidy(dest)
    names = _folders(archives, base)
    res = {"archives": len(archives), "opened": 0, "now": 0, "files": 0, "failed": [], "other": {}, "with": [], "notes": [],
           "dest": dest}
    announced = False
    for a in archives:
        out = dest / names[a]
        st = a.stat()
        state = {"archive": a.name, "size": st.st_size, "mtime_ns": st.st_mtime_ns}
        try:
            old = json.loads((out / MARK).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            old = None
        if old and all(old.get(k) == v for k, v in state.items()) and all((out / f).is_file() for f in old.get("files", [])):
            res["opened"] += 1                # unpacked in an earlier run, and unchanged since
            res["files"] += len(old["files"])
            for suf, n in (old.get("other") or {}).items():
                res["other"][suf] = res["other"].get(suf, 0) + n
            continue
        if not announced:
            log(f"Opening the archives in the data folder ({len(archives):,}) ...")
            announced = True
        if out.is_dir():                      # the archive changed: nothing of its earlier content stays
            shutil.rmtree(out, ignore_errors=True)
        out.mkdir(parents=True, exist_ok=True)
        other = {}
        try:
            files, by, note = _open(a, out, other)
            if note:
                res["notes"].append(note)
        except UnpackError as e:
            shutil.rmtree(out, ignore_errors=True)
            res["failed"].append((a.name, str(e)))
            continue
        except OSError as e:                  # the output folder itself: full disk, a path too long for Windows
            shutil.rmtree(out, ignore_errors=True)
            res["failed"].append((a.name, f"its files could not be written ({e.strerror or e}){paths.too_long(e.filename or out)}"))
            continue
        if keep is not None:
            for f in list(files):
                if f.lower().endswith((".txt", ".csv")) and not keep(out / f):
                    (out / f).unlink(missing_ok=True)
                    files.remove(f)
        (out / MARK).write_text(json.dumps({**state, "files": files, "other": other, "with": by}, indent=1), encoding="utf-8")
        res["opened"] += 1
        res["now"] += len(files)
        res["files"] += len(files)
        if by not in res["with"]:
            res["with"].append(by)
        for suf, n in other.items():
            res["other"][suf] = res["other"].get(suf, 0) + n
    if dest.is_dir():                         # an archive that is gone from the data folder takes its files with it
        current = set(names.values())
        for d in dest.iterdir():
            if d.is_dir() and (d / MARK).is_file() and d.name not in current:
                shutil.rmtree(d, ignore_errors=True)
    if not announced:
        log(f"Archives in the data folder ({len(archives):,}): unpacked in an earlier run and unchanged since.")
    if res["opened"]:
        log(f"  {res['files']:,} logger files from {res['opened']:,} archive{'s' if res['opened'] != 1 else ''}"
            + (f" ({res['now']:,} unpacked now, with {' and '.join(res['with'])})" if res["with"] else "")
            + f". They are kept in {dest}; the archives are untouched.")
    for i, note in enumerate(res["notes"][:3]):
        log(f"  Note: {note}." + (f" The same goes for {len(res['notes']) - 3:,} more." if i == 2 and len(res["notes"]) > 3 else ""))
    for name, why in res["failed"][:5]:
        log(f"  NOT OPENED {name}: {why}.")
    if len(res["failed"]) > 5:
        log(f"  ... and {len(res['failed']) - 5:,} more archives that could not be opened.")
    held = [f"{n:,} {suf}" for suf, n in sorted(res["other"].items()) if suf in NEED_A_CONVERTER]
    if held:
        log(f"  The archives also hold {' and '.join(held)} files. Those need their converter first: extract them into "
            "the data folder yourself to have them read.")
    return res


def failure(res: dict) -> str:
    """One sentence for a folder whose archives gave nothing to read."""
    if res["failed"]:
        n, (name, why) = len(res["failed"]), res["failed"][0]
        return ((f"The archive {name} could not be opened: {why}." if n == 1 else
                 f"{n:,} archives could not be opened (the first, {name}: {why}).")
                + " Extract the logger files into the folder yourself (in Windows: right-click the archive, "
                  "\"Extract all\") and run again.")
    held = [f"{n:,} {suf}" for suf, n in sorted(res["other"].items()) if suf in NEED_A_CONVERTER]
    if held:
        return (f"The archives were opened and hold {' and '.join(held)} files, which need their converter first: "
                "extract them into the folder yourself and run again.")
    return "The archives were opened and hold no logger files this version reads."
