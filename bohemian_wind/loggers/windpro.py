"""WindPRO "time series export" reader.

Reads the text file WindPRO (EMD) writes when a meteo object's time series is exported, and returns it in the
shape loggers/toa5_mast.py works with, so the same checks, statistics and outputs run on it. This is data that
has already been through someone's hands: WindPRO exports what its user loaded and kept, not a logger's file.

What the export looks like, and what this reader does about it
- Line 1: "WindPRO time series export version <n>". Then the name of the height ("50,0m - ...") and the height
  itself ("50,0"); a line of signal identifiers; a line of column names starting "Disabled;Time stamp;...";
  a line with the time format and the units (";dd.MM.yyyy hh:mm;m/s;Degrees;").
- One line per record, cells separated by semicolons, decimal commas: an empty or filled "disabled" cell, the
  time stamp, then one value per column name. Records marked disabled are left out and counted.
- Files that were edited on the way are read too: the leading "disabled" cell may be gone and the date and the
  time may sit in separate cells, with or without an empty cell between them.
- Columns: mean wind speed, wind direction and turbulence intensity are the usual three. The standard deviation
  is rebuilt as turbulence intensity x mean wind speed. Standard deviation, maximum, minimum, temperature,
  pressure and humidity columns are read when they are there; anything else is left out and named.
- WindPRO writes a missing value as a very large negative number (about -1e37): those cells become blanks.
- One height per file. The vane is carried at the same height as the cup unless the sensor settings say
  otherwise; a north offset for the vane goes there as well:
      "channels": {"D1": {"height_m": 38, "direction_offset_deg": 12}}
- The export carries no position, elevation, sensor types, serial numbers or logger events: position and
  elevation come from the project, and site visits cannot be detected from the file.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

READER = "windpro.py 0.1.0-dev"
FORMAT = "WindPRO time series export"
DATE = re.compile(r"^\d{1,4}[./-]\d{1,2}[./-]\d{1,4}")
TIME = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")
MISSING_BELOW = -1.0e30
DISABLED = ("1", "-1", "true", "x", "yes", "disabled")
TOKENS = (("yyyy", "%Y"), ("yy", "%y"), ("MM", "%m"), ("dd", "%d"), ("HH", "%H"), ("hh", "%H"), ("mm", "%M"), ("ss", "%S"))
UNITS = {"V": "m/s", "D": "Deg", "TA": "degC", "PA": "hPa", "RH": "%rH"}


def _decode(path) -> str:
    raw = Path(path).read_bytes()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def looks_like_windpro(path) -> bool:
    try:
        with open(path, "rb") as fh:
            head = fh.read(512).decode("latin-1", errors="replace")
    except OSError:
        return False
    return head.lstrip("﻿ \t\r\n").lower().startswith("windpro time series export")


def _role(name: str) -> str | None:
    n = name.lower()
    if "turb" in n:
        return "ti"
    if "direction" in n:
        return "wd"
    if "std" in n or "standard dev" in n or "sigma" in n:
        return "sd"
    if "max" in n or "gust" in n:
        return "max"
    if "min" in n:
        return "min"
    if "temp" in n:
        return "ta"
    if "press" in n:
        return "pa"
    if "humid" in n:
        return "rh"
    if "speed" in n:
        return "ws"
    return None


def _strftime(pattern: str) -> str | None:
    """'dd.MM.yyyy hh:mm' to '%d.%m.%Y %H:%M'."""
    out, rest = "", pattern.strip()
    if not rest:
        return None
    while rest:
        for token, code in TOKENS:
            if rest.startswith(token):
                out, rest = out + code, rest[len(token):]
                break
        else:
            if rest[0].isalpha():
                return None
            out, rest = out + rest[0], rest[1:]
    return out


def _number(text: str) -> float:
    try:
        return float(text.replace(",", ".")) if text else float("nan")
    except ValueError:
        return float("nan")


def read_windpro(path, channels: dict | None = None):
    """Returns (info, data) as toa5_mast.read_toa5 does: data has columns such as V1_50m_Avg, D1_50m_Avg."""
    channels = {str(k).upper(): v for k, v in (channels or {}).items()}
    lines = [ln.strip() for ln in _decode(path).lstrip("﻿").splitlines()]
    lines = [ln for ln in lines if ln]
    if not lines or not lines[0].lower().startswith("windpro time series export"):
        raise ValueError("not a WindPRO time series export (first line)")
    first = next((i for i, ln in enumerate(lines[1:60], start=1) if DATE.match(ln.lstrip(";").split(";")[0].strip())
                  or any(DATE.match(c.strip()) for c in ln.split(";")[:2])), None)
    if first is None:
        raise ValueError("no data rows found below the header")
    head = lines[1:first]
    plain = [ln for ln in head if ";" not in ln]
    split = [[c.strip() for c in ln.split(";")] for ln in head if ";" in ln]
    names_row = next((r for r in split if len(r) > 2 and r[1].lower() == "time stamp" and r[0].lower() == "disabled"), None)
    if names_row is None:
        names_row = next((r for r in split if len(r) > 2 and r[1].lower() == "time stamp" and any(_role(c) for c in r[2:])), None)
    if names_row is None:
        raise ValueError("the line of column names (Disabled;Time stamp;...) is missing")
    names = names_row[2:]
    while names and not names[-1]:
        names.pop()
    fmt_row = next((r for r in split if len(r) > 1 and "yy" in r[1]), None)
    stamp_format = _strftime(fmt_row[1]) if fmt_row else None
    label = plain[0].strip(" -") if plain else ""
    height = next((v for v in (_number(x) for x in plain[1:2]) if np.isfinite(v)), None)
    if height is None and label:
        m = re.search(r"(\d+(?:[.,]\d+)?)\s*m\b", label)
        height = float(m.group(1).replace(",", ".")) if m else None
    notes = []

    # ---- records
    stamps, values, disabled, bad, edited = [], [], 0, 0, False
    for ln in lines[first:]:
        c = [x.strip() for x in ln.split(";")]
        k = next((i for i, x in enumerate(c[:3]) if DATE.match(x)), None)
        if k is None:
            bad += 1
            continue
        flag = any(x.lower() in DISABLED for x in c[:k])
        stamp, j = c[k], k + 1
        if " " not in stamp:                              # the date alone: the time follows, perhaps after a blank
            while j < len(c) and not c[j]:
                j += 1
            if j >= len(c) or not TIME.match(c[j]):
                bad += 1
                continue
            stamp, j, edited = f"{stamp} {c[j]}", j + 1, True
        if k == 0:
            edited = True
        if flag:
            disabled += 1
            continue
        vals = c[j:j + len(names)]
        stamps.append(" ".join(stamp.split()))
        values.append([_number(x) for x in vals] + [float("nan")] * (len(names) - len(vals)))
    if not values:
        raise ValueError("no data rows could be read")
    t = pd.Series(stamps)
    ts = pd.to_datetime(t, format=stamp_format, errors="coerce") if stamp_format else pd.to_datetime(t, errors="coerce", dayfirst=True)
    if ts.isna().mean() > 0.5:
        ts = pd.to_datetime(t, errors="coerce", dayfirst=True)
    val = pd.DataFrame(values, columns=range(len(names)), index=pd.DatetimeIndex(ts, name="Timestamp"))
    bad += int(val.index.isna().sum())
    val = val[val.index.notna()]
    val = val[~val.index.duplicated(keep="first")].sort_index()
    missing = int((val < MISSING_BELOW).sum().sum())
    val = val.where(val >= MISSING_BELOW)
    if missing:
        notes.append(f"WindPRO export: {missing:,} values at WindPRO's missing-data code were read as blanks.")
    if disabled:
        notes.append(f"WindPRO export: {disabled:,} records marked disabled in WindPRO were left out.")
    if edited:
        notes.append("WindPRO export: the file was edited after it was exported (the date and the time in separate cells, "
                     "or no 'disabled' cell); the records were read as they stand.")

    roles, unread = {}, []
    for i, name in enumerate(names):
        r = _role(name)
        if r is None or r in roles:
            unread.append(name or f"column {i + 3}")
        else:
            roles[r] = i
    if "ws" not in roles:
        raise ValueError("the export holds no mean wind speed column")
    if height is None:
        height = _number(str((channels.get("V1") or {}).get("height_m", "")))
    if height is None or not np.isfinite(height):
        raise ValueError("the export does not say at what height the wind was measured")
    h_cup = float((channels.get("V1") or {}).get("height_m", height))
    h_vane = float((channels.get("D1") or {}).get("height_m", h_cup))
    data, units = {}, {}
    ws = val[roles["ws"]]
    data[f"V1_{h_cup:g}m_Avg"] = ws
    if "sd" in roles:
        data[f"V1_{h_cup:g}m_Std"] = val[roles["sd"]]
    elif "ti" in roles:
        ti = val[roles["ti"]]
        if ti.quantile(0.9) > 1.5:                         # given in per cent
            ti = ti / 100.0
        data[f"V1_{h_cup:g}m_Std"] = ti * ws
        notes.append("WindPRO export: the standard deviation of the wind speed was rebuilt as turbulence intensity x "
                     "mean wind speed.")
    for role, stat in (("max", "Max"), ("min", "Min")):
        if role in roles:
            data[f"V1_{h_cup:g}m_{stat}"] = val[roles[role]]
    if "wd" in roles:
        wd = val[roles["wd"]]
        off = (channels.get("D1") or {}).get("direction_offset_deg")
        if off:
            wd = (wd + float(off)) % 360.0
            notes.append(f"WindPRO export: vane north offset from the sensor settings added to the directions: {float(off):+g} deg.")
        data[f"D1_{h_vane:g}m_Avg"] = wd
    for role, prefix in (("ta", "TA"), ("pa", "PA"), ("rh", "RH")):
        if role in roles:
            hh = float((channels.get(f"{prefix}1") or {}).get("height_m", h_cup))
            data[f"{prefix}1_{hh:g}m_Avg"] = val[roles[role]]
    for col in data:
        units[col] = UNITS[re.match(r"[A-Z]+", col).group(0)]
    if unread:
        notes.append("WindPRO export: columns not understood and left out: " + ", ".join(unread[:8]) + ".")
    lacking = [what for what, has in (("maximum or minimum wind speed", "max" in roles or "min" in roles),
                                      ("temperature or pressure", "ta" in roles or "pa" in roles)) if not has]
    if lacking:
        notes.append("WindPRO export: the file carries no " + " and no ".join(lacking) + ": "
                     + " and ".join(x for x, has in (("gusts cannot be checked", "max" in roles or "min" in roles),
                                                     ("air density is not measured", "ta" in roles or "pa" in roles))
                                    if not has) + ".")
    df = pd.DataFrame(data, index=val.index)
    df.index.name = "Timestamp"
    step = df.index.to_series().diff().dropna()
    minutes = step.mode().iloc[0].total_seconds() / 60.0 if len(step) else 10.0
    version = lines[0].split("version")[-1].strip() if "version" in lines[0].lower() else ""
    info = {"station": f"{Path(path).stem} ({label})" if label else Path(path).stem,
            "model": "WindPRO time series export" + (f" (version {version})" if version else ""), "serial": "", "os": "",
            "program": "WindPRO export", "signature": "", "table": f"{minutes:g} min",
            "names": ["TIMESTAMP"] + list(df.columns), "units": units, "bad_lines": int(bad), "format": FORMAT,
            "reader": READER, "notes": notes,
            "column_map": [{"windpro_column": names[i], "read_as": r} for r, i in sorted(roles.items(), key=lambda kv: kv[1])],
            "disabled_records": disabled, "missing_values": missing}
    return info, df
