"""Wind climate hand-off: what the later steps (long-term correction, wind maps, layout, energy yield) need from
the mast analysis, in one small JSON file written next to every run.

The climate describes the wind at the top measurement height: for each direction sector its frequency and a
Weibull distribution, plus the shear exponent, turbulence, air density and the monthly means the long-term
correction works on. Sector 0 is centred on north; sectors run clockwise.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from .analysis import momm, sector, weibull_fit

MIN_PAIR_RATIO = 1.15        # heights closer together than this make a poor shear pair: at a 4 m gap near 120 m,
#                              a 0.5 % error in one cup moves the exponent by 0.15
SHEAR_RANGE = (0.0, 0.35)    # exponents outside this are unusual enough to check the sensors before using them
SHEAR_SPREAD = 0.10          # top pair against the fit over all heights: further apart than this, the profile is not clean
MIN_SECTOR_RECORDS = 200     # fewer records than this: the sector borrows the all-direction shape factor
MIN_MONTH_SHARE = 0.70       # a month counts towards long-term work when at least this share of it is valid
_gamma = np.vectorize(math.gamma)


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def extrapolation_shear(sh: dict) -> tuple:
    """(exponent, what it rests on) for carrying the wind above the mast: the top height with the highest height
    below it that is far enough away. Two cups a few metres apart measure the same wind, not the shear."""
    heights, means = list(sh.get("heights") or []), sh.get("means")
    if len(heights) < 2 or means is None:
        if "alpha_top_pair" in sh:
            return float(sh["alpha_top_pair"]), f"{sh['top_pair'][0]:g}-{sh['top_pair'][1]:g} m pair"
        if "alpha_top_bottom" in sh:
            return float(sh["alpha_top_bottom"]), f"{sh['lo']:g}-{sh['hi']:g} m pair"
        return None, "one measurement height: no shear measured"
    hi = heights[-1]
    for lo in reversed(heights[:-1]):
        if hi / lo >= MIN_PAIR_RATIO and means[lo] > 0 and means[hi] > 0:
            return float(np.log(means[hi] / means[lo]) / np.log(hi / lo)), f"{lo:g}-{hi:g} m pair"
    lo = heights[0]
    return float(sh["alpha_top_bottom"]), f"{lo:g}-{hi:g} m pair, heights close together"


def from_analysis(an: dict, time_qc: dict, site: dict | None = None, source_format: str = "",
                  n_sectors: int = 12) -> dict:
    """an: result of analysis.analyse; time_qc: qc["time"]; site: name, lat, lon, elevation_m (any may be missing)."""
    hs = an.get("heights") or {}
    if not hs:
        raise ValueError("no wind speed heights in the analysis")
    top = max(hs)
    ws, sd, wd = hs[top]["ws"], hs[top]["sd"], hs[top].get("wd")
    v = ws.dropna()
    A_all, k_all = weibull_fit(v)
    if not np.isfinite(k_all):
        raise ValueError("too few valid wind speed records for a wind climate")
    width = 360.0 / n_sectors
    sectors, no_direction = [], wd is None or wd.notna().sum() == 0
    if no_direction:
        for i in range(n_sectors):
            sectors.append({"centre": i * width, "freq": 1.0 / n_sectors, "A": A_all, "k": k_all,
                            "mean": float(v.mean()), "records": 0})
    else:
        m = ws.notna() & wd.notna()
        sec = sector(wd[m], n_sectors)
        total = int(m.sum())
        for i in range(n_sectors):
            x = ws[m][sec == i]
            n = int(len(x))
            if n >= MIN_SECTOR_RECORDS:
                A, k = weibull_fit(x)
            elif n > 0:
                k = k_all
                A = float(x.mean()) / float(_gamma(1 + 1 / k))
            else:
                A, k = A_all, k_all
            sectors.append({"centre": i * width, "freq": n / total if total else 0.0, "A": float(A), "k": float(k),
                            "mean": float(x.mean()) if n else None, "records": n})
    sh = an.get("shear") or {}
    alpha, basis = extrapolation_shear(sh)
    odd = []
    fit = _num(sh.get("alpha_fit")) if len(sh.get("heights") or []) > 2 else None
    if alpha is not None and not SHEAR_RANGE[0] <= alpha <= SHEAR_RANGE[1]:
        odd.append(f"The measured shear exponent {alpha:.2f} ({basis}) is outside the usual range of "
                   f"{SHEAR_RANGE[0]:g} to {SHEAR_RANGE[1]:g}.")
    if alpha is not None and fit is not None and abs(alpha - fit) > SHEAR_SPREAD:
        odd.append(f"The shear exponent {alpha:.2f} of the {basis} is far from the {fit:.2f} fitted over all "
                   f"{len(sh['heights'])} heights: the wind does not rise with height the way a clean profile does.")
    shear_warning = (" ".join(odd) + " Check the sensor heights and mounting before relying on anything carried to "
                     "another height, or enter a shear exponent for the project.") if odd else ""
    interval = float(time_qc.get("interval_s") or 600.0)
    g = ws.groupby(ws.index.to_period("M"))
    monthly = {}
    for p, mean in g.mean().items():
        n_valid = int(g.count()[p])
        share = n_valid / (p.days_in_month * 86400.0 / interval)
        if n_valid:
            monthly[str(p)] = {"mean": float(mean), "share": round(float(min(share, 1.0)), 4)}
    mm, ncal = momm(v)
    ti = (sd / ws)[(ws >= 3.0) & sd.notna()]
    ti_tab = an.get("ti")
    rep15 = _num(ti_tab.loc[15, "rep_TI"]) if ti_tab is not None and 15 in getattr(ti_tab, "index", []) else None
    dens = an.get("density") or {}
    stats = an.get("height_stats")
    heights = [] if stats is None else [
        {"height_m": float(r["height_m"]), "mean_ws": _num(r["mean_ws"]), "momm_ws": _num(r["momm_ws"]),
         "recovery_pct": _num(r["recovery_%"]), "weibull_A": _num(r["weibull_A"]), "weibull_k": _num(r["weibull_k"])}
        for _, r in stats.iterrows()]
    site = site or {}
    expected = max(int(time_qc.get("expected") or 0), 1)
    return {
        "height_m": float(top), "n_sectors": n_sectors, "sectors": sectors, "no_direction": bool(no_direction),
        "mean_ws": float(v.mean()), "momm_ws": _num(mm), "calendar_months": int(ncal),
        "weibull_A": float(A_all), "weibull_k": float(k_all),
        "shear": {"alpha": _num(alpha), "basis": basis, "alpha_all_heights": _num(sh.get("alpha_fit")),
                  "warning": shear_warning},
        "ti": {"mean_above_3": _num(ti.mean()) if len(ti) else None, "representative_15": rep15},
        "density": {"mean": _num(dens.get("mean")), "sensor_height_m": _num(dens.get("sensor_height"))},
        "monthly": monthly, "heights": heights,
        "period": [str(time_qc.get("start")), str(time_qc.get("end"))],
        "records": int(v.size), "recovery_pct": round(100.0 * v.size / expected, 2),
        "site": {"name": site.get("name") or "", "lat": _num(site.get("lat")), "lon": _num(site.get("lon")),
                 "elevation_m": _num(site.get("elevation_m"))},
        "source_format": source_format,
    }


def save(path, climate: dict) -> None:
    Path(path).write_text(json.dumps(climate, indent=1), encoding="utf-8")


def load(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sector_arrays(climate: dict) -> tuple:
    """centres, frequency (sums to 1), Weibull A and k per sector."""
    s = climate["sectors"]
    f = np.array([x["freq"] for x in s], dtype=float)
    f = f / f.sum()
    return (np.array([x["centre"] for x in s], dtype=float), f,
            np.array([x["A"] for x in s], dtype=float), np.array([x["k"] for x in s], dtype=float))


def weibull_mean(A, k):
    return np.asarray(A, dtype=float) * _gamma(1 + 1 / np.asarray(k, dtype=float))


def mean_speed(f, A, k) -> float:
    return float(np.sum(np.asarray(f) * weibull_mean(A, k)))


def speed_scale(climate: dict, longterm: dict | None = None) -> dict:
    """What every sector's Weibull scale is multiplied by so that the climate's mean is the best annual mean at
    the measurement height: {"scale", "mean_ws", "records_mean_ws", "basis"}.

    The sector distributions are fitted to every valid record. A campaign that covers some seasons twice, or lost
    part of a month, leans towards those seasons, so the plain mean of its records is not the annual mean: on a
    real record it sat several per cent below the mean of monthly means, and energy moves faster than wind
    speed. The scale puts
    the mean where the monthly figures say it is and keeps the shape of every sector: the long-term mean when there
    is a long-term correction (longterm: the result of bohemian_wind.longterm.correct), else the mean of monthly
    means when all twelve calendar months were measured, else the records as they are."""
    _, f, A, k = sector_arrays(climate)
    base = mean_speed(f, A, k)
    h = climate["height_m"]
    lt = longterm or {}
    if lt.get("long_term"):
        target = float(lt["long_term"])
        short = f"long-term mean {target:.2f} m/s at {h:g} m"
        basis = (f"{short} (measured {lt['measured']:.2f} m/s x long-term factor {lt['factor']:.3f})")
    elif climate.get("calendar_months") == 12 and climate.get("momm_ws"):
        target = float(climate["momm_ws"])
        short = f"measured mean of monthly means {target:.2f} m/s at {h:g} m, no long-term correction"
        basis = (f"no long-term correction applied: the measured mean of monthly means, {target:.2f} m/s at {h:g} m, "
                 "is taken as the annual mean")
    else:
        target = base
        short = "measured period as it is, no long-term correction"
        basis = ("no long-term correction applied and less than a full year measured: the records are taken as they "
                 "are, so the seasons not measured are missing from the result")
    scale = float(target / base)
    lean = ""
    if abs(scale / float(lt.get("factor") or 1.0) - 1.0) > 0.01:
        monthly = float(lt["measured"]) if lt.get("long_term") else target
        lean = (f"The plain mean of all records is {base:.2f} m/s at {h:g} m; the monthly figures give {monthly:.2f} m/s, "
                f"because some months hold more records than others. Every direction's wind is scaled by {scale:.3f} so "
                "that the mean is the annual figure; the shape of the distribution is kept.")
    return {"scale": scale, "mean_ws": float(target), "records_mean_ws": float(base), "basis": basis, "short": short,
            "lean": lean}


def at_height(climate: dict, height_m: float, alpha: float | None = None, factor: float = 1.0) -> tuple:
    """Sector arrays carried from the measurement height to height_m with the power law (the shape factor k is
    kept), and scaled by `factor` (see speed_scale)."""
    centres, f, A, k = sector_arrays(climate)
    a = climate["shear"]["alpha"] if alpha is None else alpha
    if a is None:
        if abs(height_m - climate["height_m"]) > 1e-6:
            raise ValueError("no shear exponent: give one to carry the wind to another height")
        a = 0.0
    return centres, f, A * factor * (height_m / climate["height_m"]) ** a, k


def expand_degrees(centres, f, A, k, rotate_deg: float = 0.0) -> tuple:
    """Sector climate as 360 one-degree bins: each degree carries its sector's A and k and an equal share of its
    frequency. Keeping sectors whole matters: blending neighbours smears a peaked wind rose and loses energy.
    rotate_deg turns the whole rose clockwise (a vane north correction)."""
    n = len(centres)
    width = 360.0 / n
    deg = np.arange(360)
    sec = (np.floor(((deg - centres[0] + width / 2) % 360) / width)).astype(int)
    r = int(round(rotate_deg)) % 360
    return deg.astype(float), np.roll(f[sec] / width, r), np.roll(A[sec], r), np.roll(k[sec], r)


def energy_direction(centres, f, A, k) -> float:
    """Direction the energy comes from: frequency x mean cube of the speed, as a vector mean. Used to orient
    turbine spacing."""
    cube = np.asarray(A) ** 3 * _gamma(1 + 3 / np.asarray(k))
    w = np.asarray(f) * cube
    t = np.deg2rad(centres)
    return float(np.rad2deg(np.arctan2((w * np.sin(t)).sum(), (w * np.cos(t)).sum())) % 360.0)


def monthly_series(climate: dict, min_share: float = MIN_MONTH_SHARE) -> pd.Series:
    """Monthly means of months with enough valid data, as a period-indexed series."""
    m = {pd.Period(p, "M"): v["mean"] for p, v in climate["monthly"].items() if v["share"] >= min_share}
    return pd.Series(m, dtype=float).sort_index()
