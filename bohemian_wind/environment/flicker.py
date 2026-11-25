"""Shadow flicker at homes: the astronomical worst case.

Assumptions (all on the high side): the sun always shines, the rotor always turns and always faces the sun, and
nothing stands between the turbine and the home (no hills, trees or walls). For every time step of a year and
every turbine-home pair within range, the home is in flicker when the line from the home to the sun passes
through the rotor. With the rotor facing the sun this is: the sun lies within the rotor's apparent radius of the
hub as seen from the home. Results are hours a year and the longest total in one day, per home.

The expected case (optional) sets three things against the worst case, each from figures the project has:
  the sun     the share of daylight with direct sun, month by month, as entered;
  the rotor   it faces the wind, not the sun: for each sector of the wind rose the rotor disc stands across that
              sector's direction, and a home is in flicker only when the line to the sun passes through that disc;
  running     the share of the time the turbine turns in that sector (wind between cut-in and cut-out).
The three are taken as independent of one another and of the time of day, which is the usual statistical
treatment; it gives expected hours a year, not the hours of any one year, and no figure for a single day.

A budget (optional) is the expected hours a year a home may have. Where a home is over it, the turbines that
shade it are stopped for the share of their shading time that brings the home down to the budget; the energy
this costs is reported.

[COMPLIANCE] A screening. Hills, trees and walls are still ignored. A permit application needs the study the
country's rules ask for.
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


def _expected_inputs(e: dict, n_turbines: int) -> dict:
    centres = np.asarray(e["centres"], dtype=float)
    n = len(centres)
    if not 1 <= n <= 32:
        raise ValueError("The expected case of shadow flicker takes a wind rose of 1 to 32 sectors.")
    freq = np.asarray(e["freq"], dtype=float)
    freq = freq / freq.sum()
    run = np.asarray(e.get("run", 1.0), dtype=float)
    run = np.clip(np.broadcast_to(run if run.ndim == 2 else np.atleast_1d(run)[None, :], (n_turbines, n)), 0.0, 1.0)
    sun = e.get("sunshine")
    entered = sun is not None
    sun = np.clip(np.broadcast_to(np.atleast_1d(np.asarray(1.0 if sun is None else sun, dtype=float)), (12,)), 0.0, 1.0)
    rad = np.radians(centres)                                    # the rotor's axis lies along the wind: east, north, up
    normals = np.column_stack([np.sin(rad), np.cos(rad), np.zeros(n)])
    return {"n": n, "freq": freq, "run": np.array(run), "sunshine": np.array(sun), "entered": entered, "normals": normals}


def _through_disc(sun: np.ndarray, to_hub: np.ndarray, normals: np.ndarray, radius: float) -> np.ndarray:
    """[step, sector]: does the line from the home towards the sun pass through the rotor disc that stands across
    the wind of that sector? to_hub: from the eye to the hub. The disc's plane holds the hub; the line meets it at
    eye + lam * sun, and counts when that point is within the radius of the hub."""
    sn = sun @ normals.T                                         # [step, sector]
    hn = to_hub @ normals.T                                      # [sector]
    with np.errstate(divide="ignore", invalid="ignore"):
        lam = hn[None, :] / sn
        off = lam[:, :, None] * sun[:, None, :] - to_hub[None, None, :]
        ok = (np.abs(sn) > 1e-9) & (lam > 0) & (np.einsum("ijk,ijk->ij", off, off) <= radius * radius)
    return ok


def at_receptors(grid: Grid, turbines: list, receptors: list, hub_m: float, rotor_m: float, year: int,
                 step_min: int = 1, limit_hours_year: float = 30.0, limit_min_day: float = 30.0,
                 utc_offset_h: float | None = None, days=None, min_sun_elevation: float = MIN_SUN_ELEVATION,
                 max_distance_rotors: float = MAX_DISTANCE_ROTORS, eye_height: float = EYE_HEIGHT,
                 expected: dict | None = None, budget_hours: float | None = None) -> dict:
    """turbines, receptors: dicts with x, y in local metres. utc_offset_h: where the days are split, as hours ahead
    of UTC (default: the zone of the site's longitude, which keeps midnight in the dark; it is not the civil time of
    the country and changes no figure unless the sun is up at that midnight). The verdicts against the two limits go
    by the hours and minutes as shown in the tables.

    expected: the figures for the expected case: {"centres": sector centres in degrees the wind comes from,
    "freq": their frequencies, "run": the share of time running, per sector or [turbine, sector], "sunshine": the
    share of daylight with direct sun, one value or twelve (January first)}. budget_hours: see the module text."""
    R = rotor_m / 2.0
    ex = _expected_inputs(expected, len(turbines)) if expected else None
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
    if ex:
        month = (t.astype("datetime64[M]").astype("int64") % 12)
        sun_share = ex["sunshine"][month]                                    # per step
        bits = np.uint32(1) << np.arange(ex["n"], dtype=np.uint32)
        seen = np.zeros((len(turbines), len(t)), np.uint32)                  # per step: the sectors in which a turbine shades a home
        seen_over = np.zeros((len(turbines), len(t)), np.uint32)             # the same, for homes over the budget only
        stop_share = np.zeros(len(turbines))
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
        mine = []                                                # expected case: (turbine, steps, [step, sector] hit)
        for j in in_range:
            cos_lim = np.sqrt(1.0 - (R / dist[j]) ** 2)
            hit = sun @ (v[j] / dist[j]) >= cos_lim
            if hit.any():
                shaded |= hit
                by_turbine[j] |= hit
                causes.append((turbines[j].get("id", str(j + 1)), float(hit.sum() * step_min / 60.0)))
                if ex:
                    idx = np.flatnonzero(hit)
                    mine.append((j, idx, _through_disc(sun[idx], v[j], ex["normals"], R)))
        per_day = np.bincount(day_index, weights=shaded.astype(float), minlength=len(day_ids)) * step_min
        hours = round(float(shaded.sum() * step_min / 60.0), 1)           # as shown: a home listed at the limit is not over it
        worst_day = round(float(per_day.max()), 0) if len(per_day) else 0.0
        causes.sort(key=lambda c: -c[1])
        more = {}
        if ex:
            steps = np.flatnonzero(shaded)                       # only the steps of the worst case can count
            chance = np.zeros((len(steps), ex["n"]))             # per step and sector: the chance a running rotor shades the home
            for j, idx, disc in mine:
                k = np.searchsorted(steps, idx)
                chance[k] = np.maximum(chance[k], disc * ex["run"][j][None, :])
            exp_h = float((sun_share[steps] * (chance @ ex["freq"])).sum() * step_min / 60.0)
            over_b = budget_hours is not None and round(exp_h, 1) > budget_hours
            for j, idx, disc in mine:
                packed = (disc * bits[None, :]).sum(axis=1).astype(np.uint32)
                seen[j, idx] |= packed
                if over_b:
                    seen_over[j, idx] |= packed
                    stop_share[j] = max(stop_share[j], 1.0 - budget_hours / exp_h)
            more = {"expected_hours_per_year": round(exp_h, 1),
                    "over_budget": bool(over_b) if budget_hours is not None else None}
        rows.append({"name": rc["name"], "hours_per_year": hours, "max_minutes_per_day": worst_day, **more,
                     "days_with_flicker": int((per_day > 0).sum()),
                     "over_hours_limit": bool(hours > limit_hours_year), "over_day_limit": bool(worst_day > limit_min_day),
                     "over_limit": bool(hours > limit_hours_year or worst_day > limit_min_day),
                     "turbines": [c[0] for c in causes[:4]],
                     "nearest_turbine_m": round(float(np.hypot(tx - rc["x"], ty - rc["y"]).min()), 0),
                     "lat": rc.get("lat"), "lon": rc.get("lon"), "x": rc["x"], "y": rc["y"]})
    rows.sort(key=lambda r: -r["hours_per_year"])
    total_steps = float(len(year_times(year, step_min, days)))
    shutdown = float(by_turbine.sum() / (len(turbines) * total_steps)) if len(turbines) else 0.0
    extra = {}
    if ex:
        for r in rows:
            r.setdefault("expected_hours_per_year", 0.0)
            r.setdefault("over_budget", False if budget_hours is not None else None)

        def turbine_hours(mask, a):                                          # expected hours a year each turbine shades a home
            on = ((mask[a:a + 8, :, None] & bits[None, None, :]) != 0)
            return (on * (ex["freq"] * ex["run"][a:a + 8])[:, None, :]).sum(axis=2) @ sun_share * step_min / 60.0

        if len(turbines):
            run_all = (ex["run"] @ ex["freq"])                               # share of the year each turbine runs
            any_h = np.concatenate([turbine_hours(seen, a) for a in range(0, len(turbines), 8)])
            over_h = np.concatenate([turbine_hours(seen_over, a) for a in range(0, len(turbines), 8)]) * stop_share
            hours_year = total_steps * step_min / 60.0
            running = np.maximum(run_all * hours_year, 1e-9)
            extra = {"expected": {
                "highest_hours": max((r["expected_hours_per_year"] for r in rows), default=None),
                "sunshine_share": [round(float(x), 3) for x in ex["sunshine"]], "sunshine_entered": ex["entered"],
                "running_share_pct": round(100.0 * float(run_all.mean()), 1),
                "stop_whenever_shading_pct": round(100.0 * float((any_h / running).mean()), 3),
                "per_turbine_hours": {turbines[j].get("id", str(j + 1)): round(float(any_h[j]), 1) for j in range(len(turbines))},
                "method": "expected case: the rotor stands across the wind of each sector of the wind rose; running between "
                          "cut-in and cut-out; " + ("direct sun for the share of daylight entered, month by month"
                                                    if ex["entered"] else "the sun taken to shine all day (no sunshine share entered)")
                          + "; sun, wind direction and running taken as independent"}}
            if budget_hours is not None:
                extra["budget"] = {
                    "hours": budget_hours, "homes_over": sum(bool(r["over_budget"]) for r in rows),
                    "shutdown_loss_pct": round(100.0 * float((over_h / running).mean()), 3),
                    "per_turbine_stop_hours": {turbines[j].get("id", str(j + 1)): round(float(over_h[j]), 1)
                                               for j in range(len(turbines)) if over_h[j] > 0},
                    "method": "each turbine that shades a home over the budget is stopped for the share of its shading "
                              "time that brings the worst such home down to the budget; the energy lost is taken at the "
                              "turbine's mean output while running"}
    return {"receptors": rows, "year": year, "step_min": step_min, "limit_hours_year": limit_hours_year, **extra,
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
                      f"{max_distance_rotors:g} rotor diameters; eye height {eye_height:g} m; days counted from "
                      f"midnight at UTC{off:+g} h" + ("" if utc_offset_h is not None else ", the zone of the site's longitude")}
