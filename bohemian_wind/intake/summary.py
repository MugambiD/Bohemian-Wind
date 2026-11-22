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


def read_directions(path) -> pd.DataFrame:
    """Hourly (or finer) reference wind: columns ws (m/s) and wd (degrees the wind comes from). Reads a CSV, or a
    zip holding one, with a time column and either u/v components or a speed and a direction."""
    from ..longterm import PAIR_NAMES, TIME_NAMES  # noqa: PLC0415
    path = Path(path)
    if not path.is_file():
        raise SummaryError(f"The direction file was not found: {path.name}.")
    try:
        if path.suffix.lower() == ".zip":
            import io  # noqa: PLC0415
            import zipfile  # noqa: PLC0415
            with zipfile.ZipFile(path) as z:
                names = [n for n in z.namelist() if n.lower().endswith(".csv")]
                if not names:
                    raise SummaryError(f"{path.name} holds no CSV file.")
                df = pd.read_csv(io.BytesIO(z.read(names[0])))
        else:
            df = pd.read_csv(path)
    except (OSError, ValueError, pd.errors.ParserError) as e:
        raise SummaryError(f"The direction file {path.name} could not be read: {e}") from e
    low = {str(c).strip().lower(): c for c in df.columns}
    if not any(n in low for n in TIME_NAMES):
        raise SummaryError(f"The direction file {path.name} has no time column ({', '.join(TIME_NAMES)}).")
    for a, b in PAIR_NAMES:
        if a in low and b in low:
            u, v = pd.to_numeric(df[low[a]], errors="coerce"), pd.to_numeric(df[low[b]], errors="coerce")
            out = pd.DataFrame({"ws": np.hypot(u, v), "wd": np.degrees(np.arctan2(-u, -v)) % 360.0})
            break
    else:
        d = next((low[n] for n in ("wind_direction_100m", "wd100", "wind_direction", "wd", "direction") if n in low), None)
        s = next((low[n] for n in ("wind_speed_100m", "ws100", "wind_speed", "ws", "speed") if n in low), None)
        if d is None or s is None:
            raise SummaryError(f"The direction file {path.name} needs the wind as u and v components, or as a speed and a direction.")
        out = pd.DataFrame({"ws": pd.to_numeric(df[s], errors="coerce"), "wd": pd.to_numeric(df[d], errors="coerce") % 360.0})
    out = out.dropna()
    if len(out) < 24 * 365:
        raise SummaryError(f"The direction file {path.name} holds {len(out):,} records: at least a year of hourly wind is needed for a rose.")
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
        ws = ref["ws"].to_numpy()
        overall = float(ws.mean())
        freq = np.array([(sec == i).mean() for i in range(n_sectors)])
        ratio = np.array([ws[sec == i].mean() / overall if (sec == i).sum() >= MIN_SECTOR_HOURS else 1.0 for i in range(n_sectors)])
        ratio = ratio / float((freq * ratio).sum())
        k_sec = sector_shape(freq, ratio, k_all)
        for i in range(n_sectors):
            m = mean_top * ratio[i]
            sectors.append({"centre": i * width, "freq": float(freq[i]), "A": float(m / _gamma(1 + 1 / k_sec)), "k": float(k_sec),
                            "mean": float(m), "records": 0, "reference_records": int((sec == i).sum())})
        no_direction = False
        dir_note = str(dspec.get("what") or "reference series") + f" ({len(ref):,} records)"
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
