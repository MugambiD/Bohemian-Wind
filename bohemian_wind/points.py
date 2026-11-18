"""Measurement points: one series per physical sensor position, assembled across
logger configuration changes.

A SymphoniePRO logger starts a new file whenever its configuration changes, and the
column names carry each channel's height and boom as configured at the time. When a
technician re-wires sensors to other channels or corrects their settings, the same
instrument appears under different column names, and one channel can carry different
instruments over the campaign. Grouping columns by channel number alone mixes them up.

Rules applied here:
- Each file's Sensor History gives every channel's configuration for that file, and
  each record is tied to the file it came from.
- A segment is the set of records of one channel under one configuration.
- An instrument is identified by its type and serial number. Its home is the height
  and boom of its most recent configuration (the one confirmed at the latest visit).
  All its segments go to the measurement point at its home position.
- Segments without a usable serial number go to the position in their own column names.
- Earlier segments recorded with a different calibration (slope and offset, or a
  vane's total direction offset) are re-expressed with the instrument's latest values.
- Configuration changes closer than 24 h apart form one site visit; records from one
  hour before the first change to one hour after the last are flagged as maintenance.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .sympro import Dataset, column_stem, parse_column

SHORT = {"wind_speed": "WS", "wind_direction": "WD", "temperature": "T", "humidity": "RH",
         "pressure": "P", "voltage": "V"}
KIND_ORDER = ("wind_speed", "wind_direction", "temperature", "humidity", "pressure", "voltage", "other")
LINEAR_KINDS = ("wind_speed", "temperature", "humidity", "pressure")
LEVEL_STATS = ("Avg", "Min", "Max", "Gust")
DIRECTION_STATS = ("Avg", "Min", "Max", "GustDir")
NO_SERIAL = {"", "0", "-", "n/a", "na", "none", "null", "unknown"}
CFG_KEYS = ("Type", "Description", "Serial Number", "Height", "Bearing", "Vane Mounting Angle",
            "Scale Factor", "Offset", "Units", "Total Direction Offset")
VISIT_GAP = pd.Timedelta(hours=24)
VISIT_PAD = pd.Timedelta(hours=1)
MIN_POINT_RECORDS = 144  # less than a day of data: listed, not analysed
OVERLAP_RECORDS = 6      # two instruments sharing a position for longer than this are kept apart


def kind_of(ch_type: str, units: str, desc: str) -> str:
    t, u, d = ch_type.lower(), units.strip(), desc.lower()
    if t.startswith("anem"):
        return "wind_speed"
    if t.startswith("vane") or u.lower() in ("deg", "°"):
        return "wind_direction"
    if t in ("calculated", "counter", "totalizer"):
        return "other"
    if u in ("C", "°C", "degC", "?C", "deg C") or "temp" in d:
        return "temperature"
    if u.lower() in ("%rh", "rh") or "humi" in d or " rh" in d:
        return "humidity"
    if u.lower() in ("hpa", "mb", "mbar", "millibar", "kpa") or "baro" in d:
        return "pressure"
    if u.lower() in ("v", "volts"):
        return "voltage"
    return "other"


def _num(v):
    try:
        x = float(str(v).strip())
    except (TypeError, ValueError):
        return None
    return x if np.isfinite(x) else None


def direction_offset(cfg: dict):
    """A vane's total direction offset: as exported, or offset + boom bearing + mounting angle."""
    tdo = _num(cfg.get("Total Direction Offset"))
    if tdo is not None:
        return tdo
    parts = [_num(cfg.get(k)) for k in ("Offset", "Bearing", "Vane Mounting Angle")]
    return sum(parts) if all(p is not None for p in parts) else None


@dataclass
class Sensor:
    """One measurement point, as used by the checks and the analysis."""
    channel: int
    kind: str
    ch_type: str
    height: float | None
    bearing: str
    units: str
    cols: dict
    meta: dict = field(default_factory=dict)
    name: str = ""
    channels: list = field(default_factory=list)
    serials: list = field(default_factory=list)
    boom_deg: float | None = None
    segments: list = field(default_factory=list)

    @property
    def label(self) -> str:
        if self.name:
            return self.name
        short = SHORT.get(self.kind, self.ch_type)
        h = f" {self.height:g} m" if self.height is not None else ""
        b = f" {self.bearing}" if self.bearing else ""
        return f"Ch{self.channel} {short}{h}{b}"

    @property
    def avg_col(self):
        return self.cols.get("Avg")


