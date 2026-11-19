"""One run: convert RLD (if needed), read exports, check, analyse, write outputs."""
from __future__ import annotations

import csv
import hashlib
import io
import re
import time
from datetime import datetime
from pathlib import Path

from . import APP_NAME, __version__
from . import settings as site_settings
from .analysis import analyse
from .points import VISIT_PAD
from .convert import convert_tree, find_rld_folders, find_sympro, is_sympro_running, pending_rld
from .qc import build_sensors, flag_periods, flag_summary, recovery_by_month, run_qc
from .report import write_excel, write_html, write_meta, write_timeseries
from .sympro import channel_changes, find_exports, read_dataset


class PipelineError(Exception):
    """A problem the user can fix; the message says how."""


def safe_name(s: str) -> str:
    s = re.sub(r"[^\w\-. ]+", "_", str(s)).strip().strip(".")
    return re.sub(r"\s+", "_", s)[:60] or "site"


def new_run_folder(output_root, name: str) -> Path:
    """runs/<name>_<date-time>, made here. Two runs started within the same second get folders of their own."""
    from .folders import fresh_folder  # noqa: PLC0415
    return fresh_folder(Path(output_root) / "runs", f"{safe_name(name)}_{datetime.now():%Y%m%d-%H%M%S}", sep="-")


def default_output(input_dir) -> Path:
    return Path(input_dir).parent / "Bohemian Wind QC outputs"


