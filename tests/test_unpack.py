"""Archives of logger files are opened by the software itself (bohemian_wind/intake/unpack.py).

Every file here is invented: the mast is the demo's, packed in different ways. The RAR files are written by this
test as containers that hold their files unpacked ("stored"), from the published description of the RAR 5 format;
the programs that open RAR archives read them like any other.

Run: python -m pytest tests/test_unpack.py -q
"""
import hashlib
import io
import os
import shutil
import struct
import subprocess
import sys
import tarfile
import zipfile
import zlib
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bohemian_wind import demo, intake  # noqa: E402
from bohemian_wind.intake import unpack  # noqa: E402

TODAY = date(2026, 3, 1)
quiet = lambda m: None  # noqa: E731


# ---------- writing archives ----------

def _vint(n: int) -> bytes:
    out = bytearray()
    while True:
        out.append((n & 0x7F) | (0x80 if n > 0x7F else 0))
        n >>= 7
        if not n:
            return bytes(out)


def _rar_block(kind: int, flags: int, fields: bytes, data_size: int | None = None) -> bytes:
    body = _vint(kind) + _vint(flags) + (b"" if data_size is None else _vint(data_size)) + fields
    size = _vint(len(body))
    return struct.pack("<I", zlib.crc32(size + body)) + size + body


def write_rar(path: Path, files: dict) -> None:
    """A RAR 5 archive whose files are stored as they are (method 0)."""
    out = bytearray(b"Rar!\x1a\x07\x01\x00")
    out += _rar_block(1, 0, _vint(0))                                           # main archive header
    for name, data in files.items():
        raw = name.encode("utf-8")
        fields = (_vint(0x0004) + _vint(len(data)) + _vint(0x20) + struct.pack("<I", zlib.crc32(data))
                  + _vint(0) + _vint(0) + _vint(len(raw)) + raw)                # file flags, size, attributes, CRC, method, host, name
        out += _rar_block(2, 0x0002, fields, data_size=len(data)) + data
    out += _rar_block(5, 0, _vint(0))                                           # end of archive
    path.write_bytes(bytes(out))


def write_zip(path: Path, files: dict) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)


def write_tgz(path: Path, files: dict) -> None:
    with tarfile.open(path, "w:gz") as t:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))


def write_7z(path: Path, files: dict, tmp: Path) -> None:
    src = tmp / ("src_" + path.stem)
    for name, data in files.items():
        (src / name).parent.mkdir(parents=True, exist_ok=True)
        (src / name).write_bytes(data)
    subprocess.run([SEVEN, "a", "-bd", "-y", str(path), "."], cwd=src, check=True, capture_output=True)  # noqa: S603


SEVEN = next((e for e in map(shutil.which, ("7z", "7zz", "7za")) if e), None)
OPENS_RAR = bool(unpack.tools())
WRITERS = {"zip": (".zip", write_zip), "tgz": (".tar.gz", write_tgz), "rar": (".rar", write_rar), "7z": (".7z", write_7z)}


def needs(kind: str) -> None:
    if kind == "rar" and not OPENS_RAR:
        pytest.skip("no program on this computer opens RAR archives")
    if kind == "7z" and not (SEVEN and OPENS_RAR):
        pytest.skip("no 7-Zip on this computer to write or open a 7z archive")


def sha(folder: Path) -> dict:
    return {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(folder.rglob("*")) if p.is_file()}


# ---------- the invented mast ----------

@pytest.fixture(scope="module")
def mast(tmp_path_factory):
    """The demo mast's logger files (name -> bytes) and what step 1 makes of them unpacked."""
    root = tmp_path_factory.mktemp("mast")
    p = demo.create(root / "Demo Ridge", today=TODAY)
    files = {f.name: f.read_bytes() for f in sorted((p.folder / "data").glob("*.dat"))}
    assert len(files) == 14
    plain = intake.run_checks(p.folder / "data", root / "plain out", log=quiet)
    return files, plain


