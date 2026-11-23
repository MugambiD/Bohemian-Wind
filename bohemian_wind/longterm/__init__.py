"""Step 2: long-term correction. Scales a short measurement campaign to the long-term wind climate with a monthly
index against reanalysis data (NASA POWER: MERRA-2 wind speed at 50 m).

Method (one method, on purpose; more will follow):
- All 12 calendar months measured: compare the mean of monthly means of the mast with the same months of the
  reference, then with the reference's long-term mean of monthly means.
- Fewer calendar months measured: ratio of the long-term annual reference to the reference over the months
  measured. This assumes the mast-to-reference ratio holds in the months not measured, and says so.
- The last reference months are checked before use: NASA POWER appends another model (GEOS-IT) to the end of the
  MERRA-2 series for its latest weeks, and such a month can break the mast-to-reference relation.
- Air for masts with no thermometer or barometer: the download also brings the reanalysis temperature at 2 m and
  surface pressure. Their long-term means, with the elevation of the reanalysis cell, go to the energy step, which
  carries them to the site for the air density. On masts with a measured density this estimate came within 2 % of
  it; the standard atmosphere, the fallback before, was 3 to 7 % too dense at warm sites.
- Vane north check: the mast's daily wind direction is set against the reanalysis direction at 50 m on days when
  both read 5 m/s or more. Terrain turns the wind, so some tens of degrees are normal; a large, steady difference
  points to a vane whose north offset is not what the settings say.

[COMPLIANCE] A screening estimate. One reference and one method do not make a bankable long-term correction.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from .. import climate as _climate

POWER_API = "https://power.larc.nasa.gov/api/temporal/{step}/point"
POWER_CREDIT = "Long-term index: NASA Langley Research Center POWER Project (MERRA-2 wind speed at 50 m)."
REF_JUMP = 0.25      # a trailing reference month whose mast-to-reference ratio is this far off is left out
LT_YEARS = 20
TAIL_MONTHS = 4        # how far back from the reference's last month its tail can be of a different make
POWER_MONTHLY = "WS50M,T2M,PS"   # wind speed for the index; air temperature and surface pressure for the air density of
#                                  masts that measure neither (asked for with the same download)
VANE_MIN_WS, VANE_MIN_DAYS = 5.0, 20   # the vane check: days with this wind on both sides, and how many it takes
VANE_LIMIT_DEG = 45.0  # mast against reanalysis direction: beyond this, the vane's north is worth a look
VANE_SPREAD_DEG = 90.0  # the middle half of the days spread wider than this: the comparison says nothing
PARTIAL_SHARE = 0.40   # a month with this share of valid data may stand in for a calendar month that is otherwise missing


class ReferenceError_(RuntimeError):
    """The reference data could not be fetched or read."""


def power_url(lat: float, lon: float, step: str, start: str, end: str, parameters: str = "WS50M") -> str:
    return (f"{POWER_API.format(step=step)}?parameters={parameters}&community=RE&longitude={lon:.4f}&latitude={lat:.4f}"
            f"&start={start}&end={end}&format=JSON")


def fetch_power(lat: float, lon: float, dest_dir, start_year: int = 2001, today: date | None = None,
                timeout: float = 60.0, period: tuple | None = None) -> dict:
    """Downloads the monthly series for complete years and the daily series for the current year, into dest_dir.
    With period (first day, last day of the measurements, "YYYY-MM-DD") the daily wind speed and direction over the
    measurements come too, for the vane north check. Returns the file paths. Needs an internet connection."""
    today = today or date.today()
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    out = {}
    jobs = [("monthly", "monthly", str(start_year), str(today.year - 1), dest / "power_monthly.json", POWER_MONTHLY),
            ("daily", "daily", f"{today.year}0101", f"{today:%Y%m%d}", dest / "power_daily.json", "WS50M")]
    if period:
        jobs.append(("measured", "daily", period[0].replace("-", ""), period[1].replace("-", ""),
                     dest / "power_daily_measured.json", "WS50M,WD50M"))
    for key, step, a, b, path, parameters in jobs:
        body, failure = None, None
        for params in dict.fromkeys((parameters, "WS50M") if key == "monthly" else (parameters,)):   # the wind speed alone,
            url = power_url(lat, lon, step, a, b, params)                                          # if the extras are refused
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "bohemian-wind"})
                with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (fixed https host)
                    got = r.read()
                json.loads(got)["properties"]["parameter"]["WS50M"]
                body = got
                break
            except (urllib.error.URLError, OSError, KeyError, ValueError) as e:
                failure = e
        if body is None:
            if key != "monthly" and "monthly" in out:
                continue        # the current year and the vane check are extras: complete years are enough
            raise ReferenceError_(f"Could not download the reference data from NASA POWER ({failure}). Check the internet "
                                  "connection, or download the file in a browser and choose 'Use a file'.") from failure
        path.write_bytes(body)
        out[key] = str(path)
    return out


def reference_air(monthly_path, first_year: int | None = None, last_year: int | None = None) -> dict | None:
    """Long-term mean air temperature at 2 m and surface pressure of the reanalysis cell, with the cell's own
    elevation, from a NASA POWER monthly file that holds them (T2M, PS). None when the file does not: the energy
    step then falls back on the standard atmosphere. Used for the air density of masts that measure neither."""
    try:
        j = json.loads(Path(monthly_path).read_text(encoding="utf-8"))
        par = j["properties"]["parameter"]
        elevation = float(j["geometry"]["coordinates"][2])
        series = {"t2m_c": par["T2M"], "ps_kpa": par["PS"]}
    except (OSError, KeyError, ValueError, TypeError, IndexError):
        return None
    out = {}
    for name, values in series.items():
        vals = [float(v) for k, v in values.items()
                if len(k) == 6 and k[4:] != "13" and float(v) > -900
                and (first_year is None or int(k[:4]) >= first_year) and (last_year is None or int(k[:4]) <= last_year)]
        if len(vals) < 12:
            return None
        out[name] = round(sum(vals) / len(vals), 3)
    if not (-60.0 < out["t2m_c"] < 60.0 and 30.0 < out["ps_kpa"] < 110.0):
        return None
    return {**out, "elevation_m": round(elevation, 1), "months": len(vals), "source": "NASA POWER, MERRA-2"}


def vane_check(clim: dict, daily_path, offset: float = 0.0) -> dict | None:
    """The mast's daily wind direction (climate["direction_days"], turned by the project's vane correction) against
    the reanalysis direction at 50 m, on days when both read VANE_MIN_WS or more. Returns the typical difference,
    mast minus reference, with the middle half of the days, and a caution when it is large. None when the climate
    or the file holds no directions, or the days are too few to say anything."""
    dd = clim.get("direction_days")
    if not dd or not daily_path or not Path(daily_path).is_file():
        return None
    try:
        p = json.loads(Path(daily_path).read_text(encoding="utf-8"))["properties"]["parameter"]
        ref = pd.DataFrame({"ref_ws": pd.Series(p["WS50M"], dtype=float), "ref_deg": pd.Series(p["WD50M"], dtype=float)})
        ref.index = pd.to_datetime(ref.index, format="%Y%m%d")
    except (KeyError, ValueError, TypeError):
        return None
    days = pd.date_range(dd["first"], periods=len(dd["deg"]), freq="D")
    mast = pd.DataFrame({"deg": pd.Series(dd["deg"], index=days, dtype=float), "ws": pd.Series(dd["ws"], index=days, dtype=float)})
    j = mast.join(ref, how="inner").dropna()
    j = j[(j["ref_ws"] >= VANE_MIN_WS) & (j["ws"] >= VANE_MIN_WS) & (j["ref_deg"] >= 0)]
    if len(j) < VANE_MIN_DAYS:
        return None
    diff = np.radians((j["deg"] + float(offset) - j["ref_deg"]).to_numpy())
    centre = float(np.degrees(np.arctan2(np.sin(diff).mean(), np.cos(diff).mean())))       # where the differences sit
    around = (np.degrees(diff) - centre + 180.0) % 360.0 - 180.0                           # no wrap at +/-180 this way
    wrap = lambda x: float((x + 180.0) % 360.0 - 180.0)  # noqa: E731
    q25, med, q75 = (wrap(centre + float(np.percentile(around, q))) for q in (25, 50, 75))
    spread = float(np.percentile(around, 75) - np.percentile(around, 25))
    h = clim["height_m"]
    res = {"days": int(len(j)), "median_deg": round(med, 1), "q25_deg": round(q25, 1), "q75_deg": round(q75, 1),
           "spread_deg": round(spread, 1), "offset_applied_deg": float(offset), "min_ws": VANE_MIN_WS, "height_m": h,
           "first_day": f"{j.index[0]:%Y-%m-%d}", "last_day": f"{j.index[-1]:%Y-%m-%d}", "caution": ""}
    if abs(med) >= VANE_LIMIT_DEG and spread <= VANE_SPREAD_DEG:
        res["caution"] = (
            f"The wind direction measured at {h:g} m runs {med:+.0f} degrees from the reanalysis (NASA POWER, MERRA-2 at "
            f"50 m) on {len(j)} days with {VANE_MIN_WS:g} m/s or more (the middle half of the days: {q25:+.0f} to "
            f"{q75:+.0f} degrees"
            + (f"; after the vane correction of {offset:+g} degrees entered in the project" if offset else "")
            + "). Terrain turns the wind, but rarely this far"
            + (", and a difference near 180 degrees is what a north mark read the wrong way round gives" if abs(med) >= 135 else "")
            + ": check the vane's north offset against the installation record before relying on the wind rose, the "
            "wind map and the layout.")
    return res


def read_power(monthly_path, daily_path=None) -> pd.Series:
    """Monthly reference series (period index). Months of the current year come from the daily file when given;
    a month is used only when every one of its days has a value."""
    v = json.loads(Path(monthly_path).read_text(encoding="utf-8"))["properties"]["parameter"]["WS50M"]
    s = pd.Series({pd.Period(f"{k[:4]}-{k[4:]}", "M"): float(x) for k, x in v.items() if k[4:] != "13"}, dtype=float)
    s = s[s > -900].sort_index()
    if daily_path and Path(daily_path).is_file():
        d = json.loads(Path(daily_path).read_text(encoding="utf-8"))["properties"]["parameter"]["WS50M"]
        dd = pd.Series({pd.Timestamp(f"{k[:4]}-{k[4:6]}-{k[6:]}"): float(x) for k, x in d.items()}, dtype=float)
        dd = dd[dd > -900]
        g = dd.groupby(dd.index.to_period("M"))
        full = g.count() == pd.Series({p: p.days_in_month for p in g.groups}, dtype=float)
        extra = g.mean()[full]
        s = pd.concat([s, extra[~extra.index.isin(s.index)]]).sort_index()
    return s


TIME_NAMES = ("valid_time", "time", "datetime", "date", "timestamp")
SPEED_NAMES = ("wind_speed_100m", "ws100", "ws100m", "wind_speed", "windspeed", "ws", "speed", "ws50m", "si100", "si10")
PAIR_NAMES = (("u100", "v100"), ("100m_u_component_of_wind", "100m_v_component_of_wind"), ("u10", "v10"),
              ("10m_u_component_of_wind", "10m_v_component_of_wind"), ("u", "v"))
SECOND_MONTH_SHARE = 0.95      # a month of the second reference counts when this share of its time steps is there


LAT_NAMES = ("latitude", "lat", "y")
LON_NAMES = ("longitude", "lon", "long", "lng", "x")
MAX_REFERENCE_SPEED = 75.0     # m/s: above any hourly mean wind a reanalysis gives; a larger value is a unit or column error


def one_location(df: pd.DataFrame, cols: dict, name: str) -> str:
    """A reference is the wind at one point. A table with a latitude and a longitude column must hold one pair of
    them: with two, which point's wind is read would depend on the order of the rows. Returns the point as text
    ("" when the table has no such columns)."""
    la = next((cols[n] for n in LAT_NAMES if n in cols), None)
    lo = next((cols[n] for n in LON_NAMES if n in cols), None)
    have = {k: pd.to_numeric(df[c], errors="coerce").round(4) for k, c in (("lat", la), ("lon", lo)) if c is not None}
    if not have:
        return ""
    pts = pd.DataFrame(have).dropna().drop_duplicates()
    if len(pts) > 1:
        shown = "; ".join(", ".join(f"{k} {getattr(r, k):g}" for k in have) for r in pts.head(4).itertuples())
        raise ValueError(f"{name} holds {len(pts)} locations ({shown}{' ...' if len(pts) > 4 else ''}). A reference is the wind at one "
                         "point: keep the rows of the point nearest the mast and take the others out")
    return ", ".join(f"{getattr(pts.iloc[0], k):g}" for k in have) if len(pts) and len(have) == 2 else ""


def checked_speed(speed, name: str, what: str) -> np.ndarray:
    """Wind speeds as numbers, refused when they cannot be wind speeds: infinite, negative, or beyond any mean wind."""
    v = np.asarray(speed, dtype=float)
    bad = np.isinf(v)
    if bad.any():
        raise ValueError(f"{name}: {int(bad.sum()):,} values of the {what} are infinite. Check the file before using it")
    ok = v[~np.isnan(v)]
    if (ok < 0).any():
        raise ValueError(f"{name}: {int((ok < 0).sum()):,} wind speeds are negative. Check which column holds the wind speed")
    if (ok > MAX_REFERENCE_SPEED).any():
        raise ValueError(f"{name}: {int((ok > MAX_REFERENCE_SPEED).sum()):,} wind speeds are above {MAX_REFERENCE_SPEED:g} m/s. "
                         "Check the column and its unit (m/s)")
    return v


def checked_speed_or_component(col, name: str, what: str) -> np.ndarray:
    """A wind component as numbers: it may be negative, never infinite."""
    v = np.asarray(pd.to_numeric(col, errors="coerce"), dtype=float)
    if np.isinf(v).any():
        raise ValueError(f"{name}: {int(np.isinf(v).sum()):,} values of the {what} are infinite. Check the file before using it")
    return v


def one_series(s: pd.Series, name: str) -> pd.Series:
    """One value per time step. The same time twice with the same value is a repeated row and is dropped; with
    different values the table holds more than one series, and which one is read must not hang on the row order."""
    s = s[s.index.notna()].sort_index(kind="stable")
    dup = s.index.duplicated(keep=False)
    if dup.any():
        spread = s[dup].groupby(level=0).agg(lambda x: float(x.max() - x.min()))
        clash = int((spread > 1e-6).sum())
        if clash:
            raise ValueError(f"{name}: {clash:,} time steps appear more than once with different wind speeds. The table seems to hold "
                             "more than one location or series: keep one")
        s = s[~s.index.duplicated()]
    return s


def read_reference_file(path) -> tuple:
    """A reference series from a table of your own: (monthly means as a period-indexed Series, what was read).

    For a second view of the long term beside NASA POWER: a point time series of another reanalysis, such as the
    ERA5 hourly time series of the Copernicus Climate Data Store saved as CSV (its zip is read as it comes). The
    table needs a time column and either a wind speed column or the two wind components (u and v), hourly, daily or
    monthly. Lines starting with # are skipped. Only complete months are used."""
    import io  # noqa: PLC0415
    import zipfile  # noqa: PLC0415
    p = Path(path)
    if not p.is_file():
        raise ValueError(f"the second reference file was not found: {p}")
    name = p.name
    if p.suffix.lower() == ".zip":
        with zipfile.ZipFile(p) as z:
            inner = [n for n in z.namelist() if n.lower().endswith((".csv", ".txt"))]
            if len(inner) != 1:
                raise ValueError(f"{p.name} holds {len(inner)} CSV files; it should hold one")
            raw, name = z.read(inner[0]), f"{p.name} ({inner[0]})"
    else:
        raw = p.read_bytes()
    try:
        df = pd.read_csv(io.BytesIO(raw), comment="#", sep=None, engine="python")
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"{p.name} could not be read as a table ({e})") from e
    cols = {str(c).strip().lower(): c for c in df.columns}
    tcol = next((cols[n] for n in TIME_NAMES if n in cols), None)
    if tcol is None:
        raise ValueError(f"{p.name} has no time column (looked for: {', '.join(TIME_NAMES)})")
    when = pd.to_datetime(df[tcol], errors="coerce", utc=True).dt.tz_localize(None)
    point = one_location(df, cols, p.name)
    pair = next(((cols[a], cols[b]) for a, b in PAIR_NAMES if a in cols and b in cols), None)
    scol = next((cols[n] for n in SPEED_NAMES if n in cols), None)
    if pair is not None:
        u = checked_speed(np.abs(checked_speed_or_component(df[pair[0]], p.name, f"component {pair[0]}")), p.name, f"component {pair[0]}")
        v = checked_speed(np.abs(checked_speed_or_component(df[pair[1]], p.name, f"component {pair[1]}")), p.name, f"component {pair[1]}")
        speed = checked_speed(np.hypot(u, v), p.name, "wind speed")
        what = f"wind speed from the components {pair[0]} and {pair[1]}"
    elif scol is not None:
        speed = checked_speed(pd.to_numeric(df[scol], errors="coerce"), p.name, f"wind speed column {scol}")
        what = f"wind speed column {scol}"
    else:
        raise ValueError(f"{p.name} has neither a wind speed column nor the two wind components "
                         f"(looked for: {', '.join(SPEED_NAMES[:5])} ...; u100 and v100 ...)")
    s = one_series(pd.Series(np.asarray(speed, dtype=float), index=pd.DatetimeIndex(when)).dropna(), p.name)
    if len(s) < 12:
        raise ValueError(f"{p.name} holds {len(s)} usable values: too few for a reference")
    step = float(np.median((s.index[1:] - s.index[:-1]).total_seconds())) / 3600.0      # hours between values
    g = s.groupby(s.index.to_period("M"))
    if step >= 24 * 27:                                                  # already monthly
        monthly, kind = g.mean(), "monthly values"
    else:
        expected = pd.Series({m: m.days_in_month * 24.0 / step for m in g.groups}, dtype=float)
        monthly = g.mean()[g.count() >= SECOND_MONTH_SHARE * expected]
        kind = "hourly values" if step <= 1.01 else "daily values" if abs(step - 24) < 0.5 else f"values every {step:g} hours"
    if len(monthly) < 24:
        raise ValueError(f"{p.name} gives {len(monthly)} complete months: a reference needs years")
    return monthly.sort_index(), (f"{name}: {what}, {kind}, {monthly.index[0]} to {monthly.index[-1]}"
                                  + (f", at {point}" if point else ""))


