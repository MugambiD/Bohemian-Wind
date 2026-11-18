"""SecondWind Nomad2 "Multi-Track Export" reader (Bohemian Wind QC v0.2 prototype).

Reads the text export of a Nomad2 logger and returns it in the shape loggers/toa5_mast.py works with, so the same
checks, statistics and outputs run on it:

  python tools/toa5_mast.py "<...>\\mast_1_raw.dat" --out "<outputs>" --name "Mast 1" --settings mast1.json

What the export looks like, and what this reader does about it
- Preamble: "Multi-Track Export - <id> <site>", "Site Name: <site>", "Nomad2 Name:<logger id>", "Notes:".
- One header line: TimeStamp,<port>-<label>(<unit>)-<n> min  <statistic>,...  e.g.
  C1-p1_anemo_80mw(m/s)-10 min  Average. Ports: C = counter (cups), A = analog, BAT / XPwr = supply.
  Statistics: Average, Max Value, Min Value, Std Dev, Vec Average, Vec Std Dev, Sample.
  The last header name can be cut short ("Sampl", "Averag"); it is matched by its beginning.
- Every data line ends with a comma. Midnight is written as a date with no time ("26.02.2012").
- The export has no blanks. A record the logger did not store is a line of zeros in every column: those lines are
  dropped, so the kit counts them as missing. A column holds zeros before its channel was set up and after it was
  removed: those zeros become blanks. A column that is zero throughout (a channel set-up that never logged) is dropped.
- One port can appear under several names when its set-up changed (A7-Humidity "Average", later "Sample"): columns
  that describe the same sensor, height and statistic are joined into one series.
- Heights are read from the label ("80mw" = 80 m, boom tag w). Labels without a height (vanes named "Thies",
  thermometer, barometer, hygrometer) take theirs from the site settings file:
      "channels": {"A4": {"height_m": 78}, "A5": {"height_m": 40}, "A6": {"height_m": 2}}
  A sensor with no height anywhere is carried at 0 m and reported, never guessed.
- The directions in the export are as logged. A vane's north offset (boom bearing, dead band, declination) goes in
  the site settings and is added to every record of that port, modulo 360:
      "channels": {"A4": {"height_m": 78, "direction_offset_deg": -28}}
- Vertical anemometers (label with "gill" or "vert", m/s on an analog port) are kept as W points: checked for
  recovery, left out of the wind speed statistics.
- The export carries no coordinates, elevation, time zone, calibration or event log. Position and elevation come
  from the site settings; site visits cannot be detected from the file.
"""
from __future__ import annotations

import io
import re
from pathlib import Path

import numpy as np
import pandas as pd

READER = "nomad2.py 0.1.0-dev"
FORMAT = "SecondWind Nomad2 Multi-Track export"
HEAD = re.compile(r"^(?P<port>[A-Za-z]+\d*)-(?P<body>.*)-\s*(?P<minutes>\d+)\s*min\s+(?P<stat>.*)$")
UNIT = re.compile(r"^(?P<label>.*?)\((?P<unit>[^()]*)\)\s*$")
HEIGHT = re.compile(r"(?<![A-Za-z0-9.])(?P<h>\d+(?:\.\d+)?)\s*m(?P<boom>[a-z]{0,2})(?![a-z])", re.I)
STAT_NAMES = (("Vec Average", "Avg"), ("Vec Std Dev", "Std"), ("Average", "Avg"), ("Std Dev", "Std"),
              ("Max Value", "Max"), ("Min Value", "Min"), ("Sample", "Avg"))
PREFIX_UNITS = {"V": "m/s", "W": "m/s", "D": "Deg", "TA": "degC", "RH": "%rH", "PA": "hPa"}


def _decode(path) -> str:
    raw = Path(path).read_bytes()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def looks_like_nomad2(path) -> bool:
    try:
        with open(path, "rb") as fh:
            head = fh.read(4096).decode("latin-1", errors="replace")
    except OSError:
        return False
    return "Multi-Track Export" in head or ("Nomad2 Name" in head and "TimeStamp," in head)


def _stat(text: str) -> str | None:
    t = " ".join(text.split())
    for full, short in STAT_NAMES:
        if t == full:
            return short
    for full, short in STAT_NAMES:  # a header line cut short: "Sampl", "Averag", "Vec Std De"
        if len(t) >= 3 and full.startswith(t):
            return short
    return None


