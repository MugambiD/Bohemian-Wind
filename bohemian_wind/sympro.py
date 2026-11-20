"""Reader for NRG SymphoniePRO measurement exports (SymPRO Desktop or NRG Cloud text files).

File layout: a preamble, then the sections Export Parameters, Site Properties,
Logger History, iPack History and Sensor History as tab-separated "Key:<TAB>Value"
lines, then a line "Data" followed by a tab-separated table. Data columns are named
Ch<n>_<Type>_<Height>m_<Bearing>_<Stat>_<Units>, e.g. Ch1_Anem_80.00m_N_Avg_m/s.
"""
from __future__ import annotations

import io
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

SECTIONS = ("Export Parameters", "Site Properties", "Logger History", "iPack History", "Sensor History")
ENCODINGS = ("utf-8-sig", "cp1252", "latin-1")
SENTINELS = (-9999.0, 9999.0, -99999.0, 99999.0)
MAX_SITE_SPREAD_M = 1000.0   # files further apart than this come from different masts
SAME_PLACE_M = 50.0          # closer than this: the same position written slightly differently
CHANNEL_FIELDS = ("Effective Date", "Type", "Description", "Serial Number", "Height", "Bearing",
                  "Vane Mounting Angle", "Scale Factor", "Offset", "Units", "Total Direction Offset")


@dataclass
class ColumnInfo:
    name: str
    channel: int
    ch_type: str
    height: float | None
    bearing: str
    stat: str
    units: str


def parse_column(name: str) -> ColumnInfo | None:
    parts = name.split("_", 5)
    if len(parts) != 6 or not re.fullmatch(r"Ch\d+", parts[0]):
        return None
    h = parts[2].strip()
    height = None
    if h.lower().endswith("m"):
        try:
            height = float(h[:-1])
        except ValueError:
            height = None
    return ColumnInfo(name, int(parts[0][2:]), parts[1], height, parts[3], parts[4], parts[5])


@dataclass
class ExportFile:
    path: Path
    preamble: dict
    site: dict
    loggers: list
    ipacks: list
    channels: list
    data: pd.DataFrame


@dataclass
class Dataset:
    files: list
    data: pd.DataFrame
    columns: dict
    site: dict
    loggers: list
    ipacks: list
    channel_history: pd.DataFrame
    duplicates_removed: int = 0
    read_errors: list = field(default_factory=list)
    source_format: str = ""
    # per record: index into file_info of the file it was read from
    source: pd.Series | None = None
    # per file read: {"file": name, "channels": {ch: Sensor History fields}, "prefixes": {ch: column stem}}
    file_info: list = field(default_factory=list)
    # set by points.build: raw logger columns (data then holds one column per measurement point)
    raw: pd.DataFrame | None = None
    points: dict = field(default_factory=dict)
    # records that share a timestamp but disagree (e.g. part-interval records written when a file
    # restarts during a site visit): the timestamp is kept with every value blanked
    conflict_times: pd.DatetimeIndex = field(default_factory=lambda: pd.DatetimeIndex([]))
    conflict_rows: int = 0
    conflicts: pd.DataFrame = field(default_factory=pd.DataFrame)
    # location and clock as written in each file: see site_consistency()
    site_checks: dict = field(default_factory=dict)
    # site properties as logged, before any site settings file was applied
    site_logged: dict = field(default_factory=dict)
    settings: dict = field(default_factory=dict)
    # logger clock corrections set in the project, as applied: [{"from", "to", "minutes", "program", "note", "records"}]
    clock_corrections: list = field(default_factory=list)


def _read_lines(path) -> list[str]:
    raw = Path(path).read_bytes()
    for enc in ENCODINGS:
        try:
            return raw.decode(enc).splitlines()
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace").splitlines()


def _kv(line: str) -> tuple[str, str]:
    key, _, value = line.partition("\t")
    return key.strip().rstrip(":").strip(), value.strip()