def second_reference(clim: dict, ref2: pd.Series, primary: dict, what: str) -> dict:
    """The long-term correction made again with a second reference, set beside the first: the two factors, the
    two long-term means and how far apart they are. The same long-term years are used when the second reference
    covers them, so that the two answer the same question."""
    y0, y1 = primary["lt_years"]
    counts = ref2.groupby(ref2.index.year).count()
    same = all(int(counts.get(y, 0)) == 12 for y in range(int(y0), int(y1) + 1))
    res = correct(clim, ref2, (int(y0), int(y1)) if same else None)
    diff = 100.0 * (res["long_term"] / primary["long_term"] - 1.0)
    out = {"what": what, "factor": res["factor"], "long_term": res["long_term"], "r": res["r"], "months": res["months"],
           "lt_years": list(res["lt_years"]), "same_years": bool(same), "difference_pct": float(diff),
           "mean_of_two": float((res["long_term"] + primary["long_term"]) / 2.0),
           "monthly": res["monthly"], "cautions": []}
    if not same:
        out["cautions"].append(f"The second reference does not cover every year of {y0} to {y1}; its own years "
                               f"{res['lt_years'][0]} to {res['lt_years'][1]} were used, so the two are not on the same footing.")
    if abs(diff) > 5.0:
        out["cautions"].append(f"The two long-term references disagree by {abs(diff):.1f} % on the long-term mean wind speed. "
                               "Find out why (a station change, a trend in one of them) before relying on either.")
    return out