def pack(folder: Path, files: dict, kind: str, tmp: Path) -> None:
    """The mast's files in three archives, the way a year of daily files is kept: one with a folder inside, one with
    things that are no logger files."""
    suffix, write = WRITERS[kind]
    names = sorted(files)
    parts = [{f"DailyDataFiles/{n}": files[n] for n in names[:5]},
             {n: files[n] for n in names[5:10]},
             {**{n: files[n] for n in names[10:]}, "notes.txt": b"Mast visit on the 3rd: nothing to report.\n",
              "photo.jpg": b"\xff\xd8\xff\xe0 not a photo", "raw/000123.rld": b"\x00\x01", "older.zip": b"PK\x05\x06" + b"\x00" * 18}]
    folder.mkdir(parents=True, exist_ok=True)
    for i, part in enumerate(parts, 1):
        target = folder / f"Mast_DailyDataFiles_2025{i:02d}{suffix}"
        write(target, part, tmp) if kind == "7z" else write(target, part)


@pytest.mark.parametrize("kind", ["zip", "tgz", "rar", "7z"])
def test_a_folder_of_archives_is_read_like_a_folder_of_files(mast, tmp_path, kind):
    needs(kind)
    files, plain = mast
    data, out = tmp_path / "Raw data", tmp_path / "out"
    pack(data, files, kind, tmp_path)
    before = sha(data)
    said = []
    r = intake.run_checks(data, out, log=said.append)

    # the same mast as from the unpacked files, to the record
    for k in ("records", "period", "mean_ws", "recovery_pct", "top_height_m", "shear_alpha", "format"):
        assert r[k] == plain[k], k
    assert r["archives"] == {"found": 3, "opened": 3, "logger_files": 14, "not_opened": []}
    assert r["findings"] == plain["findings"]

    # the originals are exactly what they were; the logger files, and only they, are beside the other outputs
    assert sha(data) == before
    kept = out / "converted" / "Raw_data"
    unpacked = {p.name: p.read_bytes() for p in kept.rglob("*.dat")}
    assert unpacked == files
    assert sorted(p.name for p in kept.rglob("*") if p.is_file() and p.name != unpack.MARK) == sorted(files)
    assert sorted(d.name for d in kept.iterdir()) == [f"Mast_DailyDataFiles_2025{i:02d}" for i in (1, 2, 3)]
    text = "\n".join(said)
    assert "Opening the archives in the data folder (3)" in text and "14 logger files from 3 archives" in text
    assert "The archives also hold 1 .rld files" in text and "the archives are untouched" in text

    # a second check opens nothing again
    stamp = {p.name: p.stat().st_mtime_ns for p in kept.rglob("*.dat")}
    said.clear()
    again = intake.run_checks(data, out, log=said.append)
    assert again["records"] == plain["records"] and {p.name: p.stat().st_mtime_ns for p in kept.rglob("*.dat")} == stamp
    assert "unpacked in an earlier run" in "\n".join(said)


def test_a_rar_that_ends_in_a_zip_file_is_read_as_the_rar_it_is(mast, tmp_path):
    """WinRAR keeps a ZIP file inside an archive as it is. Given such an archive by name, the archive program of
    Windows and of every other system (libarchive) looks at its end, sees the ZIP file and lists that instead: the
    logger files beside it would be passed over without a word. Hence that program is handed a copy of the archive
    with nothing but zeros after its end."""
    if not OPENS_RAR:
        pytest.skip("no program on this computer opens RAR archives")
    files, _ = mast
    names = sorted(files)
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as z:
        z.writestr("inside_the_zip.dat", b"not this one")
    data, dest = tmp_path / "data", tmp_path / "out"
    data.mkdir()
    write_rar(data / "full.rar", {names[0]: files[names[0]], names[1]: files[names[1]], "older_months.zip": inner.getvalue()})
    write_rar(data / "empty_zip_last.rar", {names[2]: files[names[2]], "nothing.zip": b"PK\x05\x06" + b"\x00" * 18})
    r = unpack.unpack(unpack.find(data), data, dest, log=quiet)
    assert r["failed"] == [] and r["files"] == 3
    assert sorted(p.name for p in dest.rglob("*.dat")) == names[:3]
    assert r["other"].get("archives") == 2                                           # the ZIP files inside are counted, not opened