def _prefix(port: str, label: str, unit: str) -> str | None:
    """Sensor kind as the column prefix toa5_mast.py knows: V, D, TA, RH, PA; W = vertical wind; None = supply etc."""
    lab, u = label.lower(), unit.strip().lower().replace("°", "deg")
    if u in ("degc", "c") or "probe" in lab or "temp" in lab:
        return "TA"
    if "%" in u or "humid" in lab or lab.startswith("rh"):
        return "RH"
    if u in ("mb", "mbar", "hpa") or "baro" in lab or "setra" in lab or "press" in lab:
        return "PA"
    if u in ("deg", "degrees") or "vane" in lab or lab.startswith("dir"):
        return "D"
    if u == "m/s":
        if "gill" in lab or "vert" in lab:
            return "W"
        return "V"
    return None


def parse_header(name: str) -> dict | None:
    m = HEAD.match(name.strip())
    if not m:
        return None
    body = m["body"].strip()
    u = UNIT.match(body)
    label, unit = (u["label"].strip(), u["unit"].strip()) if u else (body, "")
    stat = _stat(m["stat"])
    if stat is None:
        return None
    h = HEIGHT.search(label)
    return {"port": m["port"], "label": label, "unit": unit, "stat": stat, "stat_text": " ".join(m["stat"].split()),
            "minutes": int(m["minutes"]), "height": float(h["h"]) if h else None,
            "boom": (h["boom"] or "").upper() if h else "", "prefix": _prefix(m["port"], label, unit)}


def _timestamps(col: pd.Series) -> pd.DatetimeIndex:
    t = col.astype(str).str.strip().str.strip('"')
    t = t.where(t.str.len() > 10, t + " 00:00:00")  # midnight is written without a time
    ts = pd.to_datetime(t, format="%d.%m.%Y %H:%M:%S", errors="coerce")
    if len(ts) and ts.isna().mean() > 0.5:
        ts = pd.to_datetime(t, errors="coerce", dayfirst=True)
    return pd.DatetimeIndex(ts, name="Timestamp")