def default_years(ref: pd.Series, n: int = LT_YEARS) -> tuple:
    """The last n complete calendar years of the reference."""
    counts = ref.groupby(ref.index.year).count()
    complete = [int(y) for y, c in counts.items() if c == 12]
    if not complete:
        raise ReferenceError_("The reference holds no complete calendar year.")
    last = max(complete)
    return max(min(complete), last - n + 1), last


def _momm(s: pd.Series) -> float:
    return float(s.groupby(s.index.month).mean().mean())


def broken_tail(measured: pd.Series, ref: pd.Series, n_last: int = 3) -> list:
    """Trailing concurrent months whose mast-to-reference ratio breaks from the same calendar month in other years
    (or from the rest of the series), newest first; stops at the first month that fits.

    Only the reference's own latest months are looked at: that is where NASA POWER appends a different model to
    MERRA-2. Months of an older campaign are never dropped, however much the ratio swings with the season."""
    ratio = (measured / ref.reindex(measured.index)).dropna()
    out = []
    newest = ref.index[-1]
    for p in list(ratio.index[-n_last:])[::-1]:
        if (newest.year - p.year) * 12 + newest.month - p.month >= TAIL_MONTHS:
            break
        same = ratio[(ratio.index.month == p.month) & (ratio.index != p)]
        rest = ratio.drop(p)
        if not len(same) and len(rest) < 3:
            break
        usual = float(same.median()) if len(same) else float(rest.median())
        if abs(float(ratio[p]) / usual - 1.0) <= REF_JUMP:
            break
        out.append({"month": str(p), "mast_mean": float(measured[p]), "reference": float(ref[p]),
                    "ratio": float(ratio[p]), "usual_ratio": usual})
    return out