@pytest.mark.parametrize("which", ["tar", "7z"])
def test_every_program_that_opens_rar_gives_the_same_files(mast, tmp_path, monkeypatch, which):
    tool = next((t for t in unpack.tools() if t[2] == which), None)
    if tool is None:
        pytest.skip(f"this computer has no archive program of the kind '{which}'")
    files, _ = mast
    names = sorted(files)
    data, dest = tmp_path / "data", tmp_path / "out"
    data.mkdir()
    write_rar(data / "month.rar", {f"Daily Files/{n}": files[n] for n in names[:4]} | {"Daily Files/LOG.TXT": b"a note\n", "UPPER.DAT": files[names[4]]})
    monkeypatch.setattr(unpack, "_TOOLS", [tool])
    r = unpack.unpack(unpack.find(data), data, dest, log=quiet)
    assert r["failed"] == [] and r["notes"] == [] and r["with"] == [tool[0]], (r["failed"], r["notes"], unpack._said([tool[1], "--version"])[:200])
    got = {p.name: p.read_bytes() for p in dest.rglob("*") if p.is_file() and p.name != unpack.MARK}
    assert got == {**{n: files[n] for n in names[:4]}, "LOG.TXT": b"a note\n", "UPPER.DAT": files[names[4]]}       # .DAT and .TXT too


def test_the_unrar_program_is_asked_in_its_own_words(mast, tmp_path, monkeypatch):
    """WinRAR's UnRAR is used where it is installed and nothing else opened the archive. No machine that runs these
    tests has it, so a stand-in checks what it is asked: extract with paths, answer yes, overwrite, ask for no
    password, the three kinds of logger file, into a folder of its own."""
    files, _ = mast
    name, content = next(iter(files.items()))
    data, dest = tmp_path / "data", tmp_path / "out"
    data.mkdir()
    write_rar(data / "month.rar", {name: content})
    asked = []

    def stand_in(cmd, feed=None):
        asked.append(cmd)
        if cmd[1] == "lb":                                                          # the bare list of names
            return 0, f"sub\\{name}\nsub\\000123.rld\n", ""
        (Path(cmd[-1]) / "sub").mkdir()
        (Path(cmd[-1]) / "sub" / name).write_bytes(content)
        return 0, "All OK", ""

    monkeypatch.setattr(unpack, "_TOOLS", [("UnRAR", "C:/Program Files/WinRAR/UnRAR.exe", "unrar")])
    monkeypatch.setattr(unpack, "_run", stand_in)
    r = unpack.unpack(unpack.find(data), data, dest, log=quiet)
    assert r["failed"] == [] and r["with"] == ["UnRAR"] and (dest / "month" / name).read_bytes() == content
    assert asked[0] == ["C:/Program Files/WinRAR/UnRAR.exe", "lb", "-p-", str(data / "month.rar")] and r["other"] == {".rld": 1}
    cmd = asked[1]
    assert cmd[:5] == ["C:/Program Files/WinRAR/UnRAR.exe", "x", "-y", "-o+", "-p-"] and cmd[5] == str(data / "month.rar")
    assert cmd[6:9] == ["*.dat", "*.txt", "*.csv"] and cmd[9].endswith(("/", "\\")) and Path(cmd[9]).name.startswith("bw-")      # the short temporary folder


