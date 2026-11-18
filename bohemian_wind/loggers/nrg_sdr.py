"""NRG Symphonie text exports (Symphonie Data Retriever, "SDR") reader.

Reads the .txt files that NRG's Symphonie Data Retriever writes from the raw .RWD files of a Symphonie,
SymphoniePLUS or SymphoniePLUS3 logger, and returns them in the shape loggers/toa5_mast.py works with, so the same
checks, statistics and outputs run on them. The raw .RWD files themselves are not read: convert them with NRG's
free Symphonie Data Retriever (Windows) first.

What the export looks like, and what this reader does about it
- A header of key and value lines: "SDR <version>", logger (Model #, Serial #, Hardware Rev.), site (Site #,
  Site Desc, Site Elevation, Latitude, Longitude, Time offset (hrs)) and, for every channel, a block:
  Channel #, Type, Description, Details, Serial Number, Height, Scale Factor, Offset, Units.
- One line of column names: Date & Time Stamp, CH1Avg, CH1SD, CH1Max, CH1Min, CH2Avg, ...
- One line per record. Speeds are rounded to 0.1 in the export.
- Files that went through a spreadsheet on the way are read too. Such files show: every word of the header in a
  cell of its own, decimal commas, the date and the time in two cells, and the empty columns of a channel with no
  signal-conditioning module closed up, so that the later channels sit four cells to the left. The reader tells
  the two layouts apart row by row and says which one it found.
- Channels: a cup is V<channel> (V1 = channel 1), a vane D<channel>, thermometer TA, barometer PA, hygrometer RH;
  the logger's voltage channel is kept for checks only. Vanes log no maximum or minimum.
- A voltage channel left at a scale factor of 1 with no offset holds the logger's raw counts, not volts. It is
  left out of the supply check, and named, when its readings are higher than any supply voltage.
- Heights come from the header. Where the header is wrong or silent, the site settings say so by channel, and a
  vane's north offset goes there as well (added to every record, modulo 360):
      "channels": {"CH2": {"height_m": 50}, "CH8": {"direction_offset_deg": 15}}
  A channel with no height whose reading never changes has nothing connected and is left out, and named.
  A channel with a height is kept even when it is dead: the checks then flag it for what it is.
- A header whose channel settings differ from the file before is a different logger configuration: the run
  lists it and treats the change as a site visit.
- Speeds in mph, km/h or knots, heights in feet, temperatures in F and pressures in kPa or inHg are converted.
- Position, elevation and the clock's offset from UTC are read from the header.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

READER = "nrg_sdr.py 0.1.0-dev"
FORMAT = "NRG Symphonie text export (Symphonie Data Retriever)"
COLUMN = re.compile(r"^CH(\d+)(Avg|SD|Max|Min)$", re.I)
TIME_ONLY = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?(\s*[AaPp][Mm])?$")
NUMBER = re.compile(r"-?\d+(?:[.,]\d+)?")
STAT = {"avg": "Avg", "sd": "Std", "max": "Max", "min": "Min"}
STAMP_FORMATS = ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M",
                 "%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %I:%M %p", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M",
                 "%d/%m/%Y %I:%M:%S %p", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M")
SPEED_TO_MS = {"m/s": 1.0, "mph": 0.44704, "km/h": 1.0 / 3.6, "kph": 1.0 / 3.6, "knots": 0.514444, "kts": 0.514444}
PRESSURE_TO_HPA = {"mb": 1.0, "mbar": 1.0, "hpa": 1.0, "kpa": 10.0, "inhg": 33.8639, "in hg": 33.8639}
UNITS = {"V": "m/s", "D": "Deg", "TA": "degC", "PA": "hPa", "RH": "%rH"}
HEADER_KEYS = ("Type", "Description", "Details", "Serial Number", "Height", "Scale Factor", "Offset", "Units")
SETTINGS = ("Type", "Description", "Serial Number", "Height", "Scale Factor", "Offset", "Units")   # what makes a configuration
ZEROS = ("", "0", "0.0", "0,0", "0.00", "0,00")
COUNTS_ABOVE = 60.0   # a voltage channel scaled by 1 with no offset that reads more than this holds the logger's raw
#                       counts, not volts: no logger supply runs that high


def _decode(path) -> str:
    raw = Path(path).read_bytes()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _cells(line: str) -> list:
    return [c.strip() for c in line.split("\t")]


def _joined(line: str) -> str:
    return " ".join(" ".join(c for c in _cells(line) if c).split())


def looks_like_sdr(path) -> bool:
    """A Symphonie Data Retriever text export: starts with 'SDR', has channel blocks and CH<n>Avg columns."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(2048).decode("latin-1", errors="replace")
            first = next((_joined(ln) for ln in head.splitlines() if ln.strip()), "")
            if not first.upper().startswith("SDR"):          # most files are settled by their first line
                return False
            head += fh.read(256 * 1024).decode("latin-1", errors="replace")
    except OSError:
        return False
    return "Channel" in head and re.search(r"\bCH1Avg\b", head) is not None


