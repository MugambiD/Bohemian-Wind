"""The demo project: an invented site so every step can be tried without any data or internet connection.

Everything in it is made up: the mast records (random numbers shaped like wind, with five faults planted for the
checks to find), the terrain (a ridge drawn from a formula), the long-term reference, the homes and the exclusion
zones. The coordinates are arbitrary. Nothing comes from a real project.
"""
from __future__ import annotations

import json
import math
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from . import terrain
from .project import Project, ProjectError

NAME = "Demo Ridge (invented data)"
LAT, LON, ELEV = -5.0, 35.0, 1640.0
STATION = "DEMO01"
SEED = 20261118
PLANTED = ["Three days with no logger records", "The 58 m vane fixed at one reading for 30 hours",
           "The 40 m cup dead for 20 hours", "One gust record of 1,500 m/s at 80 m",
           "One standard deviation of 61 m/s at 60 m"]


def ground(x, y):
    """Invented terrain, metres above sea level: a north-south ridge through the mast, a lower ridge to the west
    and rolling ground. x east, y north, metres from the mast."""
    crest = 140.0 * np.exp(-(x / 950.0) ** 2) * (0.78 + 0.22 * np.cos(y / 2600.0))
    west = 75.0 * np.exp(-((x + 4300.0) / 1300.0) ** 2) * (0.8 + 0.2 * np.sin(y / 3100.0))
    rolling = 22.0 * np.sin(x / 1700.0 + 0.6) * np.cos(y / 2300.0) + 9.0 * np.sin((x + y) / 900.0)
    return 1500.0 + crest + west + rolling + 0.004 * x


def _ar1(rng, n: int, rho: float) -> np.ndarray:
    e = rng.standard_normal(n) * math.sqrt(1 - rho * rho)
    z = np.empty(n)
    z[0] = rng.standard_normal()
    for i in range(1, n):
        z[i] = rho * z[i - 1] + e[i]
    return z