def read_export(path) -> ExportFile:
    lines = _read_lines(path)
    try:
        data_idx = next(i for i, ln in enumerate(lines) if ln.strip() == "Data")
    except StopIteration:
        raise ValueError(f"{Path(path).name}: no 'Data' line, so not a SymphoniePRO measurement export")
    preamble, site, loggers, ipacks, channels = {}, {}, [], [], []
    section, current = None, None
    for ln in lines[:data_idx]:
        s = ln.strip()
        if not s:
            continue
        if s in SECTIONS:
            section, current = s, None
            continue
        key, value = _kv(ln)
        if section in (None, "Export Parameters"):
            preamble[key] = value
        elif section == "Site Properties":
            site[key] = value
        elif section in ("Logger History", "iPack History"):
            target = loggers if section == "Logger History" else ipacks
            if key == "Date" or not target:
                target.append({})
            target[-1][key] = value
        elif section == "Sensor History":
            if key == "Channel":
                current = {"Channel": value}
                channels.append(current)
            elif current is not None:
                current[key] = value
    if "Site Number" not in site and preamble.get("Site Number"):
        site["Site Number"] = preamble["Site Number"]
    table = "\n".join(lines[data_idx + 1:])
    df = pd.read_csv(io.StringIO(table), sep="\t", na_values=["", "NaN", "nan", "NA", "-"],
                     keep_default_na=False, low_memory=False)
    df = df.loc[:, [c for c in df.columns if not str(c).startswith("Unnamed")]]
    if "Timestamp" not in df.columns:
        raise ValueError(f"{Path(path).name}: data table has no Timestamp column")
    ts = pd.to_datetime(df["Timestamp"], format="%Y-%m-%d %H:%M:%S", errors="coerce")
    if ts.isna().mean() > 0.5:  # other export date formats
        ts = pd.to_datetime(df["Timestamp"], errors="coerce", dayfirst=False)
    df = df.drop(columns="Timestamp")
    for c in df.columns:
        if df[c].dtype == object:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df.index = pd.DatetimeIndex(ts, name="Timestamp")
    df = df[df.index.notna()]
    return ExportFile(Path(path), preamble, site, loggers, ipacks, channels, df)


def looks_like_export(path) -> bool:
    try:
        with open(path, "rb") as fh:
            head = fh.read(256 * 1024)
    except OSError:
        return False
    return b"Timestamp" in head and (b"\nData\n" in head or b"\nData\r\n" in head)


def find_exports(folder) -> list[Path]:
    """All .txt files under folder that look like SymphoniePRO measurement exports."""
    out = []
    for dirpath, _dirs, files in os.walk(folder):
        for f in files:
            if f.lower().endswith(".txt") and looks_like_export(Path(dirpath) / f):
                out.append(Path(dirpath) / f)
    return sorted(out)


def column_stem(name: str) -> str:
    """Ch1_Anem_80.00m_W_Avg_m/s -> Ch1_Anem_80.00m_W (channel, type, height and boom)."""
    return "_".join(str(name).split("_", 5)[:4])


def _channel_configs(ef: ExportFile) -> dict:
    out = {}
    for ch in ef.channels:
        try:
            n = int(str(ch.get("Channel", "")).strip())
        except ValueError:
            continue
        out[n] = {k: ch.get(k, "") for k in CHANNEL_FIELDS}
    return out


def _float(v):
    try:
        x = float(str(v).strip())
    except (TypeError, ValueError):
        return None
    return x if np.isfinite(x) else None


def distance_m(a, b) -> float:
    """Great-circle distance between two (lat, lon) points, metres."""
    la1, lo1, la2, lo2 = map(np.radians, (a[0], a[1], b[0], b[1]))
    h = np.sin((la2 - la1) / 2) ** 2 + np.cos(la1) * np.cos(la2) * np.sin((lo2 - lo1) / 2) ** 2
    return float(2 * 6371008.8 * np.arcsin(min(1.0, np.sqrt(h))))


def _plural(n, word):
    return f"{n:,} {word}{'s' if n != 1 else ''}"


