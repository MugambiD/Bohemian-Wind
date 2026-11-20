"""Step 1 entry point: recognise the logger format in a folder, run the quality checks and statistics, and
return one summary whatever the format.

Formats: NRG SymphoniePRO (RLD files, converted with NRG's free SymPRO Desktop on Windows, or its TXT exports),
Campbell Scientific TOA5 (.dat), SecondWind Nomad2 Multi-Track exports, NRG Symphonie text exports (written by
Symphonie Data Retriever from .RWD files) and WindPRO time series exports.
"""
from __future__ import annotations

import html
import json
import os
import re
from pathlib import Path

from .. import DISCLAIMER, __version__
from .. import climate as _climate
from .. import paths
from . import unpack

FORMATS = {"sympro": "NRG SymphoniePRO", "toa5": "Campbell Scientific TOA5", "nomad2": "SecondWind Nomad2 export",
           "sdr": "NRG Symphonie text export", "windpro": "WindPRO time series export"}
FILE_LISTED = ("nomad2", "sdr", "windpro")       # formats read from the files found, not from the folder as a whole
ACCEPTED = ("NRG SymphoniePRO (.rld or exported .txt), Campbell Scientific TOA5 (.dat), a SecondWind Nomad2 export, "
            "NRG Symphonie text exports (.txt from Symphonie Data Retriever) or a WindPRO time series export")
DATA_SUFFIXES = (".rld", ".txt", ".dat", ".csv", ".rwd") + unpack.SUFFIXES       # archives count: they are opened
SCAN_LIMIT = 20000


class IntakeError(RuntimeError):
    """A problem the user can fix; the message says how."""


def _logger_text(path) -> bool:
    """A .txt or .csv taken out of an archive: one of the logger exports that are read, or something else?"""
    from ..loggers import nomad2, nrg_sdr, windpro  # noqa: PLC0415
    from ..sympro import looks_like_export  # noqa: PLC0415
    p = Path(path)
    try:
        return bool((p.suffix.lower() == ".txt" and looks_like_export(p)) or nomad2.looks_like_nomad2(p)
                    or nrg_sdr.looks_like_sdr(p) or windpro.looks_like_windpro(p))
    except Exception:  # noqa: BLE001
        return False


def _head(path: Path) -> tuple:
    """(the first bytes of the file, "") or (None, why it cannot be opened)."""
    try:
        with open(path, "rb") as fh:
            return fh.read(8), ""
    except OSError as e:
        return None, (e.strerror or type(e).__name__) + paths.too_long(path)