def monthly_index(measured: pd.Series, ref: pd.Series, lt_years: tuple | None = None) -> dict:
    """measured, ref: monthly means with a period index. Returns the long-term factor and what it rests on."""
    measured = measured.dropna().sort_index()
    measured = measured[measured.index.isin(ref.index)]
    if len(measured) < 3:
        raise ValueError("At least 3 measured months that the reference also covers are needed for a long-term index.")
    lt_years = lt_years or default_years(ref)
    dropped = broken_tail(measured, ref)
    measured = measured[~measured.index.isin([pd.Period(d["month"], "M") for d in dropped])]
    conc = ref.reindex(measured.index)
    lt = ref[f"{lt_years[0]}-01":f"{lt_years[1]}-12"]
    if lt.groupby(lt.index.month).count().reindex(range(1, 13)).fillna(0).min() < 1:
        raise ValueError("The reference does not cover every calendar month in the long-term years.")
    cal = sorted(set(measured.index.month))
    r = float(np.corrcoef(measured, conc)[0, 1]) if len(measured) >= 3 else float("nan")
    if len(cal) == 12:
        m, rp, method = _momm(measured), _momm(conc), "mean of monthly means, all 12 calendar months measured"
    else:
        m, rp = float(measured.mean()), float(conc.mean())
        method = (f"only {len(cal)} calendar months measured: ratio of the long-term annual reference to the reference "
                  "over the months measured; assumes the mast-to-reference ratio holds in the months not measured")
    rl = _momm(lt)
    factor = rl / rp
    return {"measured": m, "reference_concurrent": rp, "reference_long_term": rl, "factor": float(factor),
            "long_term": float(m * factor), "r": r, "months": int(len(measured)), "calendar_months": len(cal),
            "first_month": str(measured.index[0]), "last_month": str(measured.index[-1]),
            "lt_years": [int(lt_years[0]), int(lt_years[1])], "method": method, "reference_months_left_out": dropped,
            "indicative": len(cal) < 12,
            "monthly": [{"month": str(p), "mast": float(measured[p]), "reference": float(conc[p])} for p in measured.index]}