def test_a_deep_folder_inside_an_archive_is_unpacked_in_the_computers_short_temporary_folder(mast, tmp_path, monkeypatch):
    """A logger's archive held a folder of 74 characters with a subfolder of 63. Unpacked beside a project kept deep in
    the user's folders, its files passed the 259 characters Windows allows, and the archive counted as not opened
    (seen on a real PC). The program now unpacks into the computer's own temporary folder, which has a short path,
    and that folder is gone afterwards whatever happened."""
    if not OPENS_RAR:
        pytest.skip("no program on this computer opens RAR archives")
    files, _ = mast
    names = sorted(files)
    deep = "Mast_ThirdPeriod_DailyDataFiles_20250526_20250617_With_Wrong_Time_UTC+1"
    sub = deep + "/Programs_of_the_third_period_20250526-20250617_GPS_had_UTC+1_instead_of_+2"
    data, dest, systemp = tmp_path / "data", tmp_path / "project" / "converted" / "data", tmp_path / "systemp"
    data.mkdir()
    systemp.mkdir()
    write_rar(data / "month.rar", {f"{deep}/{names[0]}": files[names[0]], f"{deep}/{names[1]}": files[names[1]],
                                   f"{sub}/program.CR1X": b"' a logger program", f"{deep}/notes.txt": b"clock was off\n"})
    monkeypatch.setattr(unpack.tempfile, "tempdir", str(systemp))
    asked = []
    real = unpack._run
    monkeypatch.setattr(unpack, "_run", lambda cmd: (asked.append(cmd), real(cmd))[1])
    r = unpack.unpack(unpack.find(data), data, dest, log=quiet)
    assert r["failed"] == [] and sorted(p.name for p in dest.rglob("*.dat")) == names[:2]
    unpacking = [c for c in asked if "-C" in c or any(str(a).startswith("-o") for a in c)]
    assert unpacking and all(str(systemp) in " ".join(map(str, c)) for c in unpacking)      # not inside the project
    assert list(systemp.iterdir()) == []                                                        # and nothing left behind
    assert not [d for d in dest.rglob("*") if d.is_dir() and d.name.startswith((".u", "bw-"))]
    left = dest / ".uabc123" / deep                                                  # what a failed earlier version left
    left.mkdir(parents=True)
    (left / names[0]).write_bytes(files[names[0]])
    (data / "month.rar").write_bytes((data / "month.rar").read_bytes())             # the archive is touched: opened again
    unpack.unpack(unpack.find(data), data, dest, log=quiet)
    assert not (dest / ".uabc123").exists()