def _num(text) -> float | None:
    m = NUMBER.search(str(text or ""))
    return float(m.group(0).replace(",", ".")) if m else None


def _angle(text: str) -> float | None:
    """'N 012° 30.600'' (degrees and decimal minutes, the degree sign often lost on the way) to signed degrees."""
    m = re.search(r"([NSEW])\s*(\d{1,3})\D{0,3}?\s*(\d{1,2}(?:[.,]\d+)?)", str(text or ""), re.I)
    if not m:
        return None
    value = float(m.group(2)) + float(m.group(3).replace(",", ".")) / 60.0
    return -value if m.group(1).upper() in ("S", "W") else value


def parse_header(lines: list) -> dict:
    """Logger, site and channel settings from the lines above the column names."""
    out = {"logger": {}, "site": {}, "channels": {}}
    cur = None
    for raw in lines:
        s = _joined(raw)
        if not s:
            continue
        m = re.match(r"^Channel # ?(\d+)$", s, re.I)
        if m:
            cur = int(m.group(1))
            out["channels"][cur] = {k: "" for k in HEADER_KEYS}
            continue
        if cur is not None:
            for key in HEADER_KEYS:
                if s.lower() == key.lower() or s.lower().startswith(key.lower() + " "):
                    out["channels"][cur][key] = s[len(key):].strip()
                    break
            continue
        for key, group, name in (("SDR", "logger", "sdr"), ("Model #", "logger", "model"), ("Serial #", "logger", "serial"),
                                 ("Hardware Rev.", "logger", "hardware"), ("Site #", "site", "number"),
                                 ("Site Desc", "site", "desc"), ("Project Code", "site", "project_code"),
                                 ("Project Desc", "site", "project_desc"), ("Site Location", "site", "location"),
                                 ("Site Elevation", "site", "elevation"), ("Latitude", "site", "latitude"),
                                 ("Longitude", "site", "longitude"), ("Time offset (hrs)", "site", "utc_offset")):
            if s.lower() == key.lower() or s.lower().startswith(key.lower() + " "):
                out[group][name] = s[len(key):].strip()
                break
    return out


def _kind(ch: dict) -> str | None:
    """V, D, TA, PA, RH; 'U' = voltage (checks only); 'X' = something else; None = no module in the slot."""
    desc, units = ch["Description"].lower(), ch["Units"].lower().replace("°", "").strip()
    if ch["Type"].strip() == "0" or "no scm" in desc:
        return None
    if units in ("deg", "degrees") or "vane" in desc:
        return "D"
    if units in SPEED_TO_MS or "anem" in desc:
        return "V"
    if units in ("c", "f", "degc", "degf") or "temp" in desc:
        return "TA"
    if units in PRESSURE_TO_HPA or "barom" in desc or "bp-" in desc or "press" in desc:
        return "PA"
    if "%" in units or units == "rh" or "humid" in desc:
        return "RH"
    if units in ("volt", "volts", "v") or "volt" in desc:
        return "U"
    return "X"


