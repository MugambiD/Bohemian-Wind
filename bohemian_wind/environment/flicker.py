"""Shadow flicker at homes: the astronomical worst case.

Assumptions (all on the high side): the sun always shines, the rotor always turns and always faces the sun, and
nothing stands between the turbine and the home (no hills, trees or walls). For every time step of a year and
every turbine-home pair within range, the home is in flicker when the line from the home to the sun passes
through the rotor. With the rotor facing the sun this is: the sun lies within the rotor's apparent radius of the
hub as seen from the home. Results are hours a year and the longest total in one day, per home.

[COMPLIANCE] A screening. Real flicker is a fraction of the worst case (cloud, wind direction, standstill,
screening). A permit application needs the study the country's rules ask for.
"""
from __future__ import annotations

import numpy as np

from ..terrain import Grid
from . import solar

EYE_HEIGHT = 2.0
MIN_SUN_ELEVATION = 3.0
MAX_DISTANCE_ROTORS = 10.0


def year_times(year: int, step_min: int = 1, days=None) -> np.ndarray:
    """UTC time steps of a year (or of the given day numbers, 0 = 1 January)."""
    start = np.datetime64(f"{year}-01-01T00:00:00")
    n_days = int((np.datetime64(f"{year + 1}-01-01") - np.datetime64(f"{year}-01-01")) / np.timedelta64(1, "D"))
    day_list = np.arange(n_days) if days is None else np.asarray(days, dtype=int)
    per_day = 1440 // step_min
    offs = (day_list[:, None] * 1440 + np.arange(per_day)[None, :] * step_min).ravel()
    return start + offs.astype("timedelta64[m]")


def at_receptors(grid: Grid, turbines: list, receptors: list, hub_m: float, rotor_m: float, year: int,
                 step_min: int = 1, limit_hours_year: float = 30.0, limit_min_day: float = 30.0,
                 utc_offset_h: float | None = None, days=None, min_sun_elevation: float = MIN_SUN_ELEVATION,
                 max_distance_rotors: float = MAX_DISTANCE_ROTORS, eye_height: float = EYE_HEIGHT) -> dict:
    """turbines, receptors: dicts with x, y in local metres. utc_offset_h: local time minus UTC, for counting by
    local day (default: from the longitude)."""
    R = rotor_m / 2.0
    lat0, lon0 = grid.lat0, grid.lon0
    off = round(lon0 / 15.0) if utc_offset_h is None else utc_offset_h
    t = year_times(year, step_min, days)
    elev, az = solar.position(t, lat0, lon0)
    up = elev >= min_sun_elevation
    t, sun = t[up], solar.unit_vector(elev[up], az[up])
    local_day = ((t + np.timedelta64(int(round(off * 60)), "m")).astype("datetime64[D]")).astype("int64")
    day_ids, day_index = np.unique(local_day, return_inverse=True)

    tx = np.array([q["x"] for q in turbines], dtype=float)
    ty = np.array([q["y"] for q in turbines], dtype=float)
    tz = grid.sample(tx, ty) + hub_m
    by_turbine = np.zeros((len(turbines), len(t)), bool)        # steps when a turbine shades any receptor
    rows = []
    for rc in receptors:
        ez = float(grid.sample(rc["x"], rc["y"])) + eye_height
        v = np.column_stack([tx - rc["x"], ty - rc["y"], tz - ez])
        dist = np.linalg.norm(v, axis=1)
        in_range = np.flatnonzero((dist <= max_distance_rotors * rotor_m) & (dist > R))
        if not len(in_range):           # most homes of a large survey: no turbine near enough, nothing to work out
            rows.append({"name": rc["name"], "hours_per_year": 0.0, "max_minutes_per_day": 0.0, "days_with_flicker": 0,
                         "over_hours_limit": False, "over_day_limit": False, "over_limit": False, "turbines": [],
                         "nearest_turbine_m": round(float(np.hypot(tx - rc["x"], ty - rc["y"]).min()), 0) if len(tx) else None,
                         "lat": rc.get("lat"), "lon": rc.get("lon"), "x": rc["x"], "y": rc["y"]})
            continue
        shaded = np.zeros(len(t), bool)
        causes = []
        for j in in_range:
            cos_lim = np.sqrt(1.0 - (R / dist[j]) ** 2)
            hit = sun @ (v[j] / dist[j]) >= cos_lim
            if hit.any():
                shaded |= hit
                by_turbine[j] |= hit
                causes.append((turbines[j].get("id", str(j + 1)), float(hit.sum() * step_min / 60.0)))
        per_day = np.bincount(day_index, weights=shaded.astype(float), minlength=len(day_ids)) * step_min
        hours = float(shaded.sum() * step_min / 60.0)
        worst_day = float(per_day.max()) if len(per_day) else 0.0
        causes.sort(key=lambda c: -c[1])
        rows.append({"name": rc["name"], "hours_per_year": round(hours, 1), "max_minutes_per_day": round(worst_day, 0),
                     "days_with_flicker": int((per_day > 0).sum()),
                     "over_hours_limit": bool(hours > limit_hours_year), "over_day_limit": bool(worst_day > limit_min_day),
                     "over_limit": bool(hours > limit_hours_year or worst_day > limit_min_day),
                     "turbines": [c[0] for c in causes[:4]],
                     "nearest_turbine_m": round(float(np.hypot(tx - rc["x"], ty - rc["y"]).min()), 0),
                     "lat": rc.get("lat"), "lon": rc.get("lon"), "x": rc["x"], "y": rc["y"]})
    rows.sort(key=lambda r: -r["hours_per_year"])
    total_steps = float(len(year_times(year, step_min, days)))
    shutdown = float(by_turbine.sum() / (len(turbines) * total_steps)) if len(turbines) else 0.0
    return {"receptors": rows, "year": year, "step_min": step_min, "limit_hours_year": limit_hours_year,
            "limit_min_day": limit_min_day, "n_receptors": len(rows),
            "with_any_flicker": sum(r["hours_per_year"] > 0 for r in rows),
            "over_hours_limit": sum(r["over_hours_limit"] for r in rows),
            "over_day_limit": sum(r["over_day_limit"] for r in rows),
            "over_limit": sum(r["over_limit"] for r in rows),
            "highest_hours": rows[0]["hours_per_year"] if rows else None,
            "highest_minutes_day": max((r["max_minutes_per_day"] for r in rows), default=None),
            "shutdown_upper_bound_pct": round(100.0 * shutdown, 3),
            "per_turbine_hours": {turbines[j].get("id", str(j + 1)): round(float(by_turbine[j].sum() * step_min / 60.0), 1)
                                  for j in range(len(turbines))},
            "method": f"astronomical worst case; sun at least {min_sun_elevation:g} deg above the horizon; within "
                      f"{max_distance_rotors:g} rotor diameters; eye height {eye_height:g} m; local time UTC{off:+g} h"}