@dataclass
class Segment:
    channel: int
    stem: str
    ch_type: str
    units: str
    cfg: dict
    files: list
    kind: str = ""
    height: float | None = None
    bearing: str = ""
    serial: str = ""
    cols: dict = field(default_factory=dict)
    rows: np.ndarray | None = None
    n: int = 0
    start: pd.Timestamp | None = None
    end: pd.Timestamp | None = None
    point: str = ""
    action: str = ""
    excluded: str = ""

    @property
    def instrument(self):
        if self.serial and self.serial.lower() not in NO_SERIAL:
            return ("serial", self.kind, self.serial)
        return ("channel", self.kind, self.channel, self.stem)


def _segments(ds: Dataset) -> list[Segment]:
    stems: dict[str, dict] = {}
    for col, info in ds.columns.items():
        stems.setdefault(column_stem(col), {})[info.stat] = col
    if ds.file_info and ds.source is not None:
        src = ds.source.to_numpy()
        listing = [(fi, f["prefixes"].items(), f["channels"]) for fi, f in enumerate(ds.file_info)]
    else:  # no per-file information: one segment per column stem
        src = np.zeros(len(ds.data), dtype=int)
        listing = [(0, [(parse_column(next(iter(c.values()))).channel, st) for st, c in stems.items()], {})]
    by_key: dict[tuple, Segment] = {}
    for fi, items, channels in listing:
        for ch, stem in items:
            cfg = channels.get(ch, {})
            key = (ch, stem) + tuple(str(cfg.get(k, "")).strip() for k in CFG_KEYS)
            seg = by_key.get(key)
            if seg is None:
                info = parse_column(next(iter(stems.get(stem, {}).values()), stem + "_Avg_"))
                seg = Segment(channel=ch, stem=stem, ch_type=info.ch_type if info else "",
                              units=info.units if info else str(cfg.get("Units", "")), cfg=cfg, files=[])
                seg.kind = kind_of(seg.ch_type, seg.units, str(cfg.get("Description", "")))
                seg.height = info.height if info and info.height is not None else _num(cfg.get("Height"))
                seg.bearing = info.bearing if info else ""
                seg.serial = str(cfg.get("Serial Number", "")).strip()
                seg.cols = stems.get(stem, {})
                by_key[key] = seg
            seg.files.append(fi)
    segs = list(by_key.values())
    data = ds.data
    for seg in segs:
        seg.rows = np.isin(src, seg.files)
        avg = seg.cols.get("Avg")
        has = seg.rows & data[avg].notna().to_numpy() if avg in data else np.zeros(len(data), dtype=bool)
        seg.n = int(has.sum())
        idx = data.index[has] if seg.n else data.index[seg.rows]
        seg.start, seg.end = (idx[0], idx[-1]) if len(idx) else (None, None)
        if str(seg.cfg.get("Description", "")).strip().lower().startswith("no sensor"):
            seg.excluded = "channel set to 'No Sensor'"
        elif seg.n == 0:
            seg.excluded = "no values recorded"
    return segs


def _homes(segs: list[Segment]) -> dict:
    """Latest configuration of each instrument (by the latest file it appears in)."""
    homes = {}
    for seg in segs:
        if seg.excluded or seg.instrument[0] != "serial":
            continue
        cur = homes.get(seg.instrument)
        if cur is None or max(seg.files) > max(cur.files):
            homes[seg.instrument] = seg
    return homes


def _harmonise(seg: Segment, home: Segment, stat: str, x: pd.Series) -> pd.Series:
    if seg is home or seg.instrument != home.instrument:
        return x
    s0, o0 = _num(seg.cfg.get("Scale Factor")), _num(seg.cfg.get("Offset"))
    s1, o1 = _num(home.cfg.get("Scale Factor")), _num(home.cfg.get("Offset"))
    if seg.kind in LINEAR_KINDS:
        if None in (s0, o0, s1, o1) or s0 == 0 or (s0 == s1 and o0 == o1):
            return x
        if stat == "SD":
            return x * abs(s1 / s0)
        if stat in LEVEL_STATS:
            return (x - o0) / s0 * s1 + o1
        return x
    if seg.kind == "wind_direction":
        t0, t1 = direction_offset(seg.cfg), direction_offset(home.cfg)
        if t0 is None or t1 is None:
            return x
        k = (s1 / s0) if (s0 and s1) else 1.0
        if t0 == t1 and k == 1.0:
            return x
        if stat == "SD":
            return x * k
        if stat in DIRECTION_STATS:
            return (((x - t0) % 360.0) * k + t1) % 360.0
    return x