def mast_frame(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Ten-minute records in Campbell column names (V = cup, D = vane, TA, RH, PA), faults included."""
    rng = np.random.default_rng(SEED)
    idx = pd.date_range(start, end, freq="10min", inclusive="left")
    n = len(idx)
    hour = idx.hour.to_numpy() + idx.minute.to_numpy() / 60.0
    doy = idx.dayofyear.to_numpy()
    season = 1.0 + 0.16 * np.cos(2 * np.pi * (doy - 200) / 365.25)
    diurnal = 1.0 + 0.09 * np.cos(2 * np.pi * (hour - 16) / 24.0)
    z = _ar1(rng, n, 0.985)
    p = np.clip(0.5 * (1 + np.vectorize(math.erf)(z / math.sqrt(2))), 1e-6, 1 - 1e-6)
    k = 2.3
    A = 8.55 * season * diurnal
    u80 = A * (-np.log(1 - p)) ** (1 / k)
    regime = _ar1(rng, n, 0.9993)
    wd = 92.0 + 32.0 * np.sin(2 * np.pi * (doy - 60) / 365.25) + 24.0 * _ar1(rng, n, 0.992)
    wd = np.where(regime > 1.45, wd + 165.0, wd) % 360.0
    u80 = np.where(regime > 1.45, u80 * 0.72, u80)                      # the rarer westerlies are weaker
    alpha = 0.15 + 0.06 * np.cos(2 * np.pi * (hour - 3) / 24.0)
    ti = (0.075 + 0.42 / np.maximum(u80, 1.5)) * np.exp(0.15 * rng.standard_normal(n))
    df = pd.DataFrame(index=idx)

    def cup(name: str, h: float, wobble: float):
        u = u80 * (h / 80.0) ** alpha * (1 + wobble * rng.standard_normal(n))
        sd = u * ti * (80.0 / h) ** 0.12
        df[f"{name}_{h:g}m_Avg"] = np.round(u, 3)
        df[f"{name}_{h:g}m_Std"] = np.round(sd, 3)
        df[f"{name}_{h:g}m_Min"] = np.round(np.maximum(u - 2.2 * sd, 0.0), 2)
        df[f"{name}_{h:g}m_Max"] = np.round(u + 2.4 * sd, 2)

    cup("V1", 80, 0.003)
    cup("V2", 80, 0.004)
    cup("V3", 60, 0.006)
    cup("V4", 40, 0.008)
    for name, h, off in (("D1", 78, 0.0), ("D2", 58, 2.0)):
        df[f"{name}_{h}m_Avg"] = np.round((wd + off + 1.5 * rng.standard_normal(n)) % 360.0, 1)
        df[f"{name}_{h}m_Std"] = np.round(np.clip(4.0 + 22.0 / np.maximum(u80, 1.0) + rng.standard_normal(n), 0.5, 60), 1)
    ta = 19.5 + 4.5 * np.cos(2 * np.pi * (hour - 14.5) / 24.0) + 2.2 * np.cos(2 * np.pi * (doy - 20) / 365.25) + 0.8 * _ar1(rng, n, 0.98)
    df["TA_5m_Avg"] = np.round(ta, 2)
    df["RH_5m_Avg"] = np.round(np.clip(62.0 - 2.4 * (ta - 19.5) + 6.0 * _ar1(rng, n, 0.99), 12, 100), 1)
    df["PA_5m_Avg"] = np.round(833.5 + 1.6 * _ar1(rng, n, 0.998) + 0.9 * np.cos(2 * np.pi * (hour - 10) / 12.0), 2)
    df["U_Batt_Avg"] = np.round(13.1 + 0.5 * np.cos(2 * np.pi * (hour - 13) / 24.0), 2)

    # planted faults, placed relative to the start so they exist whatever the dates
    def at(days: float) -> pd.Timestamp:
        return (start + pd.Timedelta(days=days)).floor("10min")
    gap = (df.index >= at(71)) & (df.index < at(74))
    df = df[~gap]
    stuck = (df.index >= at(128)) & (df.index < at(128) + pd.Timedelta(hours=30))
    df.loc[stuck, "D2_58m_Avg"] = 217.4
    df.loc[stuck, "D2_58m_Std"] = 0.0
    dead = (df.index >= at(203)) & (df.index < at(203) + pd.Timedelta(hours=20))
    df.loc[dead, ["V4_40m_Avg", "V4_40m_Min", "V4_40m_Max"]] = 0.3
    df.loc[dead, "V4_40m_Std"] = 0.0
    df.loc[df.index == at(260.5), "V1_80m_Max"] = 1500.0
    df.loc[df.index == at(301.25), "V3_60m_Std"] = 61.0
    return df


def write_toa5(df: pd.DataFrame, folder: Path) -> list:
    """One file a month, in the layout a Campbell logger writes."""
    folder.mkdir(parents=True, exist_ok=True)
    cols = list(df.columns)
    units = []
    for c in cols:
        units.append("m/s" if c.startswith("V") else "Deg" if c.startswith("D") else "degC" if c.startswith("TA")
                     else "%" if c.startswith("RH") else "hPa" if c.startswith("PA") else "V")
    kinds = [c.rsplit("_", 1)[-1] for c in cols]
    head = ('"TOA5","{s}","CR1000X","00000","CR1000X.Std.05.02","CPU:demo_mast.CR1X","00000","DATA_10MIN"\n'
            '"TIMESTAMP","RECORD",{names}\n"TS","RN",{units}\n"","",{kinds}\n').format(
        s=STATION, names=",".join(f'"{c}"' for c in cols), units=",".join(f'"{u}"' for u in units),
        kinds=",".join(f'"{k}"' for k in kinds))
    out, rec = [], 0
    for period, part in df.groupby(df.index.to_period("M")):
        body = part.copy()
        body.insert(0, "RECORD", np.arange(rec, rec + len(body)))
        rec += len(body)
        stamps = part.index.strftime('"%Y-%m-%d %H:%M:%S"')
        rows = body.to_csv(header=False, index=False, lineterminator="\n").splitlines()
        f = folder / f"{STATION}_DATA_10MIN_{period}.dat"
        f.write_text(head + "\n".join(f"{t},{r}" for t, r in zip(stamps, rows)) + "\n", encoding="utf-8")
        out.append(f)
    return out


def reference(df: pd.DataFrame, today: date) -> tuple:
    """Monthly and daily reference in the layout of NASA POWER point files (wind speed at 50 m). Invented: follows
    the mast's months, with the measured period about 3 % windier than the long-term average."""
    rng = np.random.default_rng(SEED + 1)
    mast = df["V1_80m_Avg"].groupby(df.index.to_period("M")).mean()
    cal = mast.groupby(mast.index.month).mean()
    months = pd.period_range("2001-01", f"{today.year - 1}-12", freq="M")
    year_dev = {y: 0.035 * rng.standard_normal() for y in range(2001, today.year + 1)}
    vals = {}
    for p in pd.period_range("2001-01", f"{today.year}-12", freq="M"):
        base = float(cal.get(p.month, mast.mean())) * 0.80 / 1.03
        v = base * (1 + year_dev[p.year]) * (1 + 0.03 * rng.standard_normal())
        if p in mast.index:                       # concurrent months track the mast
            v = float(mast[p]) * 0.80 * (1 + 0.025 * rng.standard_normal())
        vals[p] = v
    monthly = {f"{p.year}{p.month:02d}": round(vals[p], 2) for p in months}
    for y in range(2001, today.year):
        monthly[f"{y}13"] = round(float(np.mean([vals[p] for p in months if p.year == y])), 2)
    daily = {}
    d = date(today.year, 1, 1)
    last = today - timedelta(days=4)
    while d <= last:
        daily[f"{d:%Y%m%d}"] = round(vals[pd.Period(f"{d.year}-{d.month:02d}", "M")] * (1 + 0.22 * rng.standard_normal()), 2)
        d += timedelta(days=1)
    # keep each complete month's mean equal to its monthly value
    s = pd.Series(daily)
    key = s.index.str[:6]
    for m, grp in s.groupby(key):
        p = pd.Period(f"{m[:4]}-{m[4:]}", "M")
        scale = vals[p] / grp.mean()
        for k in grp.index:
            daily[k] = round(float(s[k] * scale), 2)

    def wrap(values: dict) -> dict:
        return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [LON, LAT, ELEV]},
                "header": {"title": "Invented reference for the Bohemian Wind demo, in the layout of a NASA POWER file"},
                "properties": {"parameter": {"WS50M": values}}}
    return wrap(monthly), wrap(daily)


