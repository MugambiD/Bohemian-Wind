"""Campbell Scientific TOA5 met mast data: reader and screening run.

Prototype for Bohemian Wind QC v0.2. The v0.1.3 program (.exe) reads NRG SymphoniePRO exports only; this
script reads Campbell Scientific TOA5 files and runs the same checks and statistics from the source code.

  python tools/toa5_mast.py <data folder> [<data folder> ...] --out "<Bohemian Wind QC outputs>" --name "My mast"
         [--settings <site settings .json>] [--extract] [--power-daily <.json> --power-monthly <.json>]

Data folders: every *.dat TOA5 file under them is read (CR1000X, CR6 and other Campbell loggers, e.g. the daily
DATA_10MIN<n>.dat files). With --extract, RAR archives in the first folder are first extracted to
<out>/converted/<folder name>/ (needs the unrar2-cffi package) and that folder is read too.
Column names carry the sensor and its logged height: V1_124m_Avg is cup V1 at 124 m, D1_117m_Std the standard
deviation of vane D1 at 117 m; TA, RH and PA are temperature, humidity and pressure.

Rules
- One measurement point per sensor and logged height. Where a sensor's logged height changes between logger
  programs, each height is its own point. A point with less than 60 days of data, at a height no other sensor of
  its kind measures, is listed and checked but left out of the wind statistics.
- Repeated records in overlapping files are merged; records that disagree are quarantined (as in v0.1.3).
- Site visits: logger program changes and part-interval records (under 95% of the full sample count), grouped
  when less than 24 h apart; records from 1 h before to 1 h after are flagged as maintenance.
- The v0.1.3 checks and statistics run unchanged (bohemian_wind.qc.run_qc, bohemian_wind.analysis.analyse).
- Two checks are added for loggers that keep writing while a sensor is dead:
  * cup or vane outage: standard deviation exactly 0 (cups: or a reading below 0.5 m/s, the cup's offset) for 6 h
    or more while the other cups read 3 m/s or more;
  * vane fixed at one reading: 10-minute averages within 1 deg for 6 h or more while the cups read 3 m/s or more.
  Either flags the whole still spell as 'stuck', calm hours included.
- Monthly means count only months with at least 70% valid records at that height.
- Long-term index (optional): NASA POWER wind speed at 50 m (MERRA-2; POWER appends GEOS-IT for the latest
  weeks), by tools/windmap.long_term.
"""
from __future__ import annotations

import argparse
import base64
import csv
import json
import math
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from .. import DISCLAIMER
from .. import __version__ as QC_VERSION
from .. import report as rp
from ..analysis import analyse, rose, sector, ti_bins, weibull_fit
from ..pipeline import safe_name, write_inventory
from ..points import Sensor
from ..qc import FLAG_ORDER, _runs, flag_periods, flag_summary, recovery_by_month, run_qc
from ..sympro import Dataset, distance_m, merge_duplicates
from . import nomad2  # SecondWind Nomad2 Multi-Track exports, read into the same shape as TOA5

TOOLS = Path(__file__).resolve().parents[2] / "tools"     # the atlas prototype (windmap.py) still lives there


def _windmap():
    if str(TOOLS) not in sys.path:
        sys.path.insert(0, str(TOOLS))
    import windmap  # noqa: PLC0415
    return windmap

READER = "toa5_mast.py 0.2.1-dev"
COL = re.compile(r"^(?P<sid>[A-Za-z]+\d*)_(?P<h>\d+(?:\.\d+)?)m_(?P<stat>Avg|Std|Min|Max)$")
KINDS = {"V": ("wind_speed", "Anem", "WS"), "D": ("wind_direction", "Vane", "WD"),
         "TA": ("temperature", "Temp", "T"), "RH": ("humidity", "RH", "RH"), "PA": ("pressure", "Baro", "P")}
EXTRA = {"U_Batt": ("voltage", "Batt", "Battery voltage"), "PTemp": ("other", "Panel", "Logger panel temperature")}
STATS = {"Avg": "Avg", "Std": "SD", "Min": "Min", "Max": "Max"}
KIND_RANK = {"wind_speed": 0, "wind_direction": 1, "temperature": 2, "humidity": 3, "pressure": 4, "voltage": 5,
             "other": 6}
MIN_POINT_DAYS = 60.0
VISIT_GAP, VISIT_PAD = pd.Timedelta(hours=24), pd.Timedelta(hours=1)
PART_SHARE = 0.95
STILL_RECORDS, REF_WS, VANE_STILL_DEG, NEAR_OFFSET_MS = 36, 3.0, 1.0, 0.5
REF_JUMP = 0.25  # a trailing reference month whose mast-to-reference ratio is this far off is left out
MIN_MONTH_SHARE = 0.70
G, R_DRY, R_VAP = 9.80665, 287.05, 461.5


# ---------- reading ----------

def extract_rars(folder: Path, dest: Path, log=print) -> int:
    """Extract every .rar under folder to dest/<archive name>/ (flat). Needs unrar2-cffi."""
    from unrar.cffi import rarfile  # noqa: PLC0415
    n = 0
    for rp_ in sorted(folder.rglob("*.rar")):
        out = dest / rp_.stem
        out.mkdir(parents=True, exist_ok=True)
        rf = rarfile.RarFile(str(rp_))
        for m in rf.namelist():
            base = m.replace("\\", "/").rstrip("/").split("/")[-1]
            if not base.lower().endswith(".dat"):
                continue
            data = rf.read(m)
            target = out / base
            if target.exists() and target.read_bytes() == data:
                continue
            target.write_bytes(data)
            n += 1
    log(f"  extracted {n} new .dat files to {dest}")
    return n


def read_toa5(path: Path):
    lines = Path(path).read_bytes().decode("utf-8", errors="replace").splitlines()
    if len(lines) < 4:
        raise ValueError("fewer than 4 header lines")
    head = list(csv.reader(lines[:4]))
    env, names, units = head[0], head[1], head[2]
    if not env or env[0].strip() != "TOA5":
        raise ValueError("not a TOA5 file")
    rows, bad = [], 0
    for r in csv.reader(lines[4:]):
        if len(r) == len(names):
            rows.append(r)
        elif any(x.strip() for x in r):
            bad += 1
    df = pd.DataFrame(rows, columns=names)
    tcol = df.pop("TIMESTAMP")
    ts = pd.to_datetime(tcol, format="%Y-%m-%d %H:%M:%S", errors="coerce")
    if len(ts) and ts.isna().mean() > 0.5:
        ts = pd.to_datetime(tcol, errors="coerce")
    df = df.drop(columns=[c for c in ("RECORD", "ProgName") if c in df.columns])
    df = df.apply(pd.to_numeric, errors="coerce")
    df.index = pd.DatetimeIndex(ts, name="Timestamp")
    df = df[df.index.notna()]
    e = env + [""] * 8
    info = {"station": e[1], "model": e[2], "serial": e[3], "os": e[4], "program": e[5], "signature": e[6],
            "table": e[7], "names": names, "units": dict(zip(names, units)), "bad_lines": bad}
    return info, df