def site_consistency(per_file: list) -> dict:
    """Check that every file comes from one mast and one clock.

    per_file: one dict per file with the Site Properties as written (keys file, lat, lon, tz,
    site_number). Raises ValueError when the folder mixes masts: different site numbers,
    coordinates more than MAX_SITE_SPREAD_M apart (two masts often share the default site
    number 000001), or different logger time zones (their timestamps cannot be merged).
    Files without coordinates, or with 0, 0, are not used for the location check.
    """
    from collections import Counter
    coords, tzs, nums = Counter(), Counter(), Counter()
    for f in per_file:
        lat, lon = _float(f.get("lat")), _float(f.get("lon"))
        if f.get("tz"):
            tzs[f["tz"]] += 1
        if f.get("site_number"):
            nums[f["site_number"]] += 1
        if lat is None or lon is None or (lat == 0 and lon == 0) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        coords[(round(lat, 7), round(lon, 7))] += 1
    distinct = list(coords)
    spread = max((distance_m(a, b) for i, a in enumerate(distinct) for b in distinct[i + 1:]), default=0.0)
    checks = {"files": len(per_file), "coordinates": [(a, b, n) for (a, b), n in coords.most_common()],
              "files_without_coordinates": len(per_file) - sum(coords.values()), "spread_m": spread,
              "time_zones": tzs.most_common(), "site_numbers": nums.most_common()}
    if len(nums) > 1:
        raise ValueError("The folder holds data from more than one site (site numbers "
                         + ", ".join(sorted(nums)) + "). Select one mast's folder.")
    if spread > MAX_SITE_SPREAD_M:
        where = "; ".join(f"{a:.5f}, {b:.5f} in {_plural(n, 'file')}" for (a, b), n in coords.most_common(4))
        raise ValueError(f"The files come from places up to {spread / 1000:.1f} km apart ({where}), so the folder "
                         "mixes masts. Select one mast's folder. If this mast was moved, analyse each position "
                         "separately.")
    if len(tzs) > 1:
        zones = "; ".join(f"{z} in {_plural(n, 'file')}" for z, n in tzs.most_common())
        raise ValueError(f"The files use different logger time zones ({zones}), so their timestamps cannot be "
                         "merged safely. Check the logger clock history and analyse each period separately.")
    return checks


def merge_duplicates(data: pd.DataFrame, names: list) -> tuple:
    """Records that share a timestamp, from overlapping files.

    Where every value agrees (or is blank in all but one record) the copies are one record (agreeing means to
    within one unit in the 7th significant digit, the most a logger's text export writes):
    the most complete copy is kept. Where values disagree, none can be trusted; a logger that
    restarts a file mid-interval (site visits) writes a part-interval record. Those timestamps
    are kept once with every value blanked, so the checks can flag them as conflicts.
    data carries the column _src (index into names). Columns whose names start with an underscore are
    bookkeeping (the file a record came from, the logger program its file's header names) and are not
    compared: one record copied into files with different headers is still one record.
    Returns (data, identical_removed, conflict_times, conflict_rows, table of conflicts).
    """
    data = data.sort_index(kind="stable")
    empty = pd.DataFrame(columns=["timestamp", "records", "files", "differing_columns", "largest_difference"])
    dup = data.index.duplicated(keep=False)
    if not dup.any():
        return data, 0, pd.DatetimeIndex([]), 0, empty
    vals = data.drop(columns=[c for c in data.columns if str(c).startswith("_")])
    d = vals[dup]
    grp = d.groupby(level=0)
    hi, lo = grp.max(), grp.min()
    # copies of a record written out by different programs can round the 7th significant digit differently
    tol = 1e-6 + 2e-6 * np.maximum(hi.abs(), lo.abs())
    differs = (hi - lo) > tol  # NaN compares False: a value present in one copy only does not conflict
    bad = differs.any(axis=1)
    conflict_times = pd.DatetimeIndex(bad.index[bad])
    n_values = vals.notna().sum(axis=1).to_numpy()
    order = np.lexsort((np.arange(len(data)), -n_values, data.index.asi8))
    srt = data.iloc[order]
    out = srt[~srt.index.duplicated(keep="first")]
    conflict_rows = int(data.index.isin(conflict_times).sum())
    identical_removed = (len(data) - len(out)) - (conflict_rows - len(conflict_times))
    rows = []
    for t in conflict_times[:500]:
        part = data.loc[[t]]
        cols = differs.columns[differs.loc[t].to_numpy()]
        avg = [c for c in cols if "_Avg_" in str(c)] or list(cols)  # averages tell the reader most
        span = hi.loc[t, avg] - lo.loc[t, avg]
        rel = (span / np.maximum(np.maximum(hi.loc[t, avg].abs(), lo.loc[t, avg].abs()), 1.0)).sort_values(ascending=False)
        c = rel.index[0]
        rows.append({"timestamp": t, "records": len(part),
                     "files": ", ".join(dict.fromkeys(names[int(i)] for i in part["_src"])),
                     "differing_columns": len(cols),
                     "largest_difference": f"{c}: {lo.loc[t, c]:g} to {hi.loc[t, c]:g}"})
    if len(conflict_times):
        value_cols = [c for c in out.columns if not str(c).startswith("_")]
        out = out.copy()
        out.loc[out.index.isin(conflict_times), value_cols] = np.nan
    return out, int(identical_removed), conflict_times, conflict_rows, pd.DataFrame(rows, columns=empty.columns)