def constraints_files(grid: terrain.Grid) -> dict:
    """Homes, exclusion zones and a boundary as GeoJSON texts. All invented."""
    rng = np.random.default_rng(SEED + 2)

    def ll(x, y):
        lat, lon = grid.to_latlon(np.asarray(x, dtype=float), np.asarray(y, dtype=float))
        return [[round(float(b), 6), round(float(a), 6)] for a, b in zip(np.atleast_1d(lat), np.atleast_1d(lon))]

    homes = []
    spots = [(640, 350), (-590, -650), (820, 1900), (1750, -3900), (1500, -1300), (1350, 900), (1900, 2700),
             (-1500, -2800), (-1400, 300), (-1700, 3400), (2300, -200), (-2200, 1700), (1250, 4300)]
    for x, y in spots:                                    # homesteads on and near the ridge
        homes.append((x + 25 * rng.standard_normal(), y + 25 * rng.standard_normal()))
    for cx, cy, m in ((3600, -1500, 9), (3900, 2600, 7), (-3300, -4200, 6), (-5600, 1500, 5)):   # villages in the valleys
        for _ in range(m):
            homes.append((cx + 420 * rng.standard_normal(), cy + 420 * rng.standard_normal()))
    feats = [{"type": "Feature", "properties": {"name": f"H{i + 1:02d}"},
              "geometry": {"type": "Point", "coordinates": ll(x, y)[0]}} for i, (x, y) in enumerate(homes)]
    receptors = {"type": "FeatureCollection", "name": "Homes (invented)", "features": feats}

    def poly(name, pts):
        ring = ll([p[0] for p in pts], [p[1] for p in pts])
        return {"type": "Feature", "properties": {"name": name}, "geometry": {"type": "Polygon", "coordinates": [ring + ring[:1]]}}

    def line(name, pts):
        return {"type": "Feature", "properties": {"name": name},
                "geometry": {"type": "LineString", "coordinates": ll([p[0] for p in pts], [p[1] for p in pts])}}

    exclusions = {"type": "FeatureCollection", "name": "Exclusion zones (invented)", "features": [
        poly("Forest reserve (invented)", [(-700, 4700), (900, 4500), (1300, 6100), (200, 6900), (-900, 6200)]),
        poly("Wetland (invented)", [(2500, 600), (3300, 300), (3500, 1300), (2700, 1600)]),
        poly("Shrine and burial ground (invented)", [(-250, -3050), (150, -3100), (200, -2750), (-200, -2700)]),
        line("Road (invented)", [(-7800, -1900), (-2500, -2150), (0, -1850), (2600, -2300), (7800, -2000)]),
        line("Power line (invented)", [(-2700, -7800), (-2550, -2000), (-2900, 3000), (-2600, 7800)])]}
    boundary = {"type": "FeatureCollection", "name": "Site boundary (invented)", "features": [
        poly("Land under option (invented)", [(-3400, -6600), (3300, -6800), (3600, 6500), (-3200, 6900)])]}
    return {"receptors.geojson": receptors, "exclusions.geojson": exclusions, "boundary.geojson": boundary}


