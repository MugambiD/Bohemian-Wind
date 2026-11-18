"""Quality checks on merged met mast data.

Each sensor gets boolean flag series. A record is valid for analysis when none of
its flags (FLAG_ORDER) is set. Thresholds are screening
defaults set here in one place; the method statement should fix them per site.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import points
from .points import Sensor, kind_of  # noqa: F401  (re-exported for callers)
from .sympro import Dataset

# Plausible ranges for 10-minute averages, by measured quantity
RANGES = {
    "wind_speed": (0.0, 75.0),
    "wind_direction": (0.0, 360.0),
    "temperature": (-40.0, 60.0),
    "humidity": (0.0, 105.0),
    "pressure": (500.0, 1100.0),  # hPa; scaled for kPa below
    "voltage": (0.0, 30.0),
}
PRESSURE_BAND_HPA = 60.0  # with a site elevation: standard-atmosphere pressure ± this
# v0.1.4: the range check also covers the other statistics of a record, not the average alone. A logger glitch can
# leave a believable 10-minute average next to a gust of 1,500 m/s and a standard deviation of 60 m/s (seen on two third-party masts,
# 2012-13); the average of such a record is contaminated, so the whole record is flagged 'range'.
GUST_MAX_MS = 90.0  # highest believable Max / Gust of a cup: above the 70 m/s extreme gust of IEC class I
SD_MAX = {"wind_speed": 15.0, "wind_direction": 105.0}  # 10-minute SD; a vane's SD cannot pass about 104 deg
STAT_RANGE_KINDS = ("wind_speed", "temperature", "pressure", "humidity")  # Min / Max / Gust checked against the range
NO_SIGNAL_V = {"humidity": 0.05, "pressure": 0.05, "temperature": 0.02}  # sensor output, V
SPIKE_LIMIT = {"wind_speed": 8.0, "temperature": 8.0, "pressure": 10.0}  # departure from 5-record median
STUCK_RECORDS = {"wind_speed": 6, "wind_direction": 6, "temperature": 36, "pressure": 144, "humidity": 72}
REF_WS_MOVING = 3.0  # m/s: other cups say the wind is blowing
FLAG_ORDER = ("missing", "conflict", "maintenance", "no_signal", "range", "inconsistent", "stuck", "spike")
FLAG_TEXT = {
    "missing": "No value recorded",
    "conflict": "Overlapping logger files hold different records for this timestamp (part-interval records "
                "written when a file restarted); none is used",
    "maintenance": "Recorded while the logger configuration was being changed (site visit)",
    "no_signal": "Sensor output at its zero value: unpowered, disconnected or failed",
    "range": "Outside the plausible range: the average, or the record's Min, Max or gust (cups: gust above 90 m/s), "
             "or its standard deviation (cups above 15 m/s, vanes above 105 deg). Pressure: within 60 hPa of the "
             "standard atmosphere at the site elevation",
    "inconsistent": "Min/Max/SD inconsistent with the average",
    "stuck": "Sensor not responding: flat signal while the wind blows (cups, vanes) or for 6-24 h (other sensors)",
    "spike": "Sudden jump away from neighbouring records",
}


def build_sensors(ds: Dataset) -> list[Sensor]:
    """Measurement points across configuration changes (see points.py). Replaces ds.data
    with one column per point and statistic; the logger columns stay in ds.raw."""
    return points.build(ds)


def _runs(mask: pd.Series) -> list[tuple]:
    """(start, end, n_records) for each run of True values."""
    m = mask.to_numpy(dtype=bool)
    if not m.any():
        return []
    idx = mask.index
    edges = np.diff(np.concatenate(([0], m.astype(np.int8), [0])))
    starts, ends = np.where(edges == 1)[0], np.where(edges == -1)[0] - 1
    return [(idx[s], idx[e], int(e - s + 1)) for s, e in zip(starts, ends)]


def _long_runs(cond: pd.Series, min_len: int) -> pd.Series:
    out = pd.Series(False, index=cond.index)
    for s, e, n in _runs(cond):
        if n >= min_len:
            out.loc[s:e] = True
    return out


def timestamp_qc(index: pd.DatetimeIndex) -> dict:
    diffs = pd.Series(index[1:] - index[:-1]).dt.total_seconds()
    interval = int(diffs[diffs > 0].mode().iloc[0]) if len(diffs) else 600
    freq = pd.Timedelta(seconds=interval)
    grid = pd.date_range(index[0].floor(freq), index[-1], freq=freq)
    missing = grid.difference(index)
    # consecutive missing grid timestamps form one gap
    gap_list, cur = [], None
    for t in missing:
        if cur and t - cur[1] == freq:
            cur[1] = t
            cur[2] += 1
        else:
            if cur:
                gap_list.append(tuple(cur))
            cur = [t, t, 1]
    if cur:
        gap_list.append(tuple(cur))
    off_grid = int(((index - index[0].floor(freq)) % freq != pd.Timedelta(0)).sum())
    return {"interval_s": interval, "grid": grid, "expected": len(grid), "present": len(index),
            "missing": len(missing), "gaps": gap_list, "off_grid": off_grid,
            "start": index[0], "end": index[-1]}


def site_pressure(site: dict) -> float | None:
    """Standard-atmosphere pressure at the site elevation, hPa."""
    try:
        elev = float(str(site.get("Elevation", "")).strip())
    except ValueError:
        return None
    if not np.isfinite(elev) or not -500 < elev < 6000:
        return None
    return 1013.25 * (1 - 2.25577e-5 * elev) ** 5.25588


def plausible_range(s: Sensor, site: dict) -> tuple[float, float]:
    lo, hi = RANGES.get(s.kind, (-np.inf, np.inf))
    if s.kind == "pressure":
        p0 = site_pressure(site)
        if p0 is not None:
            lo, hi = p0 - PRESSURE_BAND_HPA, p0 + PRESSURE_BAND_HPA
        if s.units.lower() == "kpa":
            lo, hi = lo / 10, hi / 10
    return lo, hi


def no_signal(s: Sensor, x: pd.Series) -> pd.Series:
    """True where an analog sensor reads its zero-output value (scale factor and offset known)."""
    lim = NO_SIGNAL_V.get(s.kind)
    slope, off = points._num(s.meta.get("Scale Factor")), points._num(s.meta.get("Offset"))
    if lim is None or not slope or off is None:
        return pd.Series(False, index=x.index)
    return ((x - off) / slope).abs() < lim


def maintenance_mask(index: pd.DatetimeIndex, windows) -> pd.Series:
    m = pd.Series(False, index=index)
    for a, b, _n in windows:
        m.loc[a:b] = True
    return m


def run_qc(ds: Dataset, sensors: list[Sensor]) -> dict:
    data = ds.data
    ws_avgs = [data[s.avg_col] for s in sensors if s.kind == "wind_speed" and s.avg_col in data]
    visits = maintenance_mask(data.index, ds.points.get("visits", []))
    conflict = pd.Series(data.index.isin(ds.conflict_times), index=data.index)
    flags, valid = {}, {}
    for s in sensors:
        if not s.avg_col or s.avg_col not in data:
            continue
        x = data[s.avg_col]
        f = {}
        f["missing"] = x.isna() & ~conflict
        f["conflict"] = conflict.copy()
        f["maintenance"] = visits & x.notna()
        f["no_signal"] = no_signal(s, x) & x.notna()
        lo, hi = plausible_range(s, ds.site)
        rng = (x < lo) | (x > hi)
        if s.kind in STAT_RANGE_KINDS:
            top = GUST_MAX_MS if s.kind == "wind_speed" else hi
            for stat in ("Min", "Max", "Gust"):
                c = s.cols.get(stat)
                if c is not None and c in data:
                    rng |= (data[c] < lo) | (data[c] > top)
        if s.kind in SD_MAX and s.cols.get("SD") in data:
            rng |= data[s.cols["SD"]] > SD_MAX[s.kind]
        f["range"] = x.notna() & rng
        inc = pd.Series(False, index=x.index)
        if "Min" in s.cols and s.cols["Min"] in data:
            inc |= data[s.cols["Min"]] > x + 1e-6
        if "Max" in s.cols and s.cols["Max"] in data:
            inc |= data[s.cols["Max"]] < x - 1e-6
        if "SD" in s.cols and s.cols["SD"] in data:
            inc |= data[s.cols["SD"]] < 0
        f["inconsistent"] = inc & x.notna()
        flat = x.diff().abs() < 1e-6
        if s.kind in ("wind_speed", "wind_direction"):
            sd = data[s.cols["SD"]] if "SD" in s.cols and s.cols["SD"] in data else None
            still = (sd.abs() < 1e-6) if sd is not None else flat
            others = [w for w in ws_avgs if w.name != s.avg_col]
            if others:
                ref = pd.concat(others, axis=1).median(axis=1)
                cond = still & (ref >= REF_WS_MOVING)
                f["stuck"] = _long_runs(cond.fillna(False), STUCK_RECORDS[s.kind])
            else:
                f["stuck"] = _long_runs(still.fillna(False), 36)
        elif s.kind in ("temperature", "pressure", "humidity"):
            cond = flat.fillna(False)
            if s.kind == "humidity":
                cond &= x < 99.5  # saturated air legitimately sits at 100 %
            f["stuck"] = _long_runs(cond, STUCK_RECORDS[s.kind])
        else:
            f["stuck"] = pd.Series(False, index=x.index)
        if s.kind in SPIKE_LIMIT:
            med = x.rolling(5, center=True, min_periods=3).median()
            f["spike"] = (x - med).abs() > SPIKE_LIMIT[s.kind]
        else:
            f["spike"] = pd.Series(False, index=x.index)
        for k in f:
            f[k] = f[k].fillna(False).astype(bool)
        bad = pd.Series(False, index=x.index)
        for name in FLAG_ORDER:
            bad |= f[name]
        flags[s.label] = f
        valid[s.label] = ~bad
    return {"flags": flags, "valid": valid, "time": timestamp_qc(data.index)}


def recovery_by_month(qc: dict, sensors: list[Sensor]) -> pd.DataFrame:
    """Share of expected records that are valid, per sensor and calendar month."""
    grid = qc["time"]["grid"]
    expected = pd.Series(1, index=grid).groupby(grid.to_period("M")).sum()
    rows = {}
    for s in sensors:
        if s.label not in qc["valid"]:
            continue
        v = qc["valid"][s.label]
        rows[s.label] = v.groupby(v.index.to_period("M")).sum().reindex(expected.index, fill_value=0) / expected
    out = pd.DataFrame(rows).T
    out.columns = [str(c) for c in out.columns]
    return out * 100.0


def flag_periods(qc: dict, top: int = 200, join_gap_h: float = 24.0) -> pd.DataFrame:
    """Flagged periods per sensor and flag; runs less than join_gap_h apart are merged into one."""
    rows, gap = [], pd.Timedelta(hours=join_gap_h)
    for label, f in qc["flags"].items():
        for name in FLAG_ORDER:
            cur = None
            for s, e, n in _runs(f[name]):
                if cur and s - cur["end"] <= gap:
                    cur["end"], cur["flagged_records"] = e, cur["flagged_records"] + n
                else:
                    if cur:
                        rows.append(cur)
                    cur = {"sensor": label, "flag": name, "start": s, "end": e, "flagged_records": n}
            if cur:
                rows.append(cur)
    df = pd.DataFrame(rows, columns=["sensor", "flag", "start", "end", "flagged_records"])
    df["span_days"] = (df["end"] - df["start"]).dt.total_seconds() / 86400.0 if len(df) else []
    # ties broken by sensor, flag and start, so every platform lists the same rows in the same order
    df = df.sort_values(["flagged_records", "sensor", "flag", "start"], ascending=[False, True, True, True], kind="stable")
    return df.head(top).reset_index(drop=True)


def flag_summary(qc: dict) -> pd.DataFrame:
    rows = []
    for label, f in qc["flags"].items():
        n = len(f["missing"])
        row = {"sensor": label, "records": n}
        for name in FLAG_ORDER:
            row[name] = int(f[name].sum())
        row["valid_%"] = 100.0 * qc["valid"][label].sum() / max(qc["time"]["expected"], 1)
        rows.append(row)
    return pd.DataFrame(rows)