def measured_months(clim: dict, ref: pd.Series) -> tuple:
    """(monthly series for the index, partial months used). Months with at least 70 % valid data count. When that
    leaves a calendar month uncovered, its best month with at least 40 % valid data stands in, provided this
    completes the year: a thin February is a better guide to February than the other eleven months are. Seen on a
    real mast, where leaving the thin month out moved the long-term mean noticeably. A campaign that stays short of
    twelve calendar months gains nothing from a thin month at full weight, so there it is left out."""
    series = _climate.monthly_series(clim)
    stand_in = {}
    for cal in sorted(set(range(1, 13)) - set(series.index.month)):
        best = None
        for key, v in clim["monthly"].items():
            p = pd.Period(key, "M")
            if p.month == cal and PARTIAL_SHARE <= v["share"] < _climate.MIN_MONTH_SHARE and p in ref.index:
                if best is None or v["share"] > best[1]:
                    best = (p, v["share"], v["mean"])
        if best:
            stand_in[cal] = best
    if not stand_in or len(set(series.index.month) | set(stand_in)) < 12:
        return series, []
    used = []
    for p, share, mean in stand_in.values():
        series[p] = mean
        used.append({"month": str(p), "valid_share": round(float(share), 3)})
    return series.sort_index(), used


def correct(clim: dict, ref: pd.Series, lt_years: tuple | None = None) -> dict:
    """Long-term correction of a wind climate (see bohemian_wind.climate)."""
    series, partial = measured_months(clim, ref)
    res = monthly_index(series, ref, lt_years)
    res["partial_months_used"] = partial
    cautions = []
    if res["indicative"]:
        cautions.append(f"Only {res['calendar_months']} of the 12 calendar months were measured. The factor assumes the months "
                        "not measured behave like the ones that were. Measure a full year before relying on it.")
    if partial:
        cautions.append("To cover all twelve calendar months, " + ", ".join(
            f"{pd.Period(m['month'], 'M').strftime('%B %Y')} is used with only {100 * m['valid_share']:.0f} % valid data" for m in partial)
            + ". The factor leans on that thin month: check it before relying on the result.")
    res["cautions"] = cautions
    res["height_m"] = clim["height_m"]
    res["credit"] = POWER_CREDIT
    return res
