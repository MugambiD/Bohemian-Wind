"""Step 1 entry point: recognise the logger format in a folder, run the quality checks and statistics, and
return one summary whatever the format.

Formats: NRG SymphoniePRO (RLD files, converted with NRG's free SymPRO Desktop on Windows, or its TXT exports),
Campbell Scientific TOA5 (.dat) and SecondWind Nomad2 Multi-Track exports.
"""
from __future__ import annotations

import html
import importlib.util
import json
import os
import re
from pathlib import Path

from .. import DISCLAIMER, __version__
from .. import climate as _climate

FORMATS = {"sympro": "NRG SymphoniePRO", "toa5": "Campbell Scientific TOA5", "nomad2": "SecondWind Nomad2 export"}
DATA_SUFFIXES = (".rld", ".txt", ".dat", ".csv", ".rar")
SCAN_LIMIT = 20000


class IntakeError(RuntimeError):
    """A problem the user can fix; the message says how."""


def _is_toa5(path: Path) -> bool:
    try:
        with open(path, "rb") as fh:
            return fh.read(8).lstrip(b"\xef\xbb\xbf").startswith(b'"TOA5"')
    except OSError:
        return False


def detect(path) -> dict:
    """What a folder (or a single file) holds. Returns format (a FORMATS key or None), counts and, for Nomad2,
    the files to read."""
    from ..loggers import nomad2  # noqa: PLC0415
    from ..sympro import looks_like_export  # noqa: PLC0415
    p = Path(path)
    if not p.exists():
        raise IntakeError(f"Not found: {p}")
    files = [p] if p.is_file() else []
    if p.is_dir():
        for dirpath, dirs, names in os.walk(p):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d.lower() not in ("_to_delete", "converted", "runs")]
            files += [Path(dirpath) / n for n in names if n.lower().endswith(DATA_SUFFIXES)]
            if len(files) > SCAN_LIMIT:
                break
    counts = {"rld": 0, "sympro_txt": 0, "toa5": 0, "nomad2": 0, "rar": 0}
    nomad_files = []
    probed = 0
    for f in files:
        suf = f.suffix.lower()
        if suf == ".rld":
            counts["rld"] += 1
        elif suf == ".rar":
            counts["rar"] += 1
        elif suf == ".dat" and _is_toa5(f):
            counts["toa5"] += 1
        elif probed < 400:                     # text exports are told apart by their first lines; cap the probing
            probed += 1
            try:
                if suf == ".txt" and looks_like_export(f):
                    counts["sympro_txt"] += 1
                elif nomad2.looks_like_nomad2(f):
                    counts["nomad2"] += 1
                    nomad_files.append(f)
            except Exception:  # noqa: BLE001, S112  (an unreadable file is simply not counted)
                continue
    fmt = None
    if counts["rld"] or counts["sympro_txt"]:
        fmt = "sympro"
    elif counts["toa5"] or (counts["rar"] and not counts["nomad2"]):
        fmt = "toa5"
    elif counts["nomad2"]:
        fmt = "nomad2"
    parts = [f"{counts['rld']:,} RLD" if counts["rld"] else "", f"{counts['sympro_txt']:,} SymphoniePRO TXT" if counts["sympro_txt"] else "",
             f"{counts['toa5']:,} TOA5 .dat" if counts["toa5"] else "", f"{counts['nomad2']:,} Nomad2 export" if counts["nomad2"] else "",
             f"{counts['rar']:,} RAR archive" if counts["rar"] else ""]
    found = ", ".join(x for x in parts if x)
    return {"format": fmt, "label": FORMATS.get(fmt, "not recognised"), "counts": counts, "nomad2_files": nomad_files,
            "found": found or "no logger files", "files": len(files)}


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
               sympro_path=None, password: str = "") -> dict:
    """Run step 1 on a data folder. Outputs go to <out_root>/runs/<name>_<time>/. Returns summarise(run folder)."""
    det = detect(path)
    if det["format"] is None:
        raise IntakeError(
            "No logger files were recognised here. This folder should hold one mast's files: NRG SymphoniePRO "
            "(.rld or exported .txt), Campbell Scientific TOA5 (.dat) or a SecondWind Nomad2 export. "
            f"Found: {det['found']}.")
    log(f"Logger format: {det['label']} ({det['found']}).")
    out_root = Path(out_root)
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
        args = [str(f) for f in det["nomad2_files"]] if det["format"] == "nomad2" else [str(path)]
        args += ["--out", str(out_root)]
        if site_name:
            args += ["--name", site_name]
        if settings_path and Path(settings_path).is_file():
            args += ["--settings", str(settings_path)]
        if det["counts"]["rar"] and det["format"] == "toa5":
            if importlib.util.find_spec("unrar") is None:
                if not det["counts"]["toa5"]:
                    raise IntakeError("This folder holds RAR archives only. Extract them into the folder (or install the "
                                      "unrar2-cffi Python package) and run again.")
                log(f"  {det['counts']['rar']:,} RAR archives were not opened (the unrar2-cffi package is not installed); "
                    "reading the .dat files only.")
            else:
                args.append("--extract")
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