def read_dataset(paths, log=print, progress=None) -> Dataset:
    frames, history, errors, per_file, file_info = [], [], [], [], []
    site, loggers, ipacks, fmt = {}, [], [], ""
    paths = list(paths)
    for i, p in enumerate(paths):
        try:
            ef = read_export(p)
        except Exception as e:  # keep going; report the file
            errors.append(f"{Path(p).name}: {e}")
            continue
        prefixes = {}
        for c in ef.data.columns:
            info = parse_column(str(c))
            if info is not None:
                prefixes.setdefault(info.channel, column_stem(c))
        ef.data["_src"] = len(file_info)
        file_info.append({"file": ef.path.name, "channels": _channel_configs(ef), "prefixes": prefixes})
        frames.append(ef.data)
        site = ef.site or site
        per_file.append({"file": ef.path.name, "lat": ef.site.get("Latitude"), "lon": ef.site.get("Longitude"),
                         "tz": ef.site.get("Time Zone") or ef.preamble.get("Time Zone", ""),
                         "site_number": ef.site.get("Site Number", "")})
        fmt = next(iter(ef.preamble), fmt) if ef.preamble else fmt
        for src, dst in ((ef.loggers, loggers), (ef.ipacks, ipacks)):
            for item in src:  # the same unit is repeated in every file with a new Date
                key = {k: v for k, v in item.items() if k != "Date"}
                if all({k: v for k, v in x.items() if k != "Date"} != key for x in dst):
                    dst.append(item)
        for ch in ef.channels:
            row = {"file": ef.path.name, "Channel": ch.get("Channel")}
            row.update({k: ch.get(k, "") for k in CHANNEL_FIELDS})
            history.append(row)
        if progress and (i % 25 == 0 or i == len(paths) - 1):
            progress(i + 1, len(paths))
    if not frames:
        raise ValueError("No readable SymphoniePRO measurement exports found. " + "; ".join(errors[:3]))
    checks = site_consistency(per_file)
    data = pd.concat(frames, axis=0, join="outer", sort=False)
    value_cols = [c for c in data.columns if c != "_src"]
    data[value_cols] = data[value_cols].replace(list(SENTINELS), np.nan)
    data, removed, conflict_times, conflict_rows, conflicts = merge_duplicates(data, [f["file"] for f in file_info])
    source = data.pop("_src").astype(int)
    columns = {c: info for c in data.columns if (info := parse_column(str(c))) is not None}
    hist = pd.DataFrame(history)
    if errors:
        log(f"  {len(errors)} file(s) could not be read; see the report.")
    return Dataset(files=[Path(p) for p in paths], data=data, columns=columns, site=site,
                   loggers=loggers, ipacks=ipacks, channel_history=hist,
                   duplicates_removed=removed, read_errors=errors, source_format=fmt,
                   source=source, file_info=file_info, conflict_times=conflict_times,
                   conflict_rows=conflict_rows, conflicts=conflicts, site_checks=checks,
                   site_logged=dict(site))


def channel_changes(hist: pd.DataFrame) -> pd.DataFrame:
    """One row per distinct configuration of each channel, oldest first."""
    if hist.empty:
        return hist
    keys = ["Channel"] + [k for k in CHANNEL_FIELDS if k != "Effective Date"]
    h = hist.copy()
    h["Effective Date"] = pd.to_datetime(h["Effective Date"], errors="coerce")
    h = h.sort_values(["Channel", "Effective Date", "file"])
    g = h.groupby(keys, dropna=False, sort=False)
    out = g.agg(first_effective=("Effective Date", "min"), first_file=("file", "first"),
                files=("file", "nunique")).reset_index()
    out["ch_num"] = pd.to_numeric(out["Channel"], errors="coerce")
    out = out.sort_values(["ch_num", "first_effective"]).drop(columns="ch_num")
    counts = out.groupby("Channel")["Channel"].transform("size")
    out["changed"] = counts > 1
    return out.reset_index(drop=True)