def default_settings(input_dir, output_root=None) -> Path:
    """Where a run looks for the site settings file of this data folder."""
    root = Path(output_root) if output_root else default_output(input_dir)
    return root / site_settings.FOLDER / f"{safe_name(Path(input_dir).name)}.json"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_inventory(path: Path, input_dir: Path, converted_dir: Path, txt_files, output_root: Path) -> dict:
    """SHA-256 of every file in the data folder and of every converted export read, so a result
    can be traced to its exact inputs. The inventory's own hash fingerprints the whole set."""
    rows, skip = [], Path(output_root).resolve()
    # outputs kept inside the data folder are not inputs; a data folder kept inside a project folder is all input
    data_inside_outputs = Path(input_dir).resolve().is_relative_to(skip)
    for p in sorted(input_dir.rglob("*")):
        if not p.is_file():
            continue
        try:
            if not data_inside_outputs and p.resolve().is_relative_to(skip):
                continue
        except OSError:
            continue
        rows.append(("data folder", p.relative_to(input_dir).as_posix(), p.stat().st_size, _sha256(p)))
    conv = converted_dir.resolve() if converted_dir.exists() else None
    for p in txt_files:
        if conv is not None and p.resolve().is_relative_to(conv):
            rows.append(("converted export", p.resolve().relative_to(conv).as_posix(), p.stat().st_size, _sha256(p)))
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["role", "file", "bytes", "sha256"])
    w.writerows(rows)
    text = buf.getvalue()
    # bytes, not text mode: Windows would turn \n into \r\n and the file would no longer match its fingerprint
    path.write_bytes(text.encode("utf-8"))
    return {"file": path.name, "files": len(rows), "bytes": sum(r[2] for r in rows),
            "data_folder_files": sum(r[0] == "data folder" for r in rows),
            "converted_exports": sum(r[0] == "converted export" for r in rows),
            "inventory_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}


def run(input_dir, output_root=None, sympro_path=None, password: str = "", convert: bool = True,
        export_timeseries: bool = True, site_name: str | None = None, settings_path=None,
        log=print, progress=None) -> dict:
    t0 = time.time()
    input_dir = Path(input_dir)
    if not input_dir.is_dir():
        raise PipelineError(f"Folder not found: {input_dir}")
    output_root = Path(output_root) if output_root else default_output(input_dir)
    run_dir = new_run_folder(output_root, input_dir.name)
    log_path = run_dir / "run.log"

    def say(msg: str):
        # written line by line, so a run that fails still leaves its log
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now():%H:%M:%S}  {msg}\n")
        log(msg)

    say(f"{APP_NAME} v{__version__}")
    say(f"Input:  {input_dir}")
    say(f"Output: {run_dir}")
    converted_dir = output_root / "converted" / safe_name(input_dir.name)
    rld_groups = find_rld_folders(input_dir)
    n_rld = sum(len(v) for v in rld_groups.values())
    txt_in = find_exports(input_dir)
    say(f"Found {n_rld} RLD files in {len(rld_groups)} folders and {len(txt_in)} TXT files.")
    conversion = None
    if n_rld and convert and not pending_rld(input_dir, converted_dir):
        say(f"All {n_rld} RLD files were converted in an earlier run; SymPRO Desktop is not needed this time.")
        conversion = convert_tree(input_dir, converted_dir, sympro_exe="", log=lambda m: None)
        say(f"  {conversion['txt_files']} TXT files available; {len(conversion['failure_logs'])} conversion failure logs.")
    elif n_rld and convert:
        exe = find_sympro(sympro_path)
        already = list(converted_dir.rglob("*.txt")) if converted_dir.exists() else []
        if not exe:
            if not txt_in and not already:
                raise PipelineError(
                    "RLD files need NRG SymphoniePRO Desktop to convert them, and it was not found. "
                    "Install it (free from NRG Systems) or set its path, then run again. "
                    "Alternatively export the RLD files to TXT in SymPRO Desktop and select that folder.")
            say("SymPRO Desktop not found: using TXT files already available.")
        else:
            if is_sympro_running():
                raise PipelineError("SymPRO Desktop is open. Close it, then run again (its converter cannot run alongside the window).")
            say(f"Converting with {exe}")
            say(f"Converted TXT files go to {converted_dir}")
            conversion = convert_tree(input_dir, converted_dir, exe, password=password, log=say)
            for e in conversion["errors"]:
                say(f"  WARNING {e}")
            say(f"  {conversion['txt_files']} TXT files available; {len(conversion['failure_logs'])} conversion failure logs.")
    # every export in the input folder, plus converted files not already exported there
    txt_files = list(txt_in)
    seen = {p.name.lower() for p in txt_in}
    if converted_dir.exists():
        txt_files += [p for p in sorted(converted_dir.rglob("*.txt")) if p.name.lower() not in seen]
    if not txt_files:
        raise PipelineError("No TXT exports to read. If the RLD files are password protected, enter the data password.")

    say(f"Reading {len(txt_files)} export files ...")
    try:
        ds = read_dataset(txt_files, log=say, progress=progress)
    except ValueError as e:
        raise PipelineError(str(e)) from e
    say(f"  {len(ds.data):,} records, {ds.data.index[0]:%Y-%m-%d} to {ds.data.index[-1]:%Y-%m-%d}; "
        f"{len(ds.columns)} data columns; {ds.duplicates_removed:,} repeated records merged.")
    if len(ds.conflict_times):
        say(f"  {len(ds.conflict_times):,} timestamps where overlapping files disagree ({ds.conflict_rows:,} records): "
            "quarantined, flagged 'conflict'.")
    chk = ds.site_checks
    if chk.get("coordinates"):
        lat, lon, n = chk["coordinates"][0]
        where = (f"{'all ' if n > 1 else ''}{n:,} file{'s' if n != 1 else ''} with coordinates: {lat:.6f}, {lon:.6f}"
                 if len(chk["coordinates"]) == 1 else
                 f"{len(chk['coordinates'])} positions in the files, up to {chk['spread_m']:,.0f} m apart")
        zones = ", ".join(z for z, _ in chk.get("time_zones", [])) or "not given"
        say(f"  Location: {where}; logger time zone {zones}.")
    settings_file = Path(settings_path) if settings_path else default_settings(input_dir, output_root)
    if settings_path or settings_file.is_file():
        try:
            notes = site_settings.apply(ds, site_settings.load(settings_file), settings_file)
        except site_settings.SettingsError as e:
            raise PipelineError(str(e)) from e
        say(f"Site settings from {settings_file}:")
        for n in notes:
            say(f"  {n}")
    say("Fingerprinting input files ...")
    inputs = write_inventory(run_dir / "inputs.csv", input_dir, converted_dir, txt_files, output_root)
    say(f"  {inputs['files']:,} file{'s' if inputs['files'] != 1 else ''}, SHA-256 in inputs.csv; "
        f"input set fingerprint {inputs['inventory_sha256'][:16]}.")
    sensors = build_sensors(ds)
    say("Measurement points: " + ", ".join(s.label for s in sensors if s.kind != "other"))
    pts = ds.points
    for a, b, n in pts.get("visits", []):
        when = (f"at {a + VISIT_PAD:%Y-%m-%d %H:%M}" if b - a <= 2 * VISIT_PAD else
                f"between {a + VISIT_PAD:%Y-%m-%d %H:%M} and {b - VISIT_PAD:%Y-%m-%d %H:%M}")
        say(f"  Site visit: {n} channel setting change{'s' if n != 1 else ''} {when}; records from "
            f"{a:%Y-%m-%d %H:%M} to {b:%Y-%m-%d %H:%M} flagged as maintenance.")
    if pts.get("moved"):
        say("  Sensors that changed logger channel, merged by serial number: " + ", ".join(pts["moved"]))
    if pts.get("rescaled"):
        say(f"  {pts['rescaled']} earlier configurations re-expressed with the latest calibration of the same sensor.")
    if pts.get("excluded"):
        say("  Not analysed (temporary configurations, under a day of data): " + ", ".join(pts["excluded"]))
    say("Running quality checks ...")
    qcr = run_qc(ds, sensors)
    say("Analysing wind statistics ...")
    an = analyse(ds, qcr, sensors)
    name = (site_name or ds.settings.get("values", {}).get("site_name") or ds.site.get("Site Description")
            or ds.site.get("Site Number") or input_dir.name)
    ctx = {"ds": ds, "qc": qcr, "an": an, "sensors": sensors, "site_name": name, "input": input_dir, "inputs": inputs,
           "generated": f"{datetime.now():%Y-%m-%d %H:%M}", "conversion": conversion,
           "recovery": recovery_by_month(qcr, sensors), "flag_summary": flag_summary(qcr),
           "flag_periods": flag_periods(qcr), "changes": channel_changes(ds.channel_history),
           "log_path": log_path}
    say("Writing report ...")
    out = {"run_dir": run_dir, "report": run_dir / "report.html", "excel": run_dir / "summary.xlsx"}
    write_html(out["report"], ctx)
    write_excel(out["excel"], ctx)
    if export_timeseries:
        out["timeseries"] = run_dir / "timeseries_with_flags.csv"
        write_timeseries(out["timeseries"], ctx)
    write_meta(run_dir / "run.json", ctx)
    (run_dir / "flags.json").write_text(ctx["flag_summary"].to_json(orient="records"), encoding="utf-8")
    try:  # hand-off for the later steps (long-term, maps, layout, yield); never stops a run
        from . import climate as _climate  # noqa: PLC0415
        _climate.save(run_dir / "climate.json", _climate.from_analysis(
            an, qcr["time"], {"name": name, "lat": ds.site.get("Latitude"), "lon": ds.site.get("Longitude"),
                              "elevation_m": ds.site.get("Elevation")}, source_format=ds.source_format or "NRG SymphoniePRO"))
    except Exception as e:  # noqa: BLE001
        say(f"Wind climate file not written: {e}")
    say(f"Done in {time.time() - t0:.0f} s. Report: {out['report']}")
    out["context"] = ctx
    return out