def detect(path, also=None) -> dict:
    """What a folder (or a single file) holds. Returns format (a FORMATS key or None), counts, the files to read
    for the formats that are read file by file, and 'mixed': the formats found when there is more than one.
    also: a second folder to look in, where the logger files of the data folder's archives were unpacked. Archives
    themselves are only counted here; run_checks opens them (unpack.py)."""
    from ..loggers import nomad2, nrg_sdr, windpro  # noqa: PLC0415
    from ..sympro import looks_like_export  # noqa: PLC0415
    p = paths.tidy(path)
    if not p.exists():
        raise IntakeError(f"Not found: {p}{paths.too_long(p)}")
    files = [p] if p.is_file() else []
    if p.is_dir():
        for dirpath, dirs, names in os.walk(p):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d.lower() not in ("_to_delete", "converted", "runs")]
            files += [Path(dirpath) / n for n in names if n.lower().endswith(DATA_SUFFIXES)]
            if len(files) > SCAN_LIMIT:
                break
    if also is not None and Path(also).is_dir():
        files += sorted(f for f in Path(also).rglob("*") if f.is_file() and f.name.lower().endswith(DATA_SUFFIXES)
                        and not any(part.startswith(".") for part in f.relative_to(also).parts))
    counts = {"rld": 0, "sympro_txt": 0, "toa5": 0, "nomad2": 0, "sdr": 0, "windpro": 0, "archives": 0, "rwd": 0}
    listed = {k: [] for k in FILE_LISTED}
    unread = {}                                # files with a data-like name that no reader recognised, by suffix
    blocked, why_blocked = 0, ""               # files that are there but cannot be opened, and why the first cannot
    probed = 0
    for f in files:
        suf = f.suffix.lower()
        head = b""
        if unpack.kind(f):                     # counted; opened by run_checks, never taken for a logger file
            counts["archives"] += 1
            continue
        if suf not in (".rld", ".rwd") and (suf == ".dat" or probed < 400):
            head, why = _head(f)
        if head is None:                       # not a matter of format: said as what it is
            blocked += 1
            why_blocked = why_blocked or why
        elif suf == ".rld":
            counts["rld"] += 1
        elif suf == ".rwd":                    # NRG Symphonie raw files: only Symphonie Data Retriever opens them
            counts["rwd"] += 1
        elif suf == ".dat" and head.lstrip(b"\xef\xbb\xbf").startswith(b'"TOA5"'):
            counts["toa5"] += 1
        elif probed < 400:                     # text exports are told apart by their first lines; cap the probing
            probed += 1
            try:
                if suf == ".txt" and looks_like_export(f):
                    counts["sympro_txt"] += 1
                elif nomad2.looks_like_nomad2(f):
                    counts["nomad2"] += 1
                    listed["nomad2"].append(f)
                elif nrg_sdr.looks_like_sdr(f):
                    counts["sdr"] += 1
                    listed["sdr"].append(f)
                elif windpro.looks_like_windpro(f):
                    counts["windpro"] += 1
                    listed["windpro"].append(f)
                else:
                    unread[suf] = unread.get(suf, 0) + 1
            except Exception:  # noqa: BLE001, S112  (an unreadable file is simply not counted)
                unread[suf] = unread.get(suf, 0) + 1
    fmt = None
    if counts["rld"] or counts["sympro_txt"]:
        fmt = "sympro"
    elif counts["toa5"]:
        fmt = "toa5"
    elif counts["nomad2"]:
        fmt = "nomad2"
    elif counts["sdr"]:
        fmt = "sdr"
    elif counts["windpro"]:
        fmt = "windpro"
    parts = [f"{counts['rld']:,} RLD" if counts["rld"] else "", f"{counts['sympro_txt']:,} SymphoniePRO TXT" if counts["sympro_txt"] else "",
             f"{counts['toa5']:,} TOA5 .dat" if counts["toa5"] else "", f"{counts['nomad2']:,} Nomad2 export" if counts["nomad2"] else "",
             f"{counts['sdr']:,} NRG Symphonie text export" if counts["sdr"] else "",
             f"{counts['windpro']:,} WindPRO export" if counts["windpro"] else "",
             f"{counts['archives']:,} archive{'s' if counts['archives'] != 1 else ''}" if counts["archives"] else "",
             f"{counts['rwd']:,} NRG Symphonie raw .RWD (not read)" if counts["rwd"] else ""]
    found = ", ".join(x for x in parts if x)
    if not found and unread:
        found = ("files in a format this version does not read ("
                 + ", ".join(f"{n:,} {suf}" for suf, n in sorted(unread.items(), key=lambda kv: -kv[1])) + ")")
    if blocked:
        found = (found + "; " if found else "") + (f"{blocked:,} file{'s' if blocked != 1 else ''} that could not be opened "
                                                    f"({'the first: ' if blocked != 1 else ''}{why_blocked})")
    families = [name for name, n in (("NRG SymphoniePRO", counts["rld"] + counts["sympro_txt"]),
                                     ("Campbell Scientific TOA5", counts["toa5"]), ("Nomad2 export", counts["nomad2"]),
                                     ("NRG Symphonie text export", counts["sdr"]), ("WindPRO export", counts["windpro"])) if n]
    return {"format": fmt, "label": FORMATS.get(fmt, "not recognised"), "counts": counts, "nomad2_files": listed["nomad2"],
            "read_files": listed.get(fmt, []), "mixed": families if len(families) > 1 else [],
            "found": found or "no data files at all", "files": len(files), "unread": unread, "blocked": blocked}


def folder_signature(path) -> str:
    """Changes when a logger file is added, removed or rewritten."""
    import hashlib  # noqa: PLC0415
    p = Path(path)
    rows = []
    if p.is_file():
        st = p.stat()
        rows.append(f"{p.name}|{st.st_size}|{int(st.st_mtime)}")
    else:
        for dirpath, dirs, names in os.walk(p):
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d.lower() not in ("_to_delete", "converted", "runs"))
            for n in sorted(names):
                if n.lower().endswith(DATA_SUFFIXES):
                    f = Path(dirpath) / n
                    try:
                        st = f.stat()
                    except OSError:
                        continue
                    rows.append(f"{f.relative_to(p).as_posix()}|{st.st_size}|{int(st.st_mtime)}")
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()[:20] + f":{len(rows)}"


