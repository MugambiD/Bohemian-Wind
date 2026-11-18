"""Step 2: long-term correction. Scales a short measurement campaign to the long-term wind climate with a monthly
index against reanalysis data (NASA POWER: MERRA-2 wind speed at 50 m).

Method (one method, on purpose; more will follow):
- All 12 calendar months measured: compare the mean of monthly means of the mast with the same months of the
  reference, then with the reference's long-term mean of monthly means.
- Fewer calendar months measured: ratio of the long-term annual reference to the reference over the months
  measured. This assumes the mast-to-reference ratio holds in the months not measured, and says so.
- The last reference months are checked before use: NASA POWER appends another model (GEOS-IT) to the end of the
  MERRA-2 series for its latest weeks, and such a month can break the mast-to-reference relation.

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


class ReferenceError_(RuntimeError):
    """The reference data could not be fetched or read."""


def power_url(lat: float, lon: float, step: str, start: str, end: str) -> str:
    return (f"{POWER_API.format(step=step)}?parameters=WS50M&community=RE&longitude={lon:.4f}&latitude={lat:.4f}"
            f"&start={start}&end={end}&format=JSON")


def fetch_power(lat: float, lon: float, dest_dir, start_year: int = 2001, today: date | None = None,
                timeout: float = 60.0) -> dict:
    """Downloads the monthly series for complete years and the daily series for the current year, into dest_dir.
    Returns the two file paths. Needs an internet connection."""
    today = today or date.today()
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    out = {}
    jobs = [("monthly", str(start_year), str(today.year - 1), dest / "power_monthly.json"),
            ("daily", f"{today.year}0101", f"{today:%Y%m%d}", dest / "power_daily.json")]
    for step, a, b, path in jobs:
        url = power_url(lat, lon, step, a, b)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "bohemian-wind"})
            with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (fixed https host)
                body = r.read()
            json.loads(body)["properties"]["parameter"]["WS50M"]
        except (urllib.error.URLError, OSError, KeyError, ValueError) as e:
            if step == "daily" and "monthly" in out:
                continue        # the current year is a bonus: complete years are enough
            raise ReferenceError_(f"Could not download the reference data from NASA POWER ({e}). Check the internet "
                                  "connection, or download the file in a browser and choose 'Use a file'.") from e
        path.write_bytes(body)
        out[step] = str(path)
    return out


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
    (or from the rest of the series), newest first; stops at the first month that fits."""
    ratio = (measured / ref.reindex(measured.index)).dropna()
    out = []
    for p in list(ratio.index[-n_last:])[::-1]:
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


def correct(clim: dict, ref: pd.Series, lt_years: tuple | None = None) -> dict:
    """Long-term correction of a wind climate (see bohemian_wind.climate). Uses the months with at least 70 % of
    their records valid."""
    res = monthly_index(_climate.monthly_series(clim), ref, lt_years)
    res["height_m"] = clim["height_m"]
    res["credit"] = POWER_CREDIT
    return res