def create(folder, today: date | None = None, months: int = 14, log=lambda m: None) -> Project:
    """Build the demo project in folder (which must not hold a project yet). Works offline."""
    folder = Path(folder)
    today = today or date.today()
    end = pd.Timestamp(today.year, today.month, 1)
    start = end - pd.DateOffset(months=months)
    project = Project.create(folder, NAME, LAT, LON, ELEV)
    log("Writing invented mast records ...")
    df = mast_frame(start, end)
    files = write_toa5(df, folder / "data")
    log(f"  {len(df):,} ten-minute records in {len(files)} monthly files, {start:%b %Y} to {(end - pd.Timedelta(days=1)):%b %Y}.")
    grid = terrain.from_function(ground, LAT, LON, half_km=10.0, res=100.0,
                                 source="invented terrain for the demo (a formula, not a survey)")
    grid.save(project.path("terrain", "grid.npz"))
    monthly, daily = reference(df, today)
    project.path("longterm", "demo_reference_monthly.json").write_text(json.dumps(monthly), encoding="utf-8")
    project.path("longterm", "demo_reference_daily.json").write_text(json.dumps(daily), encoding="utf-8")
    for name, obj in constraints_files(grid).items():
        project.path("constraints", name).write_text(json.dumps(obj, indent=1), encoding="utf-8")
    project.path("README.txt").write_text(
        "Bohemian Wind demo project\n\nEverything here is invented so the software can be tried without data or internet:\n"
        "the mast records, the terrain, the long-term reference, the homes and the exclusion zones. The coordinates are\n"
        "arbitrary. Nothing comes from a real project, and no result means anything about a real site.\n\n"
        "Faults planted in the mast records for the checks to find:\n" + "".join(f"  - {p}\n" for p in PLANTED), encoding="utf-8")
    s = project.s
    s["demo"] = True
    s["data"]["folder"] = "data"
    s["longterm"] = {"source": "file", "monthly_file": "longterm/demo_reference_monthly.json",
                     "daily_file": "longterm/demo_reference_daily.json"}
    s["map"].update(engine="linear", height_m=120.0, half_km=8.0, res_m=100.0)
    s["layout"].update(n_turbines=10, exclusions_file="constraints/exclusions.geojson",
                       receptors_file="constraints/receptors.geojson", boundary_file="constraints/boundary.geojson")
    s["layout"]["rules"].update(radius_km=7.0)
    s["environment"]["year"] = today.year + 1
    project.save()
    return project


def open_or_create(folder, log=lambda m: None) -> Project:
    folder = Path(folder)
    try:
        return Project.open(folder)
    except ProjectError:
        return create(folder, log=log)