def _describe_change(seg: Segment, home: Segment) -> str:
    notes = []
    if seg.channel != home.channel:
        notes.append(f"moved from Ch{seg.channel} to Ch{home.channel}")
    if seg.stem != home.stem and (seg.height != home.height or seg.bearing != home.bearing):
        notes.append(f"logged as {seg.height:g} m {seg.bearing}" if seg.height is not None else f"logged as {seg.bearing}")
    s0, o0 = _num(seg.cfg.get("Scale Factor")), _num(seg.cfg.get("Offset"))
    s1, o1 = _num(home.cfg.get("Scale Factor")), _num(home.cfg.get("Offset"))
    if seg.kind in LINEAR_KINDS and None not in (s0, o0, s1, o1) and s0 != 0 and (s0, o0) != (s1, o1):
        notes.append(f"re-scaled from slope {s0:g}, offset {o0:g} to {s1:g}, {o1:g}")
    if seg.kind == "wind_direction":
        t0, t1 = direction_offset(seg.cfg), direction_offset(home.cfg)
        if t0 is not None and t1 is not None and t0 != t1:
            notes.append(f"direction offset {t0:g}° re-referenced to {t1:g}° ({t1 - t0:+.2f}°)")
    return "; ".join(notes)


def config_changes(ds: Dataset) -> list[tuple]:
    """(time, channel, change) wherever a channel's settings differ from those in the previous file.

    SymPRO stamps every channel's Effective Date with the start of each exported file, so
    changes are found by comparing consecutive files, not by reading that date.
    """
    if ds.source is None or not ds.file_info or ds.data.empty:
        return []
    firsts = pd.Series(ds.data.index).groupby(ds.source.to_numpy()).min()
    prev, events, started = {}, [], False
    for fi in firsts.sort_values().index:
        chans, start = ds.file_info[fi]["channels"], firsts[fi]
        for ch in sorted(set(prev) | set(chans)):
            cur = tuple(str(chans[ch].get(k, "")).strip() for k in CFG_KEYS) if ch in chans else None
            if started and prev.get(ch, None) != cur and not (ch not in prev and cur is None):
                t = pd.to_datetime(chans[ch].get("Effective Date", ""), errors="coerce") if cur else pd.NaT
                if pd.isna(t) or t > start or t < start - pd.Timedelta(days=1):
                    t = start
                kind = "removed" if cur is None else ("added" if prev.get(ch) is None else "changed")
                events.append((t, ch, kind))
            prev[ch] = cur
        started = True
    return sorted(events)


def visit_windows(ds: Dataset) -> list[tuple]:
    """(start, end, changes) for each cluster of configuration changes inside the record."""
    out = []
    for t, _ch, _kind in config_changes(ds):
        if out and t - out[-1][1] <= VISIT_GAP:
            out[-1] = (out[-1][0], t, out[-1][2] + 1)
        else:
            out.append((t, t, 1))
    return [(a - VISIT_PAD, b + VISIT_PAD, n) for a, b, n in out]


def _label(kind, height, bearing, channel, extra=""):
    short = SHORT.get(kind, "X")
    h = f" {height:g} m" if height is not None else ""
    b = f" {bearing}" if bearing else ""
    return f"{short}{h}{b}{extra} (Ch{channel})"


