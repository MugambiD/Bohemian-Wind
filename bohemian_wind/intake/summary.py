"""Step 1 from summary tables: a wind climate for a mast whose ten-minute records are not at hand.

Some campaigns reach a reviewer only as monthly reports: a mean wind speed per height and month, and how much of
the month was valid. That is enough for a first view of the long-term mean, the shear and a wind map, and far too
little for the checks this program makes on logger files. This reader takes one small file, wind_summary.json,
which the analyst fills from the reports, and writes the same hand-off (climate.json) the later steps work on.

What the tables cannot give is said in the result and in every report:
  - no record was checked: the means are taken as the reports state them;
  - the shape of the wind speed distribution is one number, the Weibull shape factor the analyst enters;
  - the wind directions come from a reanalysis series when the mast measured none (or the reports give none):
    their frequency and the ratio of each sector's mean to the overall mean are the reanalysis's, at its own
    grid point and height. Terrain turns and channels the wind at a real site; a reanalysis does not see that.

wind_summary.json:
{
 "format": "bohemian-wind-summary", "version": 1,
 "source": "what the tables were taken from",
 "site": {"name": "", "lat": 0.0, "lon": 0.0, "elevation_m": 0.0, "position_basis": "surveyed | inferred from ..."},
 "interval_s": 600,
 "months": [{"month": "2026-03", "share": 0.98, "mean_ws": {"80": 7.1, "60": 6.8}}, ...],
 "weibull_k": 2.4, "weibull_k_basis": "why this value",
 "direction": {"file": "reference.csv", "what": "ERA5 at 100 m, nearest grid point"},
 "findings": ["anything a reader should know about the tables"]
}
"share" is the valid share of the calendar month (0 to 1). A height missing from a month is left out there.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from .. import __version__
from .. import climate as _climate

NAME = "wind_summary.json"
FORMAT = "bohemian-wind-summary"
LABEL = "Monthly summary tables (no ten-minute records)"
MEASUREMENT_PCT = 4.0          # 1 sigma, % of wind speed: sensors, mounting and data handling that could not be checked
DEFAULT_K = 2.0
MIN_SECTOR_HOURS = 100


class SummaryError(ValueError):
    pass


def find(path) -> Path | None:
    """The summary file of a data folder, or the file itself; None when the folder holds none."""
    p = Path(path)
    if p.is_file():
        return p if p.name.lower() == NAME else None
    f = p / NAME
    return f if f.is_file() else None


def _num(v, what: str, lo: float, hi: float) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise SummaryError(f"{what}: {v!r} is not a number.") from None
    if not math.isfinite(f) or not lo <= f <= hi:
        raise SummaryError(f"{what}: {f:g} is outside {lo:g} to {hi:g}.")
    return f


def read(path) -> dict:
    """The summary file, checked: heights as numbers, months in order."""
    path = Path(path)
    try:
        spec = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as e:
        raise SummaryError(f"{path.name} could not be read: {e}") from e
    if not isinstance(spec, dict) or spec.get("format") != FORMAT:
        raise SummaryError(f'{path.name} is not a wind summary: "format" must be "{FORMAT}".')
    months = {}
    for row in spec.get("months") or []:
        try:
            p = pd.Period(str(row["month"]), "M")
        except (KeyError, ValueError):
            raise SummaryError(f"A month could not be read: {row!r}. Write months as YYYY-MM.") from None
        if p in months:
            raise SummaryError(f"{p} is listed twice. One row per calendar month: join overlapping report periods first.")
        means = {_num(h, "A measurement height", 2, 400): _num(v, f"Mean wind speed in {p}", 0, 40)
                 for h, v in (row.get("mean_ws") or {}).items() if v is not None}
        if not means:
            continue
        months[p] = {"share": _num(row.get("share", 1.0), f"Valid share of {p}", 0, 1), "mean": means}
    if not months:
        raise SummaryError("The summary holds no month with a mean wind speed.")
    spec["_months"] = dict(sorted(months.items()))
    spec["_path"] = path
    return spec


def _weighted(months: dict, h: float) -> tuple:
    """(mean over all valid time, valid days) at one height."""
    w = np.array([m["share"] * p.days_in_month for p, m in months.items() if h in m["mean"]])
    v = np.array([m["mean"][h] for p, m in months.items() if h in m["mean"]])
    return (float((w * v).sum() / w.sum()), float(w.sum())) if w.sum() > 0 else (float("nan"), 0.0)


def _momm(months: dict, h: float, min_share: float = _climate.MIN_MONTH_SHARE) -> tuple:
    cal = {}
    for p, m in months.items():
        if h in m["mean"] and m["share"] >= min_share:
            cal.setdefault(p.month, []).append(m["mean"][h])
    by = [float(np.mean(v)) for v in cal.values()]
    return (float(np.mean(by)) if len(by) == 12 else None), len(by)


ROSE_HOURS = 24 * 365          # hours of wind a rose needs: a year, so that every season is in it
CALM_MS = 0.05                 # below this a wind given as components has no direction
ROSE_COVERAGE = 0.90           # share of the hours between the first and the last that must be there


def read_directions(path) -> pd.DataFrame:
    """Reference wind for the rose: one row per reading with ws (m/s), wd (degrees the wind comes from, 0 to below 360)
    and w, its weight: one over the number of readings in its hour, so that every hour counts once however often it
    was sampled and every reading keeps its own direction. Reads a CSV, or a zip holding one CSV, with a time column
    and either u/v components or a speed and a direction, hourly or finer. A direction outside 0 to 360 degrees is
    not a direction (360 is north) and its reading is left out and counted; so is a reading with no wind when the
    wind is given as components. At least a year of hours with a direction is needed, without long gaps. The frame's
    attrs say how many hours and records there were and how many were left out."""
    from ..longterm import (PAIR_NAMES, TIME_NAMES, checked_speed, checked_speed_or_component, one_location,  # noqa: PLC0415
                            one_record_per_time, parsed_times)
    path = Path(path)
    if not path.is_file():
        raise SummaryError(f"The direction file was not found: {path.name}.")
    try:
        if path.suffix.lower() == ".zip":
            import io  # noqa: PLC0415
            import zipfile  # noqa: PLC0415
            with zipfile.ZipFile(path) as z:
                names = sorted(n for n in z.namelist() if n.lower().endswith(".csv"))
                if len(names) != 1:                          # with two, which one is the wind would hang on their order
                    raise SummaryError(f"The direction file {path.name} holds {len(names)} CSV files; it should hold one. "
                                       "Take the one for the point nearest the mast out of the archive and name that file.")
                df = pd.read_csv(io.BytesIO(z.read(names[0])))
        else:
            df = pd.read_csv(path)
    except SummaryError:
        raise
    except (OSError, ValueError, pd.errors.ParserError) as e:
        raise SummaryError(f"The direction file {path.name} could not be read: {e}") from e
    low = {str(c).strip().lower(): c for c in df.columns}
    tcol = next((low[n] for n in TIME_NAMES if n in low), None)
    if tcol is None:
        raise SummaryError(f"The direction file {path.name} has no time column ({', '.join(TIME_NAMES)}).")
    d = next((low[n] for n in ("wind_direction_100m", "wd100", "wind_direction", "wd", "direction") if n in low), None)
    sp = next((low[n] for n in ("wind_speed_100m", "ws100", "wind_speed", "ws", "speed") if n in low), None)
    pair = next(((low[a], low[b]) for a, b in PAIR_NAMES if a in low and b in low), None)
    if pair is None and (d is None or sp is None):
        raise SummaryError(f"The direction file {path.name} needs the wind as u and v components, or as a speed and a direction.")
    try:
        when = pd.DatetimeIndex(parsed_times(df[tcol], path.name))
        one_location(df, low, path.name)                     # one point, or which wind is read would hang on the row order
        if pair is not None:
            u = checked_speed_or_component(df[pair[0]], path.name, f"component {pair[0]}")
            v = checked_speed_or_component(df[pair[1]], path.name, f"component {pair[1]}")
            checked_speed(np.hypot(u, v), path.name, "wind speed")
            rec = one_record_per_time(pd.DataFrame({"u": u, "v": v}, index=when), path.name)
            ws = np.hypot(rec["u"].to_numpy(), rec["v"].to_numpy())
            wd = np.degrees(np.arctan2(-rec["u"].to_numpy(), -rec["v"].to_numpy())) % 360.0
            wd[ws < CALM_MS] = np.nan                        # no wind, no direction: the bearing of a zero vector means nothing
            rec = pd.DataFrame({"ws": ws, "wd": wd}, index=rec.index)
        else:
            ws = checked_speed(pd.to_numeric(df[sp], errors="coerce"), path.name, f"wind speed column {sp}")
            wd = np.array(pd.to_numeric(df[d], errors="coerce"), dtype=float)
            wd[~((wd >= 0.0) & (wd <= 360.0))] = np.nan      # the bearing as it stands: a missing-value code (-999, 9999) is not a direction
            wd[wd == 360.0] = 0.0                            # 360 is north
            both = pd.DataFrame({"ws": ws, "wd": wd}, index=when)
            both = both[both["ws"].notna()]
            both["wd"] = both["wd"].fillna(-1.0)             # kept through the check for repeated rows, then taken out again
            rec = one_record_per_time(both, path.name)
            rec["wd"] = rec["wd"].where(rec["wd"] >= 0.0)
    except ValueError as e:
        raise SummaryError(f"The direction file cannot be used: {e}.") from e
    records = len(rec)
    out = rec.dropna()
    unusable = records - len(out)
    left = (f" {unusable:,} of its {records:,} records have no usable direction (outside 0 to 360 degrees, missing, or no wind) "
            "and were left out.") if unusable else ""
    if not len(out):
        raise SummaryError(f"The direction file {path.name} holds no usable wind direction: all {records:,} records have a "
                           "direction outside 0 to 360 degrees, a missing one, or no wind. Check the direction column and its "
                           "missing-value code.")
    # every hour counts once, and every reading keeps its own direction: a reading weighs one over the number of readings
    # in its hour. (One direction per hour from the mean wind vector would invent a bearing when the wind turns within the
    # hour: north and south in equal parts would come out as east.)
    hour = out.index.floor("h")
    out = out.assign(w=1.0 / pd.Series(hour, index=out.index).groupby(hour).transform("size").to_numpy())
    first, last, hours = hour.min(), hour.max(), int(hour.nunique())
    if hours < ROSE_HOURS:
        raise SummaryError(f"The direction file {path.name} holds wind with a direction for {hours:,} different hours, from "
                           f"{first:%d %b %Y %H:%M} to {last:%d %b %Y %H:%M} ({records:,} records): at least a year of hours "
                           f"({ROSE_HOURS:,}) is needed for a rose.{left}")
    between = int((last - first) / pd.Timedelta(hours=1)) + 1
    if hours < ROSE_COVERAGE * between:
        raise SummaryError(f"The direction file {path.name} has gaps: wind with a direction for {hours:,} of the {between:,} hours "
                           f"between {first:%d %b %Y} and {last:%d %b %Y} ({100 * hours / between:.0f} %). "
                           f"A rose needs hourly wind for at least {100 * ROSE_COVERAGE:.0f} % of its period.{left}")
    out.attrs.update(hours=hours, records=records, unusable=unusable, first=first, last=last)
    return out


def _gamma(x: float) -> float:
    return math.gamma(x)


def _cv2(k: float) -> float:
    """Squared coefficient of variation of a Weibull distribution."""
    return _gamma(1 + 2 / k) / _gamma(1 + 1 / k) ** 2 - 1.0


def sector_shape(freq, ratio, k_all: float) -> float:
    """The shape factor every sector gets so that the sectors together are as wide as one Weibull with k_all.
    Sectors with different means, each as wide as k_all, add up to a wider distribution than k_all; a slightly
    larger k per sector brings the whole back to the stated shape."""
    f, r = np.asarray(freq, dtype=float), np.asarray(ratio, dtype=float)
    f = f / f.sum()
    r = r / (f * r).sum()
    target = 1.0 + _cv2(k_all)                  # E[v^2] / mean^2 of the whole
    spread = float((f * r * r).sum())
    if spread >= target:                         # the sector means alone are wider than the stated shape allows
        return 12.0
    lo, hi = k_all, 12.0
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if spread * (1.0 + _cv2(mid)) > target:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def build_climate(spec: dict, n_sectors: int = 12) -> dict:
    months = spec["_months"]
    heights = sorted({h for m in months.values() for h in m["mean"]})
    top = heights[-1]
    interval = _num(spec.get("interval_s", 600), "interval_s", 1, 3600)
    k_all = _num(spec.get("weibull_k", DEFAULT_K), "weibull_k", 1.2, 4.0)
    mean_top, days_top = _weighted(months, top)
    if not math.isfinite(mean_top):
        raise SummaryError("No month has valid data at the top height.")
    A_all = mean_top / _gamma(1 + 1 / k_all)
    width = 360.0 / n_sectors
    dspec = spec.get("direction") or {}
    sectors, no_direction, dir_note = [], True, ""
    if dspec.get("file"):
        ref = read_directions(spec["_path"].parent / str(dspec["file"]))
        sec = (np.floor(((ref["wd"].to_numpy() + width / 2) % 360) / width)).astype(int)
        ws, wt = ref["ws"].to_numpy(), ref["w"].to_numpy()             # weights: every hour once
        overall = float((ws * wt).sum() / wt.sum())
        in_sec = [float(wt[sec == i].sum()) for i in range(n_sectors)]  # hours in each sector
        freq = np.array(in_sec) / float(wt.sum())
        ratio = np.array([float((ws * wt)[sec == i].sum() / in_sec[i]) / overall if in_sec[i] >= MIN_SECTOR_HOURS else 1.0
                          for i in range(n_sectors)])
        ratio = ratio / float((freq * ratio).sum())
        k_sec = sector_shape(freq, ratio, k_all)
        for i in range(n_sectors):
            m = mean_top * ratio[i]
            sectors.append({"centre": i * width, "freq": float(freq[i]), "A": float(m / _gamma(1 + 1 / k_sec)), "k": float(k_sec),
                            "mean": float(m), "records": 0, "reference_records": int(round(in_sec[i]))})
        no_direction = False
        dir_note = str(dspec.get("what") or "reference series") + (f" ({ref.attrs['hours']:,} hours, {ref.attrs['first']:%b %Y} to {ref.attrs['last']:%b %Y}"
                                                                         + (f"; {ref.attrs['unusable']:,} of {ref.attrs['records']:,} records had no usable "
                                                                            "direction and were left out" if ref.attrs["unusable"] else "") + ")")
    else:
        for i in range(n_sectors):
            sectors.append({"centre": i * width, "freq": 1.0 / n_sectors, "A": A_all, "k": k_all, "mean": mean_top, "records": 0})
    # shear: over the months in which every height has a mean, weighted by their valid time
    common = {p: m for p, m in months.items() if all(h in m["mean"] for h in heights)}
    sh = {}
    if len(heights) >= 2 and common:
        means = {h: _weighted(common, h)[0] for h in heights}
        sh = {"heights": heights, "means": means,
              "alpha_top_bottom": float(np.log(means[top] / means[heights[0]]) / np.log(top / heights[0])), "lo": heights[0], "hi": top}
        if len(heights) > 2:
            sh["alpha_fit"] = float(np.polyfit(np.log(heights), np.log([means[h] for h in heights]), 1)[0])
    alpha, basis = _climate.extrapolation_shear(sh) if sh else (None, "one measurement height: no shear measured")
    warning = ""
    if alpha is not None and not _climate.SHEAR_RANGE[0] <= alpha <= _climate.SHEAR_RANGE[1]:
        warning = (f"The shear exponent {alpha:.2f} ({basis}) is outside the usual range of {_climate.SHEAR_RANGE[0]:g} to "
                   f"{_climate.SHEAR_RANGE[1]:g}. Check the heights in the reports before relying on anything carried to another height.")
    momm_top, ncal = _momm(months, top)
    first, last = min(months), max(months)
    period = spec.get("period") or [str(first.start_time), str(last.end_time.floor("s"))]
    try:
        span_days = (pd.Timestamp(period[1]) - pd.Timestamp(period[0])).total_seconds() / 86400.0
    except (ValueError, TypeError):
        raise SummaryError(f'"period" could not be read: {period!r}.') from None
    stats = []
    for h in reversed(heights):
        m, days = _weighted(months, h)
        mm, _ = _momm(months, h)
        stats.append({"height_m": float(h), "mean_ws": m, "momm_ws": mm, "recovery_pct": round(100.0 * days / span_days, 2) if span_days > 0 else None,
                      "weibull_A": m / _gamma(1 + 1 / k_all), "weibull_k": k_all})
    site = spec.get("site") or {}

    def opt(key, lo, hi):
        return None if site.get(key) is None else _num(site[key], f"site {key}", lo, hi)

    return {
        "height_m": float(top), "n_sectors": n_sectors, "sectors": sectors, "no_direction": no_direction,
        "mean_ws": mean_top, "momm_ws": momm_top, "calendar_months": int(ncal),
        "weibull_A": float(A_all), "weibull_k": float(k_all),
        "shear": {"alpha": alpha, "basis": basis + ", from the monthly means" if alpha is not None else basis,
                  "alpha_all_heights": sh.get("alpha_fit"), "warning": warning},
        "ti": {"mean_above_3": None, "representative_15": None},
        "density": {"mean": None, "sensor_height_m": None, "pressure_hpa": None, "pressure_valid_pct": None,
                    "pressure_sensor_height_m": None, "temperature_c": None},
        "monthly": {str(p): {"mean": float(m["mean"][top]), "share": round(float(m["share"]), 4)} for p, m in months.items() if top in m["mean"]},
        "heights": stats, "direction_days": None, "period": [str(period[0]), str(period[1])],
        "records": int(round(days_top * 86400.0 / interval)),
        "recovery_pct": round(100.0 * days_top / span_days, 2) if span_days > 0 else 0.0,
        "site": {"name": str(site.get("name") or ""), "lat": opt("lat", -90, 90), "lon": opt("lon", -180, 180),
                 "elevation_m": opt("elevation_m", -500, 6000)},
        "source_format": LABEL,
        "summary": {"source": str(spec.get("source") or "summary tables"), "weibull_k_basis": str(spec.get("weibull_k_basis") or
                    ("entered by the analyst" if "weibull_k" in spec else f"none entered: {DEFAULT_K:g} assumed")),
                    "direction": dir_note, "position_basis": str(site.get("position_basis") or ""),
                    "measurement_pct": MEASUREMENT_PCT},
    }


def findings(spec: dict, clim: dict) -> list:
    s = clim["summary"]
    out = [f"This wind climate rests on monthly summary tables ({s['source']}), not on ten-minute records. No record was checked "
           "by this program: sensor faults, icing, tower shading and clock errors cannot be seen in a monthly mean.",
           f"The shape of the wind speed distribution is one number, Weibull k = {clim['weibull_k']:.2f} ({s['weibull_k_basis']})."]
    if clim["no_direction"]:
        out.append("No wind direction is given: every direction is treated alike in later steps.")
    else:
        out.append(f"No wind direction was measured or reported. The wind rose is taken from {s['direction']}: its sector frequencies, "
                   "and each sector's mean wind speed in proportion. Terrain turns the wind at a real site in ways a reanalysis grid "
                   "does not see, so the rose is a first view, to be replaced by a measured one.")
    if s["position_basis"]:
        out.append(f"Mast position: {s['position_basis']}.")
    thin = [str(p) for p, m in spec["_months"].items() if m["share"] < _climate.MIN_MONTH_SHARE]
    if thin:
        out.append(f"{len(thin)} months have less than {100 * _climate.MIN_MONTH_SHARE:.0f} % valid data and do not count towards the "
                   "long-term correction: " + ", ".join(thin) + ".")
    if clim["calendar_months"] < 12:
        out.append(f"Only {clim['calendar_months']} of the 12 calendar months have a month with enough valid data. The seasons not "
                   "measured are missing from the measured mean.")
    out += [str(x) for x in spec.get("findings") or []]
    return out


def run(path, out_root, site_name: str | None = None, settings_path=None, log=print) -> Path:
    """Write a run folder (climate.json, findings.txt, summary.xlsx, inputs.csv) from a summary file. Returns it."""
    from ..pipeline import safe_name  # noqa: PLC0415
    f = find(path)
    if f is None:
        raise SummaryError(f"No {NAME} here.")
    spec = read(f)
    if settings_path and Path(settings_path).is_file():           # position entered in the project replaces the file's
        own = json.loads(Path(settings_path).read_text(encoding="utf-8"))
        site = dict(spec.get("site") or {})
        for a, b in (("latitude", "lat"), ("longitude", "lon"), ("elevation_m", "elevation_m")):
            if own.get(a) is not None:
                site[b] = own[a]
        spec["site"] = site
    clim = build_climate(spec)
    if site_name:
        clim["site"]["name"] = site_name
    name = clim["site"]["name"] or f.parent.name
    run_dir = Path(out_root) / "runs" / f"{safe_name(name)}_{datetime.now():%Y%m%d-%H%M%S}"
    run_dir.mkdir(parents=True, exist_ok=True)
    _climate.save(run_dir / "climate.json", clim)
    found = findings(spec, clim)
    (run_dir / "findings.txt").write_text("\n".join(f"{i + 1}. {t}" for i, t in enumerate(found)) + "\n", encoding="utf-8")
    heights = sorted({h for m in spec["_months"].values() for h in m["mean"]}, reverse=True)
    table = pd.DataFrame([{"month": str(p), "valid_share_%": round(100 * m["share"], 1), **{f"{h:g} m": m["mean"].get(h) for h in heights}}
                          for p, m in spec["_months"].items()])
    try:
        with pd.ExcelWriter(run_dir / "summary.xlsx") as xl:
            table.to_excel(xl, sheet_name="Monthly means", index=False)
            pd.DataFrame(clim["heights"]).to_excel(xl, sheet_name="Heights summary", index=False)
            pd.DataFrame(clim["sectors"]).to_excel(xl, sheet_name="Sectors", index=False)
    except (OSError, ImportError, ValueError) as e:
        log(f"summary.xlsx not written: {e}")
    rows = ["file,bytes,sha256"]
    files = [f] + ([f.parent / str(spec["direction"]["file"])] if (spec.get("direction") or {}).get("file") else [])
    for x in files:
        b = x.read_bytes()
        rows.append(f"{x.name},{len(b)},{hashlib.sha256(b).hexdigest()}")
    (run_dir / "inputs.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    (run_dir / "run.json").write_text(json.dumps({"reader": "summary", "version": __version__, "source": clim["summary"]["source"]}, indent=1),
                                      encoding="utf-8")
    log(f"Summary tables: {len(spec['_months'])} months, heights {', '.join(f'{h:g}' for h in heights)} m; mean at {clim['height_m']:g} m "
        f"{clim['mean_ws']:.2f} m/s over the valid time.")
    for t in found[:4]:
        log("  " + t)
    return run_dir