def load(folders: list, log=print, channels: dict | None = None) -> dict:
    """folders: data folders (every *.dat under them) or single files. A single file lets one mast be read from a
    folder that holds several (the run still stops if the files read carry more than one station).
    channels: per-port settings for formats whose files do not carry them (Nomad2 heights)."""
    paths = sorted({p.resolve() for f in folders if Path(f).is_file() for p in [Path(f)]}
                   | {p.resolve() for f in folders if Path(f).is_dir() for p in Path(f).rglob("*")
                      if p.is_file() and p.suffix.lower() == ".dat"})
    frames, infos, errors = [], [], []
    for p in paths:
        try:
            if nomad2.looks_like_nomad2(p):
                info, df = nomad2.read_nomad2(p, channels)
            else:
                info, df = read_toa5(p)
        except Exception as e:  # keep going; report the file
            errors.append(f"{p.name}: {e}")
            continue
        if df.empty:
            continue
        info.update(file=str(p), first=df.index[0], last=df.index[-1], records=len(df))
        df["_src"] = len(infos)
        infos.append(info)
        frames.append(df)
    if not frames:
        raise SystemExit("No readable logger files found (formats read: Campbell Scientific TOA5, SecondWind Nomad2 "
                         "Multi-Track export). " + "; ".join(errors[:3]))
    formats = sorted({i.get("format", "Campbell Scientific TOA5") for i in infos})
    if len(formats) > 1:
        raise SystemExit(f"The files read are in more than one format ({', '.join(formats)}). Select one mast's data.")
    stations = sorted({i["station"] for i in infos})
    if len(stations) > 1:
        raise SystemExit(f"The folders hold more than one station ({', '.join(stations)}). Select one mast's data: "
                         "give that mast's folder, or its file.")
    first_seen = {}
    for i in infos:
        first_seen[i["program"]] = min(first_seen.get(i["program"], i["first"]), i["first"])
    programs = sorted(first_seen, key=first_seen.get)
    code = {p: k for k, p in enumerate(programs)}
    data = pd.concat(frames, axis=0, join="outer", sort=False)
    data["_prog"] = data["_src"].map(lambda k: code[infos[int(k)]["program"]]).astype(float)
    names = [f"{Path(i['file']).parent.name}/{Path(i['file']).name}" for i in infos]
    data, removed, conflict_times, conflict_rows, conflicts = merge_duplicates(data, names)
    source = data.pop("_src").astype(int)
    prog = data.pop("_prog").ffill().bfill().astype(int)
    log(f"  {len(paths)} .dat files, {len(infos)} read, {len(errors)} unreadable; {len(data):,} records "
        f"{data.index[0]:%Y-%m-%d %H:%M} to {data.index[-1]:%Y-%m-%d %H:%M}; {removed:,} repeated records merged; "
        f"{len(conflict_times):,} conflicting timestamps quarantined.")
    return {"paths": paths, "infos": infos, "errors": errors, "programs": programs, "prog": prog, "source": source,
            "format": formats[0], "notes": list(dict.fromkeys(n for i in infos for n in i.get("notes", []))),
            "data": data, "removed": removed, "conflict_times": conflict_times, "conflict_rows": conflict_rows,
            "conflicts": conflicts}