def build(ds: Dataset) -> list[Sensor]:
    """Assemble measurement points. Replaces ds.data with one column per point and statistic;
    the logger columns move to ds.raw and the mapping goes to ds.points."""
    segs = _segments(ds)
    homes = _homes(segs)
    groups: dict[tuple, list[Segment]] = {}
    for seg in segs:
        if seg.excluded:
            continue
        home = homes.get(seg.instrument, seg)
        key = (seg.kind, round(home.height, 1) if home.height is not None else None, home.bearing)
        groups.setdefault(key, []).append(seg)
    data = ds.data
    # two different instruments recording at the same position at the same time stay apart
    final: dict[tuple, list[Segment]] = {}
    for key, members in groups.items():
        inst = {}
        for seg in members:
            avg = seg.cols.get("Avg")
            has = seg.rows & data[avg].notna().to_numpy() if avg in data else np.zeros(len(data), dtype=bool)
            inst[seg.instrument] = inst.get(seg.instrument, np.zeros(len(data), dtype=bool)) | has
        keys = list(inst)
        clash = any((inst[a] & inst[b]).sum() > OVERLAP_RECORDS for i, a in enumerate(keys) for b in keys[i + 1:])
        if clash:
            for seg in members:
                final.setdefault(key + (seg.instrument,), []).append(seg)
        else:
            final[key] = members
    cols, sensors, excluded_points = {}, [], []
    for key, members in final.items():
        members.sort(key=lambda s: (s.start is None, s.start))
        latest = max(members, key=lambda s: max(s.files))
        home = homes.get(latest.instrument, latest)
        extra = ""
        if len(key) > 3:
            extra = f" #{latest.serial}" if latest.instrument[0] == "serial" else f" {latest.stem}"
        label = _label(key[0], home.height, home.bearing, home.channel, extra)
        point_cols = {}
        stats = sorted({st for seg in members for st in seg.cols})
        for st in stats:
            series = None
            for seg in members:
                col = seg.cols.get(st)
                if col is None or col not in data:
                    continue
                x = data[col].where(seg.rows)
                x = _harmonise(seg, homes.get(seg.instrument, seg), st, x)
                series = x if series is None else series.combine_first(x)
            if series is not None:
                name = f"{label}|{st}"
                cols[name] = series
                point_cols[st] = name
        for seg in members:
            seg.point = label
            seg.action = _describe_change(seg, homes.get(seg.instrument, seg)) or "as logged"
        n = int(cols[point_cols["Avg"]].notna().sum()) if "Avg" in point_cols else 0
        meta = dict(home.cfg)
        sensor = Sensor(channel=home.channel, kind=key[0], ch_type=home.ch_type, height=home.height,
                        bearing=home.bearing, units=home.units, cols=point_cols, meta=meta, name=label,
                        channels=sorted({s.channel for s in members}),
                        serials=list(dict.fromkeys(s.serial for s in members if s.serial)),
                        boom_deg=_num(home.cfg.get("Bearing")), segments=members)
        if n < min(MIN_POINT_RECORDS, max(1, len(data) // 2)):
            excluded_points.append(label)
            for seg in members:
                seg.excluded = f"not analysed: {n} records at this position (temporary configuration)"
            continue
        sensors.append(sensor)
    order = {k: i for i, k in enumerate(KIND_ORDER)}
    sensors.sort(key=lambda s: (s.channel, order.get(s.kind, 99), -(s.height or 0)))
    point_data = pd.DataFrame(cols, index=data.index)
    keep = [c for s in sensors for c in s.cols.values()]
    ds.raw = data
    ds.data = point_data[keep]
    ds.points = {"segments": segment_table(segs, [s.label for s in sensors] + excluded_points),
                 "visits": visit_windows(ds), "excluded": excluded_points,
                 "moved": sorted({s.point for s in segs if not s.excluded and "moved from" in s.action}),
                 "rescaled": sum(1 for s in segs if not s.excluded and ("re-scaled" in s.action or "re-referenced" in s.action))}
    return sensors


def segment_table(segs: list[Segment], order: list | None = None) -> pd.DataFrame:
    rank = {p: i for i, p in enumerate(order or [])}
    rows = []
    for s in sorted(segs, key=lambda s: (rank.get(s.point, len(rank)), s.point or "~",
                                         s.start or pd.Timestamp.max, s.channel)):
        rows.append({"point": s.point or "–", "channel": s.channel, "serial": s.serial,
                     "from": s.start, "to": s.end, "records": s.n, "action": s.excluded or s.action,
                     "logger columns": s.stem, "description": s.cfg.get("Description", ""),
                     "scale_factor": s.cfg.get("Scale Factor", ""), "offset": s.cfg.get("Offset", ""),
                     "direction_offset": direction_offset(s.cfg) if s.kind == "wind_direction" else None})
    return pd.DataFrame(rows)