def _plain(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", str(text)))).strip()


def run_checks(path, out_root, site_name: str | None = None, settings_path=None, log=print,
               sympro_path=None, password: str = "", channels: dict | None = None, clock: list | None = None) -> dict:
    """Run step 1 on a data folder. Outputs go to <out_root>/runs/<name>_<time>/. Returns summarise(run folder).
    channels: sensor heights and vane offsets by logger port or channel, for formats whose files do not carry them
    or carry them wrongly (Nomad2 ports such as A4, NRG Symphonie channels such as CH7).
    clock: logger clock corrections for periods a logger's clock was wrong (loggers.toa5_mast.clock_rules)."""
    path = paths.tidy(path)
    out_root = Path(out_root)
    if not path.exists():
        raise IntakeError(f"Not found: {path}{paths.too_long(path)}")
    # archives first: their logger files are unpacked beside the other outputs and read from there
    opened = None
    archives = unpack.find(path) if path.is_dir() else [path] if unpack.kind(path) else []
    if archives:
        from ..pipeline import safe_name  # noqa: PLC0415
        dest = out_root / "converted" / safe_name(path.name if path.is_dir() else unpack.stem(path))
        opened = unpack.unpack(archives, path if path.is_dir() else path.parent, dest, log=log, keep=_logger_text)
        if path.is_file() and dest.is_dir():   # an archive chosen by itself: what it held is the data folder
            path = dest
    det = detect(path, also=opened["dest"] if opened and opened["dest"] != path else None)
    if det["format"] is None:
        raw_only = (" The .RWD files are a Symphonie logger's raw files: convert them to text with NRG's free "
                    "Symphonie Data Retriever (Windows), then choose the folder with the .txt files."
                    if det["counts"]["rwd"] else "")
        from_archives = " " + unpack.failure(opened) if opened else ""
        raise IntakeError(f"No logger files were recognised here. This folder should hold one mast's files: {ACCEPTED}. "
                          f"Found: {det['found']}.{raw_only}{from_archives}")
    if det["mixed"]:
        raise IntakeError("This folder holds files of more than one kind (" + det["found"] + "). Choose the folder, or "
                          "the file, of one mast in one format.")
    log(f"Logger format: {det['label']} ({det['found']}).")
    if det["format"] == "sympro" and clock:
        raise IntakeError("Clock corrections are applied to Campbell TOA5 files and the text exports this version reads, "
                          "not yet to NRG SymphoniePRO files. Take them out to run step 1.")
    if det["format"] == "sympro":
        from .. import report as _report  # noqa: PLC0415
        from ..pipeline import PipelineError, run  # noqa: PLC0415
        try:
            out = run(path, out_root, sympro_path=sympro_path, password=password, site_name=site_name,
                      settings_path=settings_path, log=log)
        except PipelineError as e:
            raise IntakeError(str(e)) from e
        run_dir = Path(out["run_dir"])
        try:
            found = [_plain(x) for x in _report.findings(out["context"])]
        except Exception:  # noqa: BLE001
            found = []
        (run_dir / "findings.txt").write_text("\n".join(f"{i + 1}. {t}" for i, t in enumerate(found)) + "\n", encoding="utf-8")
    else:
        from ..loggers import toa5_mast  # noqa: PLC0415
        args = [str(f) for f in det["read_files"]] if det["format"] in FILE_LISTED else [str(path)]
        if det["counts"]["rwd"] and det["format"] == "sdr":
            log(f"  {det['counts']['rwd']:,} raw .RWD files were not read: only NRG's Symphonie Data Retriever opens them. "
                "Export them to text there to add their period.")
        args += ["--out", str(out_root)]
        if site_name:
            args += ["--name", site_name]
        if channels or clock:                  # this reader takes these in the same file as the site settings
            merged = json.loads(Path(settings_path).read_text(encoding="utf-8")) if settings_path and Path(settings_path).is_file() else {}
            if channels:
                merged["channels"] = channels
            if clock:
                merged["clock_corrections"] = clock
            settings_path = out_root / "runs" / "reader_settings.json"
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            settings_path.write_text(json.dumps(merged, indent=1), encoding="utf-8")
        if settings_path and Path(settings_path).is_file():
            args += ["--settings", str(settings_path)]
        try:
            run_dir = Path(toa5_mast.main(args, log=log))
        except SystemExit as e:
            raise IntakeError(str(e.code if e.code not in (None, 0) else "The reader stopped without a message.")) from e
        from .mast_report import write_report  # noqa: PLC0415
        try:
            write_report(run_dir)
        except Exception as e:  # noqa: BLE001  (the run's tables and figures are still there)
            log(f"Report page not written: {e}")
    out = summarise(run_dir)
    out["format"] = det["label"]
    if opened:
        out["archives"] = {"found": opened["archives"], "opened": opened["opened"], "logger_files": opened["files"],
                           "not_opened": [list(x) for x in opened["failed"]]}
        if opened["failed"]:                   # said where the findings are read, not only in the working log
            n, (name, why) = len(opened["failed"]), opened["failed"][0]
            out["findings"].insert(0, (f"The archive {name} in the data folder could not be opened ({why})" if n == 1 else
                                       f"{n:,} archives in the data folder could not be opened (the first, {name}: {why})")
                                   + ": the records they hold are missing from this check.")
    return out


def summarise(run_dir) -> dict:
    """Headline numbers of a finished run, from the files in its folder."""
    run_dir = Path(run_dir)
    out = {"run_dir": str(run_dir), "run_id": run_dir.name, "report": None, "excel": None, "climate": None,
           "findings": [], "figures": []}
    for key, name in (("report", "report.html"), ("excel", "summary.xlsx"), ("climate", "climate.json")):
        if (run_dir / name).is_file():
            out[key] = str(run_dir / name)
    f = run_dir / "findings.txt"
    if f.is_file():
        out["findings"] = [re.sub(r"^\d+\.\s*", "", ln).strip() for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if (run_dir / "flags.json").is_file():
        out["flags"] = json.loads((run_dir / "flags.json").read_text(encoding="utf-8"))
    if (run_dir / "figures").is_dir():
        out["figures"] = sorted(str(p) for p in (run_dir / "figures").glob("*.png"))
    if out["climate"]:
        c = _climate.load(out["climate"])
        out.update({
            "site": c["site"].get("name") or run_dir.name, "lat": c["site"].get("lat"), "lon": c["site"].get("lon"),
            "elevation_m": c["site"].get("elevation_m"), "period": c["period"], "records": c["records"],
            "recovery_pct": c["recovery_pct"], "top_height_m": c["height_m"], "mean_ws": c["mean_ws"],
            "momm_ws": c.get("momm_ws"), "calendar_months": c.get("calendar_months"),
            "shear_alpha": (c.get("shear") or {}).get("alpha"), "shear_basis": (c.get("shear") or {}).get("basis"),
            "shear_warning": (c.get("shear") or {}).get("warning") or "",
            "ti15": (c.get("ti") or {}).get("representative_15"), "ti_mean": (c.get("ti") or {}).get("mean_above_3"),
            "density": (c.get("density") or {}).get("mean"), "heights": c.get("heights") or [],
            "no_direction": c.get("no_direction", False), "format": c.get("source_format") or "",
            "months_usable": sum(1 for m in c["monthly"].values() if m["share"] >= _climate.MIN_MONTH_SHARE),
            "weibull_A": c.get("weibull_A"), "weibull_k": c.get("weibull_k")})
    out["disclaimer"] = DISCLAIMER
    out["version"] = __version__
    return out


def latest_run(out_root) -> Path | None:
    """Newest run folder that produced a wind climate."""
    runs = Path(out_root) / "runs"
    if not runs.is_dir():
        return None
    done = [p for p in runs.iterdir() if (p / "climate.json").is_file()]
    return max(done, key=lambda p: (p / "climate.json").stat().st_mtime) if done else None


def save_summary(path, summary: dict) -> None:
    Path(path).write_text(json.dumps(summary, indent=1, default=str), encoding="utf-8")