def file_inventory(path: Path, files: list) -> dict:
    """SHA-256 of each input file, for a run given files rather than a data folder (as pipeline.write_inventory)."""
    import hashlib  # noqa: PLC0415
    rows = []
    for p in sorted(Path(f) for f in files):
        h = hashlib.sha256()
        with open(p, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        rows.append(("data file", p.name, p.stat().st_size, h.hexdigest()))
    text = "role,file,bytes,sha256\n" + "".join(f"{r[0]},{r[1]},{r[2]},{r[3]}\n" for r in rows)
    path.write_bytes(text.encode("utf-8"))
    return {"file": path.name, "files": len(rows), "bytes": sum(r[2] for r in rows), "data_folder_files": len(rows),
            "converted_exports": 0, "inventory_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}


def _bases(cols) -> list:
    return list(dict.fromkeys(re.sub(r"_(Avg|Std|Min|Max)$", "", c) for c in cols
                              if c not in ("TIMESTAMP", "RECORD", "ProgName")))


def configurations(infos: list, programs: list) -> pd.DataFrame:
    rows, prev = [], None
    for p in programs:
        fs = [i for i in infos if i["program"] == p]
        names = fs[0]["names"]
        bases = _bases(names)
        added = [b for b in bases if prev is not None and b not in prev["bases"]]
        dropped = [b for b in (prev["bases"] if prev else []) if b not in bases]
        reordered = []
        if prev is not None:
            for b in bases:
                if b in prev["bases"]:
                    now = [c for c in names if re.sub(r"_(Avg|Std|Min|Max)$", "", c) == b]
                    before = [c for c in prev["names"] if re.sub(r"_(Avg|Std|Min|Max)$", "", c) == b]
                    if now != before and sorted(now) == sorted(before):
                        reordered.append(b)
        rows.append({"program": p, "first_record": min(i["first"] for i in fs), "last_record": max(i["last"] for i in fs),
                     "files": len(fs), "columns": len(names), "added": ", ".join(added), "removed": ", ".join(dropped),
                     "statistics_reordered": ", ".join(reordered),
                     "logger": f"{fs[0]['model']} s/n {fs[0]['serial']}, OS {fs[0]['os']}",
                     "program_signature": fs[0]["signature"], "table": fs[0]["table"]})
        prev = {"bases": bases, "names": names}
    return pd.DataFrame(rows)


def gps_position(data: pd.DataFrame) -> dict | None:
    need = ["gps_latitude_deg_Avg", "gps_latitude_min_Avg", "gps_longitude_deg_Avg", "gps_longitude_min_Avg"]
    if not all(c in data.columns for c in need):
        return None
    g = data[need].dropna()
    g = g[(g.iloc[:, 0] != 0) | (g.iloc[:, 2] != 0)]
    if g.empty:
        return None

    def dm(deg, mins):
        sign = np.where((deg < 0) | ((deg == 0) & (mins < 0)), -1.0, 1.0)
        return pd.Series(sign * (deg.abs().to_numpy() + mins.abs().to_numpy() / 60.0), index=deg.index)

    lat, lon = dm(g.iloc[:, 0], g.iloc[:, 1]), dm(g.iloc[:, 2], g.iloc[:, 3])
    la, lo = float(lat.median()), float(lon.median())
    daily = pd.DataFrame({"lat": lat, "lon": lon}).resample("D").median().dropna()
    spread = max((distance_m((la, lo), (r.lat, r.lon)) for r in daily.itertuples()), default=0.0)
    return {"lat": la, "lon": lo, "records": int(len(g)), "first": g.index[0], "last": g.index[-1],
            "largest_daily_offset_m": float(spread)}


# ---------- measurement points, visits ----------

def build_points(data: pd.DataFrame, infos: list) -> tuple:
    units = {}
    for i in infos:
        for k, v in i["units"].items():
            units.setdefault(k, v)
    groups = {}
    for c in data.columns:
        m = COL.match(str(c))
        if m:
            groups.setdefault((m["sid"], float(m["h"])), {})[STATS[m["stat"]]] = c
    heights_by_sid = {}
    for sid, h in groups:
        heights_by_sid.setdefault(sid, set()).add(h)

    def prefix(sid):
        return re.match(r"[A-Za-z]+", sid).group(0)

    def key(item):
        (sid, h), _ = item
        kind = KINDS.get(prefix(sid), ("other",))[0]
        return (KIND_RANK.get(kind, 9), -h, int(re.sub(r"\D", "", sid) or 0))

    pts, rows, ch = [], [], 0
    for (sid, h), cols in sorted(groups.items(), key=key):
        if "Avg" not in cols:
            continue
        kind, ch_type, short = KINDS.get(prefix(sid), ("other", prefix(sid), prefix(sid)))
        x = data[cols["Avg"]]
        have = x.notna()
        first = x.index[have][0] if have.any() else pd.NaT
        last = x.index[have][-1] if have.any() else pd.NaT
        days = float(have.sum()) / 144.0
        name = f"{sid} {short} {h:g} m"
        if len(heights_by_sid[sid]) > 1 and pd.notna(first):
            name += f" ({first:%b %Y}–{last:%b %Y})"
        # another sensor of the same kind that stays at this height all campaign
        same_height = [k for k in groups if k[1] == h and k[0] != sid and prefix(k[0]) == prefix(sid)
                       and len(heights_by_sid[k[0]]) == 1]
        temporary = len(heights_by_sid[sid]) > 1 and days < MIN_POINT_DAYS and not same_height
        u = units.get(cols["Avg"], "")
        ch += 1
        s = Sensor(channel=ch, kind=kind, ch_type=ch_type, height=h, bearing="", units=u, cols=dict(cols),
                   meta={"Description": f"{sid} at {h:g} m", "Serial Number": "", "Scale Factor": "", "Offset": "",
                         "Units": u}, name=name)
        pts.append((s, temporary))
        rows.append({"point": name, "sensor": sid, "kind": kind, "height_m": h, "units": u,
                     "columns": ", ".join(cols.values()), "first": first, "last": last, "days_of_data": round(days, 1),
                     "analysed": "no: short spell at a height no other sensor measures" if temporary else "yes"})
    for base, (kind, ch_type, label) in EXTRA.items():
        cols = {STATS[st]: f"{base}_{st}" for st in STATS if f"{base}_{st}" in data.columns}
        if "Avg" in cols:
            u = units.get(cols["Avg"], "")
            ch += 1
            s = Sensor(channel=ch, kind=kind, ch_type=ch_type, height=None, bearing="", units=u, cols=cols,
                       meta={"Description": label, "Serial Number": "", "Scale Factor": "", "Offset": "", "Units": u},
                       name=label)
            pts.append((s, False))
            x = data[cols["Avg"]]
            rows.append({"point": label, "sensor": base, "kind": kind, "height_m": None, "units": u,
                         "columns": ", ".join(cols.values()), "first": x.first_valid_index(),
                         "last": x.last_valid_index(), "days_of_data": round(float(x.notna().sum()) / 144.0, 1),
                         "analysed": "checks only"})
    return pts, pd.DataFrame(rows)


def site_visits(data: pd.DataFrame, prog: pd.Series, programs: list) -> tuple:
    events = []
    if "NumSamplesInterval" in data.columns:
        n = data["NumSamplesInterval"]
        full = float(n.mode().iloc[0])
        part = (n < PART_SHARE * full).fillna(False)
        events += [(t, "part") for t in n.index[part.to_numpy()]]
    change = prog.ne(prog.shift()) & prog.shift().notna()
    events += [(t, programs[int(prog.loc[t])]) for t in prog.index[change.to_numpy()]]
    events.sort(key=lambda e: e[0])
    groups = []
    for t, what in events:
        if groups and t - groups[-1]["end"] <= VISIT_GAP:
            groups[-1]["end"] = t
            groups[-1]["events"].append(what)
        else:
            groups.append({"start": t, "end": t, "events": [what]})
    windows = [(g["start"] - VISIT_PAD, g["end"] + VISIT_PAD, len(g["events"])) for g in groups]
    table = pd.DataFrame([{"first_event": g["start"], "last_event": g["end"], "flagged_from": g["start"] - VISIT_PAD,
                           "flagged_to": g["end"] + VISIT_PAD,
                           "part_interval_records": sum(e == "part" for e in g["events"]),
                           "program_loaded": "; ".join(e for e in g["events"] if e != "part")} for g in groups])
    return windows, table


# ---------- added checks ----------

def still_spells(data: pd.DataFrame, sensors: list, qcr: dict) -> pd.DataFrame:
    """Flag whole spells in which a cup or vane is dead or fixed (see module notes); returns the spells."""
    cups = [s for s in sensors if s.kind == "wind_speed"]
    rows = []
    for s in sensors:
        if s.kind not in ("wind_speed", "wind_direction") or s.label not in qcr["flags"]:
            continue
        x = data[s.avg_col]
        others = [c.avg_col for c in cups if c.avg_col != s.avg_col]
        ref = data[others].median(axis=1) if others else pd.Series(np.nan, index=data.index)
        still = pd.Series(False, index=data.index)
        if "SD" in s.cols:
            still |= data[s.cols["SD"]].abs() < 1e-6
        if s.kind == "wind_speed":  # a dead cup sits at its offset, sometimes with a stray pulse
            still |= x < NEAR_OFFSET_MS
        if s.kind == "wind_direction":
            ranges = []
            for y in (x % 360.0, (x + 180.0) % 360.0):  # either view avoids the 0/360 wrap
                roll = y.rolling(STILL_RECORDS, min_periods=STILL_RECORDS)
                ranges.append(roll.max() - roll.min())
            flat_end = (np.minimum(ranges[0], ranges[1]) < VANE_STILL_DEG).fillna(False)
            flat = flat_end.copy()
            for k in range(1, STILL_RECORDS):
                flat |= flat_end.shift(-k, fill_value=False)
            still |= flat
        still &= x.notna()
        live = x.notna() & ~still
        for a, b, _n in _runs(~live):
            seg = still.loc[a:b]
            windy = int((seg & (ref.loc[a:b] >= REF_WS)).sum())
            if windy < STILL_RECORDS or not seg.any():
                continue
            idx = seg.index[seg.to_numpy()]
            before = int(qcr["flags"][s.label]["stuck"].loc[idx].sum())
            qcr["flags"][s.label]["stuck"].loc[idx] = True
            rows.append({"sensor": s.label, "from": idx[0], "to": idx[-1],
                         "days": round((idx[-1] - idx[0]).total_seconds() / 86400.0, 1),
                         "still_records": int(len(idx)), "while_other_cups_3ms_or_more": windy,
                         "already_flagged_by_v0.1.3_checks": before, "added_by_this_check": int(len(idx)) - before,
                         "reading": f"{float(x.loc[idx].median()):.2f} {s.units}".strip()})
    for label, f in qcr["flags"].items():
        bad = pd.Series(False, index=data.index)
        for name in FLAG_ORDER:
            bad |= f[name]
        qcr["valid"][label] = ~bad
    return pd.DataFrame(rows)


def relocation_check(data: pd.DataFrame, pts: list, qcr: dict, alpha: dict) -> pd.DataFrame:
    """Sensors logged at two heights: does the step in their ratio to a fixed cup fit a physical move?"""
    valid = qcr["valid"]
    by_sid = {}
    for s, temp in pts:
        if s.kind == "wind_speed":
            sid = s.meta["Description"].split(" at ")[0]
            by_sid.setdefault(sid, []).append((s, temp))
    fixed = [s for s, temp in pts if s.kind == "wind_speed" and not temp
             and len(by_sid[s.meta["Description"].split(" at ")[0]]) == 1]
    rows = []
    for sid, items in by_sid.items():
        if len(items) < 2:
            continue
        old = next((s for s, temp in items if temp), None)
        new = next((s for s, temp in items if not temp), None)
        if old is None or new is None:
            continue
        refh = min({s.height for s in fixed}, key=lambda h: abs(h - new.height))
        refs = [s for s in fixed if s.height == refh]
        ref = pd.concat([data[s.avg_col].where(valid[s.label]) for s in refs], axis=1).mean(axis=1)
        o = (data[old.avg_col].where(valid[old.label]) / ref)
        n = (data[new.avg_col].where(valid[new.label]) / ref)
        months = sorted(set(o.dropna().index.month))
        for lab, lim in (("all records, reference 4 m/s or more", 4.0), ("reference 8 m/s or more", 8.0)):
            om = o[(ref >= lim)].dropna()
            nm = n[(ref >= lim) & n.index.month.isin(months)].dropna()
            a = alpha.get((min(new.height, refh), max(new.height, refh)), np.nan)
            expect = (old.height / new.height) ** a if np.isfinite(a) else np.nan
            rows.append({"sensor": sid, "logged_before": f"{old.height:g} m, {old.label}", "logged_after": f"{new.height:g} m",
                         "reference": f"{' + '.join(s.label for s in refs)} (mean)", "records": lab,
                         "ratio_before": float(om.median()) if len(om) else np.nan, "n_before": int(len(om)),
                         "ratio_after_same_months": float(nm.median()) if len(nm) else np.nan, "n_after": int(len(nm)),
                         "observed_step_%": (float(om.median()) / float(nm.median()) - 1) * 100 if len(om) and len(nm) else np.nan,
                         "step_if_moved_%": (expect - 1) * 100, "shear_used": a})
    return pd.DataFrame(rows)


# ---------- statistics ----------

def monthly(hs: dict, grid: pd.DatetimeIndex) -> dict:
    """Mean per calendar month and the share of the whole month's records that are valid (a month the
    campaign only touches, such as a last record at 00:00 on the 1st, gets a share near 0)."""
    months = pd.period_range(grid[0].to_period("M"), grid[-1].to_period("M"), freq="M")
    exp = pd.Series(months.days_in_month * 144, index=months)
    out = {}
    for h, d in hs.items():
        ws = d["ws"]
        g = ws.groupby(ws.index.to_period("M"))
        t = pd.DataFrame({"mean": g.mean(), "valid": g.count()}).reindex(exp.index)
        t["share"] = t["valid"].fillna(0) / exp
        out[h] = t
    return out


def momm_ok(t: pd.DataFrame) -> tuple:
    ok = t[t["share"] >= MIN_MONTH_SHARE]["mean"].dropna()
    by_cal = ok.groupby(ok.index.month).mean()
    return (float(by_cal.mean()) if len(by_cal) == 12 else np.nan), ok


def shear_pairs(hs: dict, wd: pd.Series | None, pairs, min_ws=3.0) -> tuple:
    rows, alpha, hourly = [], {}, {}
    for lo, hi in pairs:
        if lo not in hs or hi not in hs:
            continue
        f = pd.DataFrame({"lo": hs[lo]["ws"], "hi": hs[hi]["ws"]}).dropna()
        f = f[(f >= min_ws).all(axis=1)]
        if len(f) < 100:
            continue
        ln = math.log(hi / lo)
        a = math.log(f["hi"].mean() / f["lo"].mean()) / ln
        day = f[(f.index.hour >= 9) & (f.index.hour < 17)]
        night = f[(f.index.hour >= 20) | (f.index.hour < 6)]
        hb = f.groupby(f.index.hour).apply(lambda u: math.log(u["hi"].mean() / u["lo"].mean()) / ln)
        hourly[f"{lo:g}-{hi:g} m"] = hb
        rows.append({"pair": f"{lo:g}-{hi:g} m", "records": len(f), "mean_lo": f["lo"].mean(), "mean_hi": f["hi"].mean(),
                     "alpha": a, "alpha_day_09_17": math.log(day["hi"].mean() / day["lo"].mean()) / ln,
                     "alpha_night_20_06": math.log(night["hi"].mean() / night["lo"].mean()) / ln})
        alpha[(lo, hi)] = a
    return pd.DataFrame(rows), alpha, pd.DataFrame(hourly)


def hub_density(data, valid, sensors, z_hub: float) -> dict:
    """Air density at z_hub: pressure from the barometer carried up hypsometrically with the virtual temperature
    of the highest thermometer and hygrometer."""
    t = next((s for s in sorted(sensors, key=lambda s: -(s.height or 0)) if s.kind == "temperature" and s.height), None)
    rh = next((s for s in sorted(sensors, key=lambda s: -(s.height or 0)) if s.kind == "humidity" and s.height), None)
    p = next((s for s in sensors if s.kind == "pressure"), None)
    if t is None or p is None:
        return {}
    T = data[t.avg_col].where(valid[t.label])
    P = data[p.avg_col].where(valid[p.label])
    RH = data[rh.avg_col].where(valid[rh.label]).clip(0, 100) if rh is not None else pd.Series(0.0, index=data.index)
    e = RH / 100.0 * 6.1078 * 10 ** (7.5 * T / (T + 237.3))
    tk = T + 273.15
    tv = tk / (1 - 0.378 * e / P)
    p_hub = P * np.exp(-G * (z_hub - p.height) / (R_DRY * tv))
    e_hub = e * p_hub / P
    rho = (p_hub - e_hub) * 100.0 / (R_DRY * tk) + e_hub * 100.0 / (R_VAP * tk)
    return {"rho": rho, "mean": float(rho.mean()), "p_mean": float(p_hub.mean()), "t_sensor": t.label,
            "p_sensor": p.label, "rh_sensor": rh.label if rh is not None else "", "z": z_hub}


def energy_rose(ws: pd.Series, wd: pd.Series, n=16) -> pd.DataFrame:
    m = ws.notna() & wd.notna()
    sec = sector(wd[m], n)
    e = (ws[m] ** 3).groupby(sec).sum()
    tab = rose(ws, wd, n)
    tab["energy_%"] = (e / e.sum() * 100.0).reindex(range(n)).fillna(0.0).to_numpy()
    return tab


# ---------- long-term reference ----------

def power_reference(daily_json, monthly_json=None) -> tuple:
    p = json.loads(Path(daily_json).read_text(encoding="utf-8"))["properties"]["parameter"]
    ws = pd.Series(p["WS50M"], dtype=float)
    ws.index = pd.to_datetime(ws.index, format="%Y%m%d")
    ws = ws[ws > -900]
    wd = pd.Series(p.get("WD50M", {}), dtype=float)
    if len(wd):
        wd.index = pd.to_datetime(wd.index, format="%Y%m%d")
        wd = wd[wd > -900]
    g = ws.groupby(ws.index.to_period("M"))
    from_daily = g.mean()[g.count() >= 25]
    ref = from_daily.copy()
    note = "monthly means of NASA POWER daily WS50M"
    if monthly_json:
        wm = _windmap()
        mon = wm.power_monthly(monthly_json)
        ref = pd.concat([mon, from_daily[from_daily.index > mon.index[-1]]]).sort_index()
        note = (f"NASA POWER monthly WS50M to {mon.index[-1]}, then monthly means of daily WS50M "
                f"to {ref.index[-1]}")
    return ws, wd, ref, from_daily, note


def daily_vector_mean(wd: pd.Series, ws: pd.Series, min_records=120) -> tuple:
    m = wd.notna() & ws.notna()
    u = (ws[m] * np.sin(np.radians(wd[m]))).resample("D").mean()
    v = (ws[m] * np.cos(np.radians(wd[m]))).resample("D").mean()
    n = m[m].resample("D").size()
    d = (np.degrees(np.arctan2(u, v)) % 360.0)[n >= min_records]
    s = ws[m].resample("D").mean()[n >= min_records]
    return d, s


# ---------- output helpers ----------

def save_png(b64: str | None, path: Path) -> str | None:
    if not b64:
        return None
    path.write_bytes(base64.b64decode(b64))
    return path.name


def _xl(df: pd.DataFrame) -> pd.DataFrame:
    """Excel cannot hold time-zone-aware or Period values; make them plain."""
    out = df.copy()
    if isinstance(out.index, pd.PeriodIndex):
        out.index = out.index.astype(str)
    for c in out.columns:
        if isinstance(out[c].dtype, pd.PeriodDtype):
            out[c] = out[c].astype(str)
    return out


def main(argv=None, log=None):
    """log: where progress lines go (default: print). Problems the user can fix are raised as SystemExit."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("folders", nargs="+", help="data folders holding TOA5 .dat files (and RAR archives with --extract)")
    ap.add_argument("--out", required=True, help="Bohemian Wind QC outputs folder")
    ap.add_argument("--name", help="site name for the run folder and outputs")
    ap.add_argument("--settings", help="site settings .json: site_name, elevation_m, source, notes")
    ap.add_argument("--extract", action="store_true", help="extract RAR archives in the first folder first")
    ap.add_argument("--power-daily", help="NASA POWER daily point JSON (WS50M, WD50M) for the long-term index")
    ap.add_argument("--power-monthly", help="NASA POWER monthly point JSON (WS50M)")
    ap.add_argument("--lt-years", default="2005-2024", help="long-term reference years, e.g. 2005-2024")
    ap.add_argument("--ref-height", type=float, default=100.0,
                    help="basis height for the long-term work: the reference-month check and the daily correlation "
                         "use its monthly and daily means (default 100 m)")
    a = ap.parse_args(argv)

    t0 = time.time()
    out_root = Path(a.out)
    raw = Path(a.folders[0])
    settings = json.loads(Path(a.settings).read_text(encoding="utf-8")) if a.settings else {}
    folders = [Path(f) for f in a.folders]
    converted = out_root / "converted" / safe_name(raw.name)
    name = a.name or settings.get("site_name") or raw.name
    run_dir = out_root / "runs" / f"{safe_name(name)}_{datetime.now():%Y%m%d-%H%M%S}"
    (run_dir / "figures").mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "run.log"

    def say(msg):
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now():%H:%M:%S}  {msg}\n")
        (log or (lambda m: print(m, flush=True)))(msg)

    say(f"{READER} with Bohemian Wind QC v{QC_VERSION} checks and statistics")
    say(f"Input: {', '.join(str(f) for f in folders)}")
    say(f"Output: {run_dir}")
    if a.extract:
        say("Extracting RAR archives ...")
        extract_rars(raw, converted, log=say)
    if converted.exists() and converted.resolve() not in [f.resolve() for f in folders]:
        folders.append(converted)
    say("Reading logger files ...")
    L = load(folders, log=say, channels=settings.get("channels"))
    say(f"  Format: {L['format']}.")
    for n in L["notes"]:
        say(f"  {n}")
    data, infos, programs, prog = L["data"], L["infos"], L["programs"], L["prog"]
    cfg = configurations(infos, programs)
    pos = gps_position(data)
    lat = settings.get("latitude", pos["lat"] if pos else None)
    lon = settings.get("longitude", pos["lon"] if pos else None)
    elev = settings.get("elevation_m")
    site = {"Site Description": settings.get("site_name", name), "Site Number": infos[0]["station"],
            "Latitude": f"{lat:.7f}" if lat is not None else "", "Longitude": f"{lon:.7f}" if lon is not None else "",
            "Elevation": f"{elev}" if elev is not None else "", "Time Zone": settings.get("time_zone", "not stated")}
    say(f"  Station {infos[0]['station']}, {infos[0]['model']} s/n {infos[0]['serial']}; {len(programs)} logger programs.")
    if pos:
        say(f"  Logger GPS: {pos['lat']:.6f}, {pos['lon']:.6f} ({pos['records']:,} records from {pos['first']:%d %b %Y}); "
            f"daily positions within {pos['largest_daily_offset_m']:.0f} m.")
    pts, points_table = build_points(data, infos)
    sensors_all = [s for s, _ in pts]
    sensors = [s for s, temp in pts if not temp]
    windows, visits_table = site_visits(data, prog, programs)
    say(f"  {len(sensors_all)} measurement points; not analysed: "
        + (", ".join(s.label for s, temp in pts if temp) or "none") + f"; {len(windows)} site visits or restarts.")
    ds = Dataset(files=L["paths"], data=data, columns={}, site=site,
                 loggers=[{"Model": infos[0]["model"], "Serial Number": infos[0]["serial"], "Firmware": infos[0]["os"]}],
                 ipacks=[], channel_history=pd.DataFrame(), duplicates_removed=L["removed"], read_errors=L["errors"],
                 source_format=L["format"], source=L["source"], conflict_times=L["conflict_times"],
                 conflict_rows=L["conflict_rows"], conflicts=L["conflicts"],
                 site_checks={"gps": pos}, site_logged=dict(site), settings=settings)
    ds.points = {"visits": windows, "excluded": [s.label for s, temp in pts if temp]}
    say("Fingerprinting input files ...")
    if raw.is_dir():
        inputs = write_inventory(run_dir / "inputs.csv", raw, converted, L["paths"], out_root)
    else:
        inputs = file_inventory(run_dir / "inputs.csv", [f for f in folders if f.is_file()])
    say(f"  {inputs['files']:,} files, input set fingerprint {inputs['inventory_sha256'][:16]}.")
    say("Running quality checks (v0.1.3) ...")
    qcr = run_qc(ds, sensors_all)
    spells = still_spells(data, sensors_all, qcr)
    say(f"  Added check found {len(spells)} dead or fixed sensor spells; "
        f"{int(spells['added_by_this_check'].sum()) if len(spells) else 0:,} records flagged beyond the v0.1.3 checks.")
    say("Analysing wind statistics (v0.1.3) ...")
    an = analyse(ds, qcr, sensors)
    hs = an["heights"]
    grid = qcr["time"]["grid"]
    hts = sorted(hs)
    mon = monthly(hs, grid)
    wd_top = hs[max(hs)]["wd"]
    pairs = [(hts[i], hts[j]) for i in range(len(hts)) for j in range(i + 1, len(hts))]
    shear_tab, alpha, shear_hourly = shear_pairs(hs, wd_top, pairs)
    reloc = relocation_check(data, pts, qcr, alpha)

    # per-height summary
    exp = max(qcr["time"]["expected"], 1)
    rows = []
    for h in sorted(hs, reverse=True):
        ws = hs[h]["ws"]
        A, k = weibull_fit(ws.dropna())
        mm, ok = momm_ok(mon[h])
        rows.append({"height_m": h, "source": hs[h]["source"], "vane": hs[h]["vane"], "valid_records": int(ws.notna().sum()),
                     "recovery_%": 100.0 * ws.notna().sum() / exp, "mean_all_valid": float(ws.mean()),
                     "momm_months_70pct": mm, "months_used": len(ok), "weibull_A": A, "weibull_k": k,
                     "max_10min_mean": float(ws.max()), "max_10min_at": ws.idxmax()})
    height_stats = pd.DataFrame(rows)
    hub_h = 120.0 if 120.0 in hs else max(hs)
    ti_hub = ti_bins(hs[hub_h]["ws"], hs[hub_h]["sd"])
    rose_hub = energy_rose(hs[hub_h]["ws"], hs[hub_h]["wd"]) if hs[hub_h]["wd"] is not None else None
    dens = {z: hub_density(data, qcr["valid"], sensors, z) for z in (100.0, hub_h)}
    wake_rows = []
    for pr in an.get("pairs", []):
        for w in pr.get("wake", []):
            r = dict(w)
            r["height_m"] = pr["height"]
            r["implied_boom_bearing_deg"] = (w["observed_wake_deg"] - 180.0) % 360.0 if np.isfinite(
                w.get("observed_wake_deg", np.nan)) else np.nan
            wake_rows.append(r)
    wake = pd.DataFrame(wake_rows)

    # long-term
    lt_rows, lt_tables, vane_check, ref_dropped, lt_used = [], {}, pd.DataFrame(), [], {}
    basis_h = float(a.ref_height) if float(a.ref_height) in hs else (100.0 if 100.0 in hs else max(hs))
    say(f"  Basis height for the long-term work: {basis_h:g} m.")
    if a.power_daily:
        wm = _windmap()
        y0, y1 = (int(x) for x in a.lt_years.split("-"))
        pws, pwd, ref, from_daily, ref_note = power_reference(a.power_daily, a.power_monthly)
        # Trailing reference months that break the mast-to-reference relation are left out: POWER appends
        # GEOS-IT to the end of the MERRA-2 series for its latest weeks. Checked at the basis height (--ref-height)
        # against the same calendar month in other years, newest month first.
        h_chk = basis_h
        okc = momm_ok(mon[h_chk])[1]
        ratio = (okc / ref.reindex(okc.index)).dropna()
        for p in list(ratio.index[-3:])[::-1]:
            same = ratio[(ratio.index.month == p.month) & (ratio.index != p)]
            usual = float(same.median()) if len(same) else float(ratio.drop(p).median())
            if abs(float(ratio[p]) / usual - 1.0) <= REF_JUMP:
                break
            ref_dropped.append({"month": str(p), "mast_mean": float(okc[p]), "reference": float(ref[p]),
                                "ratio": float(ratio[p]), "usual_ratio_same_month": usual})
        drop = {pd.Period(r["month"], "M") for r in ref_dropped}
        for h in sorted(hs, reverse=True):
            _, ok = momm_ok(mon[h])
            ok = ok[~ok.index.isin(drop) & ok.index.isin(ref.index)]
            if ok.index.month.nunique() < 12:
                continue
            lt_used[h] = ok
            for lab, yrs in (("base", (y0, y1)), (f"LT years {y0 + 1}-{y1 + 1}", (y0 + 1, y1 + 1))):
                if (ref.index.year == yrs[1]).sum() < 12:
                    continue
                m2 = wm.Mast(name=name, lat=lat, lon=lon, elevation=elev or 0, top_height=h,
                             monthly={str(p): float(v) for p, v in ok.items()}, shear=0.0, period="")
                lt = wm.long_term(m2, ref, lt_years=yrs)
                lt_rows.append({"height_m": h, "variant": lab, "months": lt["months"], "calendar_months": lt["calendar_months"],
                                "first_month": str(ok.index[0]), "last_month": str(ok.index[-1]),
                                "measured_momm": lt["measured"], "reference_concurrent": lt["ref_period"],
                                "reference_long_term": lt["ref_lt"], "factor": lt["factor"], "long_term_mean": lt["lt"],
                                "monthly_r": lt["r"], "lt_years": f"{yrs[0]}-{yrs[1]}", "method": lt["method"]})
            # check: monthly linear regression, mast = c0 + c1 x reference, applied to the long-term months
            conc = ref.reindex(ok.index)
            c1, c0 = np.polyfit(conc.to_numpy(), ok.to_numpy(), 1)
            ltr = ref[f"{y0}-01":f"{y1}-12"]
            pred = c0 + c1 * ltr.groupby(ltr.index.month).mean()
            meas = float(ok.groupby(ok.index.month).mean().mean())
            lt_rows.append({"height_m": h, "variant": "check: monthly linear regression", "months": len(ok),
                            "calendar_months": int(ok.index.month.nunique()), "first_month": str(ok.index[0]),
                            "last_month": str(ok.index[-1]), "measured_momm": meas,
                            "reference_concurrent": float(conc.groupby(conc.index.month).mean().mean()),
                            "reference_long_term": float(ltr.groupby(ltr.index.month).mean().mean()),
                            "factor": float(pred.mean()) / meas, "long_term_mean": float(pred.mean()),
                            "monthly_r": float(np.corrcoef(conc, ok)[0, 1]), "lt_years": f"{y0}-{y1}",
                            "method": f"mast = {c0:.3f} + {c1:.3f} x reference, monthly, then mean of the 12 calendar months"})
            lt_tables[h] = pd.DataFrame({"mast_mean": momm_ok(mon[h])[1],
                                         "reference_ws50m": ref.reindex(momm_ok(mon[h])[1].index)})
            lt_tables[h]["used"] = lt_tables[h].index.isin(ok.index)
            lt_tables[h]["share_valid"] = mon[h]["share"].reindex(lt_tables[h].index)
        # daily correlation and vane check against the reference direction
        top = max(hs)
        d_top, s_top = daily_vector_mean(hs[hub_h]["wd"], hs[hub_h]["ws"]) if hs[hub_h]["wd"] is not None else (None, None)
        if d_top is not None and len(pwd):
            j = pd.concat([d_top.rename("mast_dir"), s_top.rename("mast_ws"), pwd.rename("ref_dir"), pws.rename("ref_ws")],
                          axis=1, join="inner").dropna()
            j["difference"] = (j["mast_dir"] - j["ref_dir"] + 180.0) % 360.0 - 180.0
            vane_check = j[~j.index.to_period("M").isin(drop)]
        daily_ws = hs[basis_h]["ws"]
        dn = daily_ws.resample("D").count()
        dm = daily_ws.resample("D").mean()[dn >= 132]
        jj = pd.concat([dm.rename("mast"), pws.rename("ref")], axis=1, join="inner").dropna()
        jj = jj[~jj.index.to_period("M").isin(drop)]
        daily_r = float(np.corrcoef(jj["mast"], jj["ref"])[0, 1]) if len(jj) > 30 else np.nan
        last_merra = pws.index[-1]
    lt_tab = pd.DataFrame(lt_rows)

    # ---------- findings ----------
    F = []
    tq = qcr["time"]
    F.append(f"Data: {len(data):,} ten-minute records from {data.index[0]:%d %b %Y %H:%M} to {data.index[-1]:%d %b %Y %H:%M}; "
             f"{tq['missing']:,} of {tq['expected']:,} expected timestamps missing ({100 * tq['missing'] / tq['expected']:.1f}%). "
             f"{L['removed']:,} repeated records in overlapping files merged; {len(L['conflict_times']):,} conflicting.")
    F += L["notes"]
    for g0, g1, nrec in sorted(tq["gaps"], key=lambda g: -g[2]):
        if nrec >= 144:
            F.append(f"No data from {g0:%d %b %Y %H:%M} to {g1:%d %b %Y %H:%M} ({nrec * tq['interval_s'] / 86400:.1f} days).")
    for _, r in spells.iterrows():
        F.append(f"{r['sensor']}: dead or fixed from {r['from']:%d %b %Y %H:%M} to {r['to']:%d %b %Y %H:%M} "
                 f"({r['days']:.1f} days; reading {r['reading']}); flagged as 'stuck' for the whole spell.")
    fsum = flag_summary(qcr)
    for flag, what in (("range", "outside the physical limits (mean, minimum, maximum or gust, or the standard deviation)"),
                       ("spike", "isolated spikes")):
        hit = fsum[fsum[flag] > 0]
        if len(hit):
            F.append(f"{int(hit[flag].sum()):,} record{'s' if hit[flag].sum() != 1 else ''} {what}, flagged '{flag}': "
                     + "; ".join(f"{r['sensor']} ({int(r[flag]):,})" for _, r in hit.iterrows()) + ".")
    for _, r in reloc.iterrows():
        if r["records"].startswith("reference 8"):
            F.append(f"{r['sensor']} logged at {r['logged_before']} and at {r['logged_after']} afterwards. Against "
                     f"{r['reference']}, it read {r['observed_step_%']:+.1f}% higher before the change than in the same "
                     f"months later (winds of 8 m/s or more); a move from {r['logged_before'].split(',')[0]} to "
                     f"{r['logged_after']} explains {r['step_if_moved_%']:+.1f}% (shear {r['shear_used']:.3f}). "
                     "The earlier spell is left out of the statistics; confirm the change on the site visit record.")
    if len(cfg) > 1:
        for i in range(1, len(cfg)):
            r = cfg.iloc[i]
            bits = [f"added {r['added']}" if r["added"] else "", f"removed {r['removed']}" if r["removed"] else "",
                    f"statistics reordered for {r['statistics_reordered']}" if r["statistics_reordered"] else ""]
            bits = [b for b in bits if b]
            F.append(f"Logger program {r['program']} from {r['first_record']:%d %b %Y %H:%M}"
                     + (": " + "; ".join(bits) if bits else ": same columns") + ".")
    for _, w in wake.iterrows():
        if np.isfinite(w.get("observed_wake_deg", np.nan)):
            F.append(f"{w['cup']}: mast wake centred at {w['observed_wake_deg']:.0f}° ({w['wake_deficit_%']:.0f}% deficit), "
                     f"so its boom points to about {w['implied_boom_bearing_deg']:.0f}°. No boom bearings are logged: "
                     "check against the installation record.")
    if len(vane_check):
        vc = vane_check[(vane_check["ref_ws"] >= 5) & (vane_check["mast_ws"] >= 5)]
        if len(vc) >= 20:
            q = vc["difference"].quantile([0.25, 0.5, 0.75])
            F.append(f"Vane north check: on {len(vc)} days with 5 m/s or more, the {hub_h:g} m direction runs "
                     f"{q[0.5]:+.0f}° from NASA POWER (MERRA-2) at 50 m (quartiles {q[0.25]:+.0f}° to {q[0.75]:+.0f}°).")
    for r in ref_dropped:
        F.append(f"NASA POWER reference for {r['month']} left out of the long-term index: mast-to-reference ratio "
                 f"{r['ratio']:.2f} against {r['usual_ratio_same_month']:.2f} in the same month of other years. POWER appends "
                 "GEOS-IT to the end of MERRA-2 for its latest weeks; this month does not match the rest of the series.")
    for _, r in lt_tab[lt_tab["variant"] == "base"].iterrows() if len(lt_tab) else []:
        reg = lt_tab[(lt_tab["height_m"] == r["height_m"]) & lt_tab["variant"].str.startswith("check")]
        F.append(f"Long-term at {r['height_m']:g} m{' (basis)' if r['height_m'] == basis_h else ''}: measured mean of "
                 f"monthly means {r['measured_momm']:.2f} m/s "
                 f"({r['months']} months, {r['first_month']} to {r['last_month']}) x {r['factor']:.3f} "
                 f"(MERRA-2 {r['lt_years']}, monthly r {r['monthly_r']:.2f}) = {r['long_term_mean']:.2f} m/s"
                 + (f"; monthly regression check {float(reg['long_term_mean'].iloc[0]):.2f} m/s." if len(reg) else "."))
    F.append(DISCLAIMER)

    # ---------- outputs ----------
    say("Writing outputs ...")
    fig = run_dir / "figures"
    figs = {}
    rec = recovery_by_month(qcr, sensors_all)
    figs["recovery"] = save_png(rp.chart_recovery(rec), fig / "01_recovery.png")
    mws = pd.DataFrame({h: mon[h]["mean"].where(mon[h]["share"] >= MIN_MONTH_SHARE) for h in sorted(hs, reverse=True)})
    figs["monthly"] = save_png(rp.chart_lines(mws, "Monthly mean wind speed (months with at least 70% valid data)", "m/s"),
                               fig / "02_monthly_ws.png")
    figs["diurnal"] = save_png(rp.chart_lines(an["diurnal_ws"], "Mean wind speed by hour of day (logger time)", "m/s",
                                              "Hour"), fig / "03_diurnal_ws.png")
    for i, pr in enumerate(an.get("pairs", [])):
        figs[f"pair_{pr['height']:g}"] = save_png(rp.chart_pair(pr), fig / f"04_pair_{pr['height']:g}m.png")
    figs["shear"] = save_png(rp.chart_shear(an["shear"]), fig / "05_shear.png")
    figs["ti"] = save_png(rp.chart_ti(ti_hub, hub_h), fig / f"06_ti_{hub_h:g}m.png")
    if rose_hub is not None:
        figs["rose"] = save_png(rp.chart_rose(rose_hub, f"Direction frequency at {hub_h:g} m, %"), fig / "07_rose.png")
    for h in (100.0, hub_h):
        if h in hs:
            row = height_stats[height_stats["height_m"] == h].iloc[0]
            figs[f"weibull_{h:g}"] = save_png(rp.chart_weibull(hs[h]["ws"].dropna(), row["weibull_A"], row["weibull_k"], h),
                                              fig / f"08_weibull_{h:g}m.png")
    figs["density"] = save_png(rp.chart_density(an["density"]), fig / "09_density.png")

    stats = {"site": name, "station": infos[0]["station"], "lat": lat, "lon": lon, "elevation_m": elev,
             "period": f"{data.index[0]:%d %b %Y} – {data.index[-1]:%d %b %Y}",
             "monthly": {f"{h:g}": {str(p): round(float(v), 4) for p, v in momm_ok(mon[h])[1].items()} for h in hs},
             "momm": {f"{h:g}": momm_ok(mon[h])[0] for h in hs},
             "shear": {f"{lo:g}-{hi:g}": v for (lo, hi), v in alpha.items()},
             "rose_height_m": hub_h,
             "rose": [[float(c), float(r["freq_%"]), float(r["mean_ws"])] for c, r in rose_hub.iterrows()] if rose_hub is not None else [],
             "energy_rose": [[float(c), float(r["energy_%"])] for c, r in rose_hub.iterrows()] if rose_hub is not None else [],
             "ti_rep_15": float(ti_hub.loc[15, "rep_TI"]) if 15 in ti_hub.index else None,
             "density": {f"{z:g}": d.get("mean") for z, d in dens.items()},
             "monthly_lt": {f"{h:g}": {str(p): round(float(v), 4) for p, v in ok.items()} for h, ok in lt_used.items()},
             "reference_dropped": ref_dropped, "lt_basis_height": basis_h, "run_id": run_dir.name,
             "long_term": lt_rows, "daily_r": daily_r if a.power_daily else None,
             "reference_note": ref_note if a.power_daily else "", "last_reference_day": str(last_merra) if a.power_daily else "",
             "findings": F, "reader": READER, "qc_version": QC_VERSION, "inputs_sha256": inputs["inventory_sha256"]}
    (run_dir / "stats.json").write_text(json.dumps(stats, indent=1, default=str), encoding="utf-8")
    (run_dir / "findings.txt").write_text("\n".join(f"{i + 1}. {t}" for i, t in enumerate(F)) + "\n", encoding="utf-8")

    summary = pd.DataFrame({"Item": ["Site", "Station / logger", "Position", "Elevation", "Period", "Records",
                                     "Logger programs", "Reader", "Checks and statistics", "Input fingerprint", "Disclaimer"],
                            "Value": [name, f"{infos[0]['station']}; {infos[0]['model']} s/n {infos[0]['serial']}",
                                      f"{lat:.6f}, {lon:.6f} (logger GPS median)" if lat is not None else "",
                                      f"{elev} m ({settings.get('source', '')})" if elev is not None else "",
                                      stats["period"], f"{len(data):,}", ", ".join(programs), READER,
                                      f"Bohemian Wind QC v{QC_VERSION} (bohemian_wind.qc, bohemian_wind.analysis)",
                                      inputs["inventory_sha256"], DISCLAIMER]})
    sheets = {"Summary": summary, "Findings": pd.DataFrame({"#": range(1, len(F) + 1), "finding": F}),
              "Heights": height_stats, "Long-term": lt_tab,
              "Monthly WS": pd.concat([_xl(mon[h]).add_prefix(f"{h:g} m ") for h in sorted(hs, reverse=True)],
                                      axis=1).reset_index(names="month"),
              "Diurnal WS": an["diurnal_ws"].reset_index(names="hour"),
              "Shear": shear_tab, "Shear by hour": shear_hourly.reset_index(names="hour"),
              "Shear test": an["shear_test"], f"TI {hub_h:g} m": ti_hub.reset_index(),
              f"Rose {hub_h:g} m": rose_hub.reset_index() if rose_hub is not None else pd.DataFrame(),
              "Mast wake": wake, "Height relabel check": reloc, "Dead sensor spells": spells,
              "Flag summary": flag_summary(qcr), "Flag periods": flag_periods(qcr),
              "Recovery % by month": rec.reset_index().rename(columns={"index": "sensor"}),
              "Measurement points": points_table, "Site visits": visits_table, "Logger programs": cfg,
              "Sensor table": an["sensor_table"],
              # a mast with no thermometer has no air density (v0.2.0-dev stopped here with KeyError 'monthly')
              "Air density": pd.DataFrame() if "monthly" not in an["density"] else
              pd.DataFrame({"month": [str(p) for p in an["density"]["monthly"].index],
                            "density_logged_kg_m3": an["density"]["monthly"].to_numpy(),
                            **{f"density_{z:g}m_kg_m3": d["rho"].groupby(d["rho"].index.to_period("M")).mean()
                               .reindex(an["density"]["monthly"].index).to_numpy() for z, d in dens.items() if d}})}
    for h, t in lt_tables.items():
        sheets[f"LT months {h:g} m"] = t.reset_index(names="month").assign(month=lambda d: d["month"].astype(str))
    if len(vane_check):
        sheets["Vane vs reference"] = vane_check.reset_index(names="day")
    for pr in an.get("pairs", []):
        if "sectors" in pr:
            sheets[f"Pair {pr['height']:g} m"] = pr["sectors"].reset_index()
    if len(L["conflicts"]):
        sheets["Conflicting records"] = L["conflicts"]
    with pd.ExcelWriter(run_dir / "summary.xlsx", engine="openpyxl") as xw:
        for nm, df in sheets.items():
            if df is None or not len(df):
                continue
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [" ".join(str(x) for x in c if str(x)) for c in df.columns]
            df.to_excel(xw, sheet_name=nm[:31], index=False)

    ts = pd.DataFrame(index=data.index)
    for h in sorted(hs, reverse=True):
        ts[f"ws_{h:g}m"] = hs[h]["ws"].round(3)
        ts[f"sd_{h:g}m"] = hs[h]["sd"].round(3)
    for s in sensors_all:
        if s.kind == "wind_direction":
            ts[f"wd_{s.height:g}m"] = data[s.avg_col].where(qcr["valid"][s.label]).round(1)
    for s in sensors:
        if s.kind in ("temperature", "humidity", "pressure"):
            ts[f"{s.label.replace(' ', '_')}"] = data[s.avg_col].where(qcr["valid"][s.label]).round(2)
    for s in sensors_all:
        ts[f"valid_{s.label.replace(' ', '_')}"] = qcr["valid"][s.label].astype(int)
    ts.index.name = "Timestamp"
    with open(run_dir / "timeseries_by_height.csv", "wb") as fh:
        fh.write(ts.to_csv(lineterminator="\n").encode("utf-8"))
    meta = {"reader": READER, "qc_version": QC_VERSION, "generated": f"{datetime.now():%Y-%m-%d %H:%M}",
            "inputs": inputs, "folders": [str(f) for f in folders], "settings": settings, "programs": programs,
            "gps": pos, "visits": len(windows), "figures": figs, "seconds": round(time.time() - t0, 1)}
    (run_dir / "run.json").write_text(json.dumps(meta, indent=1, default=str), encoding="utf-8")
    (run_dir / "flags.json").write_text(fsum.to_json(orient="records"), encoding="utf-8")
    try:  # hand-off for the later steps (long-term, maps, layout, yield); never stops a run
        from bohemian_wind import climate as _climate  # noqa: PLC0415
        _climate.save(run_dir / "climate.json", _climate.from_analysis(
            an, qcr["time"], {"name": name, "lat": lat, "lon": lon, "elevation_m": elev}, source_format=L["format"]))
    except Exception as e:  # noqa: BLE001
        say(f"Wind climate file not written: {e}")
    say(f"Done in {time.time() - t0:.0f} s. Outputs: {run_dir}")
    return run_dir


if __name__ == "__main__":
    main()