def read_nomad2(path, channels: dict | None = None):
    """Returns (info, data) as toa5_mast.read_toa5 does: data has columns such as V1_80m_Avg, D1_78m_Std."""
    channels = {str(k).upper(): v for k, v in (channels or {}).items()}
    lines = _decode(path).splitlines()
    at = next((i for i, ln in enumerate(lines[:60]) if ln.startswith("TimeStamp,")), None)
    if at is None:
        raise ValueError("not a Nomad2 Multi-Track export (no 'TimeStamp,' header line)")
    pre = [ln.strip().strip('"') for ln in lines[:at] if ln.strip()]
    site = next((ln.split(":", 1)[1].strip() for ln in pre if ln.lower().startswith("site name")), "")
    logger = next((ln.split(":", 1)[1].strip() for ln in pre if ln.lower().startswith("nomad2 name")), "")
    names = [n.strip() for n in lines[at].split(",")]
    while names and not names[-1]:
        names.pop()
    raw = pd.read_csv(io.StringIO("\n".join(lines[at + 1:])), header=None, names=names, usecols=range(len(names)),
                      skipinitialspace=True, dtype=str, keep_default_na=False, on_bad_lines="skip")
    bad = sum(1 for ln in lines[at + 1:] if ln.strip()) - len(raw)
    index = _timestamps(raw.pop("TimeStamp"))
    val = raw.apply(pd.to_numeric, errors="coerce")
    val.index = index
    val = val[val.index.notna()]

    notes = []
    # a record the logger did not store is exported as zeros in every column
    empty = (val.fillna(0.0) == 0.0).all(axis=1)
    n_empty = int(empty.sum())
    val = val[~empty]
    if n_empty:
        notes.append(f"Nomad2 export: {n_empty:,} records are zeros in every column (records the logger did not "
                     "store); they are treated as missing.")
    cols, unread, never = [], [], []
    for c in val.columns:
        p = parse_header(c)
        if p is None:
            unread.append(c)
            continue
        x = val[c]
        live = (x != 0) & x.notna()
        if not live.any():
            never.append(c)
            continue
        first, last = live.idxmax(), live[::-1].idxmax()
        x = x.where((x.index >= first) & (x.index <= last))  # zeros outside the channel's own span: not set up
        p.update(source=c, series=x, first=first, last=last)
        cols.append(p)
    if never:
        ports = ", ".join(dict.fromkeys(parse_header(c)["port"] + " " + parse_header(c)["label"] for c in never))
        notes.append(f"Nomad2 export: columns that are zero throughout were dropped (channel set-ups that never "
                     f"logged): {ports}.")
    if unread:
        notes.append("Nomad2 export: columns not understood and left out: " + ", ".join(unread[:8]) + ".")

    # heights: site settings first, then the label, then another column of the same port
    port_height = {}
    for p in cols:
        if p["height"] is not None:
            port_height.setdefault((p["port"], p["prefix"]), p["height"])
    assumed = []
    for p in cols:
        ov = channels.get(p["port"].upper(), {})
        if "height_m" in ov:
            p["height"] = float(ov["height_m"])
        elif p["height"] is None:
            p["height"] = port_height.get((p["port"], p["prefix"]))
        if p["height"] is None and p["prefix"] is not None:
            p["height"] = 0.0
            assumed.append(f"{p['port']} {p['label']}")
    if assumed:
        notes.append("Nomad2 export: no height in the label or the site settings for "
                     + ", ".join(dict.fromkeys(assumed)) + "; carried at 0 m. Set \"channels\": {\"<port>\": "
                     "{\"height_m\": ...}} in the site settings: vanes are matched to cups by height, and air "
                     "density at hub height needs the thermometer and barometer heights.")

    # vane north offsets from the site settings
    turned = []
    for p in cols:
        off = channels.get(p["port"].upper(), {}).get("direction_offset_deg")
        if off and p["prefix"] == "D" and p["stat"] == "Avg":
            p["series"] = (p["series"] + float(off)) % 360.0
            turned.append(f"{p['port']} {p['label']} {float(off):+g} deg")
    if turned:
        notes.append("Nomad2 export: vane north offsets from the site settings added to the logged directions: "
                     + ", ".join(dict.fromkeys(turned)) + ".")

    # sensor numbers: one per port and height within a kind, in the order of the export
    seq, sid_of, mapping = {}, {}, []
    data, units = {}, {}
    for p in cols:
        if p["prefix"] is None:
            base = "U_Batt" if p["port"].upper() == "BAT1" else re.sub(r"\W+", "", p["port"])
            name = f"{base}_{p['stat']}"
        else:
            key = (p["prefix"], p["port"], p["height"])
            if key not in sid_of:
                seq[p["prefix"]] = seq.get(p["prefix"], 0) + 1
                sid_of[key] = f"{p['prefix']}{seq[p['prefix']]}"
            name = f"{sid_of[key]}_{p['height']:g}m_{p['stat']}"
        x = p["series"]
        data[name] = data[name].combine_first(x) if name in data else x
        units.setdefault(name, PREFIX_UNITS.get(p["prefix"] or "", p["unit"] or "V"))
        mapping.append({"column": name, "nomad2_column": p["source"], "port": p["port"], "label": p["label"],
                        "boom_tag": p["boom"], "statistic": p["stat_text"], "first": p["first"], "last": p["last"]})
    if not data:
        raise ValueError("Nomad2 export holds no data columns")
    df = pd.DataFrame(data, index=val.index)
    df.index.name = "Timestamp"
    booms = {m["column"].split("_")[0]: m["boom_tag"] for m in mapping if m["boom_tag"]}
    if booms:
        notes.append("Nomad2 export: boom tags from the labels: "
                     + ", ".join(f"{k} = {v}" for k, v in booms.items()) + ". No boom bearings are logged.")
    minutes = sorted({p["minutes"] for p in cols})
    info = {"station": " ".join(x for x in (logger, site) if x) or Path(path).stem, "model": "SecondWind Nomad2",
            "serial": logger, "os": "", "program": "Multi-Track export", "signature": "",
            "table": f"{minutes[0]} min" if minutes else "", "names": ["TIMESTAMP"] + list(df.columns), "units": units,
            "bad_lines": int(max(bad, 0)), "format": FORMAT, "reader": READER, "notes": notes, "column_map": mapping,
            "empty_records": n_empty}
    return info, df