def test_windows_paths_are_given_in_their_long_form(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert unpack._long("/projects/x").startswith("\\\\?\\")
    assert unpack._long("\\\\?\\C:\\already") == "\\\\?\\C:\\already"
    monkeypatch.setattr(sys, "platform", "linux")
    assert unpack._long("/projects/x") == "/projects/x"


def test_an_archive_that_changes_or_goes_takes_its_unpacked_files_with_it(mast, tmp_path):
    files, _ = mast
    names = sorted(files)
    data, dest = tmp_path / "data", tmp_path / "out" / "converted" / "data"
    data.mkdir()
    write_zip(data / "january.zip", {n: files[n] for n in names[:3]})
    write_zip(data / "february.zip", {n: files[n] for n in names[3:6]})
    r = unpack.unpack(unpack.find(data), data, dest, log=quiet)
    assert (r["archives"], r["opened"], r["files"], r["now"], r["failed"]) == (2, 2, 6, 6, [])

    write_zip(data / "january.zip", {names[0]: files[names[0]], names[6]: files[names[6]]})     # re-sent with other content
    (data / "february.zip").unlink()
    r = unpack.unpack(unpack.find(data), data, dest, log=quiet)
    assert (r["archives"], r["files"], r["now"]) == (1, 2, 2)
    assert sorted(p.name for p in dest.rglob("*.dat")) == sorted([names[0], names[6]])
    assert [d.name for d in dest.iterdir()] == ["january"]


def test_names_inside_an_archive_never_decide_where_a_file_is_written(mast, tmp_path):
    files, _ = mast
    one = next(iter(files.values()))
    data, dest = tmp_path / "data", tmp_path / "out" / "converted" / "data"
    (data / "north").mkdir(parents=True)
    (data / "south").mkdir()
    write_zip(data / "north" / "week.zip", {"../../escaped.dat": one, "/absolute/elsewhere.dat": one,
                                           "a/DATA.dat": one, "b/DATA.dat": one + b" ", "C:\\\\win\\\\DATA2.dat": one,
                                           "__MACOSX/._DATA.dat": b"resource fork", "bad:name?.dat": one})
    write_zip(data / "south" / "week.zip", {"DATA.dat": one})                    # the same archive name in another folder
    r = unpack.unpack(unpack.find(data), data, dest, log=quiet)
    assert r["failed"] == [] and r["files"] == 7
    written = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*.dat"))
    assert all(w.startswith("out/converted/data/week~") for w in written), written
    folders = sorted(d.name for d in dest.iterdir())
    assert len(folders) == 2 and all(f.startswith("week~") and len(f) == len("week~") + 8 for f in folders)
    north = next(d for d in dest.iterdir() if len(list(d.glob("*.dat"))) == 6)
    names = sorted(p.name for p in north.glob("*.dat"))
    assert "escaped.dat" in names and "elsewhere.dat" in names and "DATA2.dat" in names and "bad_name_.dat" in names
    marked = [n for n in names if n.startswith("DATA~")]                           # two DATA.dat from two folders: both kept
    assert len(marked) == 2 and {(north / n).read_bytes() for n in marked} == {one, one + b" "}
    assert not list(tmp_path.rglob("._DATA.dat"))


def test_an_archive_that_cannot_be_opened_is_named_and_the_rest_is_read(mast, tmp_path):
    files, plain = mast
    names = sorted(files)
    data, out = tmp_path / "data", tmp_path / "out"
    data.mkdir()
    write_zip(data / "first.zip", {n: files[n] for n in names[:7]})
    write_zip(data / "second.zip", {n: files[n] for n in names[7:]})
    whole = (data / "second.zip").read_bytes()
    (data / "second.zip").write_bytes(whole[: len(whole) // 2])                    # a download that broke off
    said = []
    r = intake.run_checks(data, out, log=said.append)
    assert 0 < r["records"] < plain["records"]
    assert r["archives"]["opened"] == 1 and r["archives"]["not_opened"][0][0] == "second.zip"
    assert r["findings"][0].startswith("The archive second.zip in the data folder could not be opened")
    assert "the records they hold are missing from this check" in r["findings"][0]
    assert any(line.strip().startswith("NOT OPENED second.zip") for line in said)

    (data / "first.zip").unlink()                                                  # nothing left to read: the run stops, and says why
    with pytest.raises(intake.IntakeError, match="The archive second.zip could not be opened.*Extract the logger files"):
        intake.run_checks(data, out, log=quiet)


def test_a_computer_without_a_program_for_rar_says_so_in_plain_words(mast, tmp_path, monkeypatch):
    files, _ = mast
    data = tmp_path / "data"
    data.mkdir()
    write_rar(data / "month.rar", dict(list(files.items())[:2]))
    monkeypatch.setattr(unpack, "_TOOLS", [])
    monkeypatch.setitem(sys.modules, "unrar", None)                                # as if the optional package were not installed
    monkeypatch.setitem(sys.modules, "unrar.cffi", None)
    with pytest.raises(intake.IntakeError) as e:
        intake.run_checks(data, tmp_path / "out", log=quiet)
    text = str(e.value)
    assert "Found: 1 archive." in text and "no program that opens RAR archives" in text and "Extract all" in text
    assert "unrar2-cffi" not in text and "pip" not in text                         # no advice an installed program's user cannot follow


def test_archives_without_logger_files_and_archives_of_files_that_need_a_converter(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    write_zip(data / "report.zip", {"report.pdf": b"%PDF-1.4", "table.csv": b"a,b\n1,2\n", "readme.txt": b"hello\n"})
    with pytest.raises(intake.IntakeError, match="The archives were opened and hold no logger files this version reads"):
        intake.run_checks(data, tmp_path / "out", log=quiet)
    assert not [p for p in (tmp_path / "out").rglob("*") if p.is_file() and p.name != unpack.MARK]      # the notes did not stay

    write_zip(data / "raw.zip", {"000101.rld": b"\x00", "000102.rld": b"\x00"})
    with pytest.raises(intake.IntakeError, match="hold 2 .rld files, which need their converter first"):
        intake.run_checks(data, tmp_path / "out", log=quiet)


def test_one_archive_chosen_by_itself_is_read(mast, tmp_path):
    files, plain = mast
    write_zip(tmp_path / "Whole mast.zip", files)
    r = intake.run_checks(tmp_path / "Whole mast.zip", tmp_path / "out", log=quiet)
    assert r["records"] == plain["records"] and r["mean_ws"] == plain["mean_ws"]
    assert r["archives"] == {"found": 1, "opened": 1, "logger_files": 14, "not_opened": []}


def test_archives_of_symphoniepro_exports_are_read_too(tmp_path):
    """The other way into step 1: NRG SymphoniePRO text exports go through their own reader, which takes the
    unpacked files from the same folder as the ones it converts itself."""
    csv = os.environ.get("BW_DEMO_CSV")
    if not csv:
        pytest.skip("BW_DEMO_CSV not set")
    from make_synthetic import make
    make(csv, tmp_path / "all")
    some = sorted((tmp_path / "all").rglob("*.txt"))[:40]
    assert len(some) == 40
    plain, zipped = tmp_path / "plain", tmp_path / "zipped"
    plain.mkdir()
    zipped.mkdir()
    for f in some:
        shutil.copy(f, plain / f.name)
    write_zip(zipped / "export_part1.zip", {f.name: f.read_bytes() for f in some[:25]})
    write_zip(zipped / "export_part2.zip", {f"later/{f.name}": f.read_bytes() for f in some[25:]})
    a = intake.run_checks(plain, tmp_path / "out a", log=quiet)
    b = intake.run_checks(zipped, tmp_path / "out b", log=quiet)
    assert a["format"] == b["format"] == "NRG SymphoniePRO"
    assert (b["records"], b["period"], b["mean_ws"]) == (a["records"], a["period"], a["mean_ws"]) and a["records"] > 1000
    assert b["archives"] == {"found": 2, "opened": 2, "logger_files": 40, "not_opened": []}


def test_archives_are_counted_not_taken_for_logger_files_and_a_new_one_is_noticed(mast, tmp_path):
    files, _ = mast
    data = tmp_path / "data"
    data.mkdir()
    write_zip(data / "a.zip", dict(list(files.items())[:1]))
    (data / "b.tar.gz").write_bytes(b"not really")
    det = intake.detect(data)
    assert det["format"] is None and det["counts"]["archives"] == 2 and det["found"] == "2 archives" and not det["unread"]
    assert unpack.kind("x.TAR.GZ") == "tar" and unpack.kind("x.RAR") == "rar" and unpack.kind("x.dat") is None
    assert unpack.stem(Path("Mast 2025.tar.gz")) == "Mast 2025"
    long = "MM0000_DailyDataFiles_20250526_20250617_With_Wrong_Time_UTC+1"            # as loggers' archives are named
    short = unpack.stem(Path(long + ".rar"))
    assert len(short) <= unpack.STEM_MAX and short.startswith("MM0000_DailyDataFiles") and short != unpack.stem(Path(long + "2.rar"))
    for name in ("year.part1.rar", "year.part2.rar", "year.part03.rar"):          # one archive in three files: opened by its first
        (data / name).write_bytes(b"Rar!")
    assert [p.name for p in unpack.find(data)] == ["a.zip", "b.tar.gz", "year.part1.rar"]
    sig = intake.folder_signature(data)
    write_zip(data / "c.zip", dict(list(files.items())[:1]))
    assert intake.folder_signature(data) != sig                                    # daily intake sees a new archive arrive


def test_the_reason_a_program_gives_is_put_in_plain_words():
    assert unpack._why("ERROR: Wrong password : month.rar") == "it is protected by a password"
    assert unpack._why("tar.exe: Encrypted file is unsupported\n") == "it is protected by a password"
    assert unpack._why("7-Zip 23.01\n\nScanning the drive\nERROR: month.7z\nCannot open the file as archive\n") == "Cannot open the file as archive"
    assert unpack._why("") == "the program gave no reason"
    assert unpack._why("tar.exe: x.dat: Can't create 'x.dat'\ntar.exe: Error exit delayed from previous errors.\n") == "x.dat: Can't create 'x.dat'"
    assert unpack._why("bsdtar: Error opening archive: Unrecognized archive format\n") == "Error opening archive: Unrecognized archive format"