def _height(text: str) -> float | None:
    v = _num(text)
    if v is None or v <= 0:
        return None
    return v * 0.3048 if re.search(r"\b(ft|feet)\b", text, re.I) else v


def _timestamps(stamps: pd.Series) -> pd.DatetimeIndex:
    """The date order is not written in the file: of the formats that read the stamps, the one that gives the
    most evenly spaced records wins (day-first and month-first disagree from the 13th of a month onwards)."""
    t = stamps.str.replace(r"\s+", " ", regex=True).str.strip()
    best, score = None, (-1, -1)
    for fmt in STAMP_FORMATS:
        ts = pd.to_datetime(t, format=fmt, errors="coerce")
        ok = int(ts.notna().sum())
        if not ok:
            continue
        step = ts.dropna().diff().dropna()
        even = int((step == step.mode().iloc[0]).sum()) if len(step) else 0
        if (ok, even) > score:
            best, score = ts, (ok, even)
    if best is None:
        best = pd.to_datetime(t, errors="coerce")
    return pd.DatetimeIndex(best, name="Timestamp")


def read_sdr(path, channels: dict | None = None):
    """Returns (info, data) as toa5_mast.read_toa5 does: data has columns such as V1_60m_Avg, D7_58m_Std."""
    channels = {str(k).upper(): v for k, v in (channels or {}).items()}
    lines = _decode(path).splitlines()
    at = next((i for i, ln in enumerate(lines[:2000]) if sum(1 for c in _cells(ln) if COLUMN.match(c)) >= 4), None)
    if at is None or not (lines and _joined(lines[0]).upper().startswith("SDR")):
        raise ValueError("not a Symphonie Data Retriever text export (no 'SDR' first line or no CH<n>Avg columns)")
    head = parse_header(lines[:at])
    names = [(int(m.group(1)), STAT[m.group(2).lower()]) for c in _cells(lines[at]) for m in [COLUMN.match(c)] if m]
    if not head["channels"]:
        raise ValueError("the header holds no channel settings")
    kinds = {n: _kind(c) for n, c in head["channels"].items()}
    dead = [i for i, (n, _s) in enumerate(names) if kinds.get(n) is None]
    live = [i for i in range(len(names)) if i not in dead]
    notes = []

    # ---- records: the stamp in one cell or two, the values in place or closed up over empty channels
    stamps, rows, layouts, bad = [], [], Counter(), 0
    split_stamp = False
    tail_live = any(i >= len(live) for i in live)      # a channel in use sits where a closed-up row ends in blanks
    for ln in lines[at + 1:]:
        if not ln.strip():
            continue
        c = _cells(ln)
        if len(c) > 1 and TIME_ONLY.match(c[1]) and " " not in c[0]:
            stamp, vals, split_stamp = f"{c[0]} {c[1]}", c[2:], True
        else:
            stamp, vals = c[0], c[1:]
        if any(x for x in vals[len(names):]):
            bad += 1
            continue
        vals = (vals + [""] * len(names))[:len(names)]
        in_place = all(vals[i] in ZEROS for i in dead) and sum(1 for i in live if vals[i]) * 2 >= len(live)
        closed = bool(dead) and all(vals[i] for i in range(len(live))) and not any(vals[len(live):])
        # a logger writes every channel in use, so blanks under one, with every earlier cell filled, mean closed up
        kind = "closed up" if closed and tail_live else "in place" if in_place else "closed up" if closed else None
        if kind is None:
            bad += 1
            continue
        layouts[kind] += 1
        stamps.append(stamp)
        rows.append((kind, vals))
    if not rows:
        raise ValueError("no data rows could be matched to the column names")
    layout = "in place" if layouts["in place"] >= layouts["closed up"] else "closed up"
    keep = [k == layout for k, _v in rows]
    bad += len(rows) - sum(keep)
    use = live if layout == "closed up" else list(range(len(names)))          # the columns the kept cells belong to
    cells = range(len(live)) if layout == "closed up" else use
    table = [[v[j] for j in cells] for (_k, v), ok in zip(rows, keep) if ok]
    stamps = [s for s, ok in zip(stamps, keep) if ok]
    comma = any("," in x for r in table[:200] for x in r)
    raw = pd.DataFrame(table, columns=pd.MultiIndex.from_tuples([names[i] for i in use]))
    val = raw.apply(lambda col: pd.to_numeric(col.str.replace(",", ".", regex=False), errors="coerce"))
    val.index = _timestamps(pd.Series(stamps))
    val = val[val.index.notna()]
    val = val[~val.index.duplicated(keep="first")].sort_index()
    if val.empty:
        raise ValueError("no record carries a readable date and time")
    if comma or split_stamp:
        notes.append("NRG Symphonie text export: the files were saved again from a spreadsheet (decimal commas, the date "
                     "and the time in separate cells); the numbers and stamps were read as they stand.")
    if layout == "closed up":
        gone = ", ".join(f"CH{n}" for n in sorted({names[i][0] for i in dead}))
        notes.append(f"NRG Symphonie text export: the data rows carry no cells for {gone} (no signal-conditioning module), "
                     "so the later channels sit to the left of their column names; they were matched to their channels "
                     "in order.")

    # ---- channels to sensors
    data, units, mapping = {}, {}, []
    unused, other, no_height, converted, turned, counts = [], [], [], [], [], []
    for n in sorted(head["channels"]):
        ch, kind = head["channels"][n], kinds[n]
        port = f"CH{n}"
        if kind is None or (n, "Avg") not in val.columns:
            continue
        label = f"{port} {ch['Description']}".strip()
        avg = val[(n, "Avg")]
        if kind == "X":
            other.append(label)
            continue
        ov = channels.get(port, {})
        h = float(ov["height_m"]) if "height_m" in ov else _height(ch["Height"])
        u = ch["Units"].lower().replace("°", "").strip()
        if kind == "U":
            typical = float(avg.median()) if avg.notna().any() else 0.0
            if _num(ch["Scale Factor"]) == 1.0 and not _num(ch["Offset"]) and typical > COUNTS_ABOVE:
                counts.append(label)
                continue
            for stat in ("Avg", "Std", "Max", "Min"):
                if (n, stat) in val.columns and f"U_Batt_{stat}" not in data:
                    data[f"U_Batt_{stat}"] = val[(n, stat)]
                    units[f"U_Batt_{stat}"] = "V"
            mapping.append({"column": "U_Batt", "channel": n, "description": ch["Description"], "height_m": h,
                            "scale": _num(ch["Scale Factor"]), "offset": _num(ch["Offset"]), "serial": ch["Serial Number"]})
            continue
        if h is None:
            if avg.dropna().nunique() <= 1:
                unused.append(label)
                continue
            h = 0.0
            no_height.append(label)
        factor, shift = 1.0, 0.0
        if kind == "V" and u in SPEED_TO_MS and u != "m/s":
            factor = SPEED_TO_MS[u]
        elif kind == "TA" and u in ("f", "degf"):
            factor, shift = 5.0 / 9.0, -32.0 * 5.0 / 9.0
        elif kind == "PA" and u in PRESSURE_TO_HPA and PRESSURE_TO_HPA[u] != 1.0:
            factor = PRESSURE_TO_HPA[u]
        if factor != 1.0 or shift != 0.0:
            converted.append(f"{label} from {ch['Units']}")
        if re.search(r"\b(ft|feet)\b", ch["Height"], re.I) and "height_m" not in ov:
            converted.append(f"{label} height from feet")
        sid = f"{kind}{n}"
        for stat in ("Avg", "Std", "Max", "Min"):
            if (n, stat) not in val.columns:
                continue
            x = val[(n, stat)]
            if kind == "D":
                if stat in ("Max", "Min"):          # the logger stores no extremes for a vane: the cells hold zeros
                    continue
                off = ov.get("direction_offset_deg")
                if off and stat == "Avg":
                    x = (x + float(off)) % 360.0
                    turned.append(f"{label} {float(off):+g} deg")
            elif stat == "Std":
                x = x * abs(factor)
            else:
                x = x * factor + shift
            col = f"{sid}_{h:g}m_{stat}"
            data[col] = x
            units[col] = UNITS[kind]
        mapping.append({"column": f"{sid}_{h:g}m", "channel": n, "description": ch["Description"], "height_m": h,
                        "scale": _num(ch["Scale Factor"]), "offset": _num(ch["Offset"]), "serial": ch["Serial Number"]})
    if not any(c.endswith("_Avg") and not c.startswith("U_Batt") for c in data):
        raise ValueError("the export holds no channel this reader knows (cup, vane, thermometer, barometer, hygrometer)")
    if unused:
        notes.append("NRG Symphonie text export: channels with no height in the header and a reading that never changes "
                     "were left out (nothing connected): " + ", ".join(unused) + ".")
    if no_height:
        notes.append("NRG Symphonie text export: no height in the header for " + ", ".join(no_height) + "; carried at "
                     "0 m. Give the height by channel in the sensor settings (for example CH2: 50 m).")
    if counts:
        notes.append("NRG Symphonie text export: a voltage channel set to a scale factor of 1 with no offset holds the "
                     "logger's raw counts, not volts, and was left out of the supply check: " + ", ".join(counts)
                     + ". Set its scale factor in the logger software and export again to check the supply.")
    if other:
        notes.append("NRG Symphonie text export: channels of a kind that is not checked were left out: "
                     + ", ".join(other) + ".")
    if converted:
        notes.append("NRG Symphonie text export: converted to metric units: " + ", ".join(dict.fromkeys(converted)) + ".")
    if turned:
        notes.append("NRG Symphonie text export: vane north offsets from the sensor settings added to the logged "
                     "directions: " + ", ".join(dict.fromkeys(turned)) + ".")
    set_heights = [f"CH{m['channel']} {m['height_m']:g} m" for m in mapping
                   if "height_m" in channels.get(f"CH{m['channel']}", {})]
    if set_heights:
        notes.append("NRG Symphonie text export: heights from the sensor settings used in place of the header's: "
                     + ", ".join(set_heights) + ".")

    df = pd.DataFrame(data, index=val.index)
    df.index.name = "Timestamp"
    step = df.index.to_series().diff().dropna()
    minutes = step.mode().iloc[0].total_seconds() / 60.0 if len(step) else 10.0
    # the logger configuration: what was connected where, and how it was scaled
    cfg = "|".join(f"{n}:" + ":".join(c[k] for k in SETTINGS) for n, c in sorted(head["channels"].items()))
    signature = hashlib.sha1(cfg.encode("utf-8")).hexdigest()[:6]      # noqa: S324  (a label, not a secret)
    lat, lon = _angle(head["site"].get("latitude")), _angle(head["site"].get("longitude"))
    if lat is not None and lon is not None and abs(lat) < 1e-9 and abs(lon) < 1e-9:
        lat = lon = None                                             # 0, 0 is the logger's blank position
    elevation = _num(head["site"].get("elevation"))
    offset = _num(head["site"].get("utc_offset"))
    site_no, desc = head["site"].get("number", ""), head["site"].get("desc", "")
    info = {"station": " ".join(x for x in (site_no, desc) if x) or Path(path).stem,
            "model": f"NRG Symphonie (model {head['logger'].get('model', '?')})", "serial": head["logger"].get("serial", ""),
            "os": head["logger"].get("hardware", ""), "program": f"configuration {signature}", "signature": signature,
            "table": f"{minutes:g} min", "names": ["TIMESTAMP"] + list(df.columns), "units": units,
            "bad_lines": int(bad), "format": FORMAT, "reader": READER, "notes": notes, "column_map": mapping,
            "layout": layout, "settings": {f"CH{n}": {k: c[k] for k in SETTINGS} for n, c in sorted(head["channels"].items())},
            "site": {"lat": lat, "lon": lon, "elevation_m": elevation if elevation else None,
                     "utc_offset_h": offset, "sdr_version": head["logger"].get("sdr", "")}}
    return info, df
