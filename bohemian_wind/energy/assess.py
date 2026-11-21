"""Layout design and energy yield: from the wind field and the buildable area to turbine positions, wake losses,
net energy and P50/P90.

Method
1. Free-stream energy of one turbine at every buildable cell: the mast's sector Weibulls (long-term, hub height)
   times the cell's ratio in each sector, times an air-density equivalent speed factor for the cell's elevation,
   through the generic power curve.
2. Placement: best cells first, with an elliptical spacing rule aligned with the direction the energy comes from
   and, optionally, every turbine within a link distance of another.
3. Wakes and annual energy with PyWake. Optionally, wake-aware fine-tuning: the most-waked turbines are tried
   on the best free cells and a move is kept only when the wind farm total improves.
4. Other losses (a table of assumptions) give the P50; an uncertainty budget gives P75 and P90.
5. Checks reported next to the result: two other wake models, and the energy if no turbine were windier than
   the mast (how much of the result rests on the flow model).

[COMPLIANCE] A developer estimate prepared with open-source tools. Not an accredited or bankable energy yield
assessment; must not be used for financing decisions.
"""
from __future__ import annotations

import time

import numpy as np

from .. import climate as _climate
from ..flow.fields import WindField
from . import EnergyError, budget, placement, wakes
from .turbine import RHO_SCALE_HEIGHT, Turbine, generic_model

S_GRID = np.arange(0.30, 2.0001, 0.002)
DV = 0.05


CEILING_CAUTION_PCT = 10.0   # more than this share of the P50 resting on mapped wind above the mast's is called out


def density_factor(elev, elev_mast: float):
    """Equivalent wind-speed factor for the change of air density with elevation: power below rated goes with
    density times speed cubed, so a density ratio r acts like a speed factor r^(1/3)."""
    return np.exp(-(np.asarray(elev, dtype=float) - elev_mast) / RHO_SCALE_HEIGHT / 3.0)


def sector_energy_tables(wt, f, A, k) -> np.ndarray:
    """[sector, speed factor]: MWh a year one turbine draws from each sector when that sector's wind is scaled by
    the factor (S_GRID)."""
    v = np.arange(DV / 2, 30.0, DV)
    p = np.asarray(wt.power(v), dtype=float) / 1e6
    out = np.zeros((len(f), len(S_GRID)))
    for i, (fi, ai, ki) in enumerate(zip(f, A, k)):
        aa = S_GRID[:, None] * ai
        pdf = (ki / aa) * (v[None, :] / aa) ** (ki - 1) * np.exp(-(v[None, :] / aa) ** ki)
        out[i] = fi * (pdf * p[None, :]).sum(axis=1) * DV * wakes.HOURS
    return out


def free_stream_energy(tables: np.ndarray, ratio_eff: np.ndarray) -> np.ndarray:
    """MWh a year per cell. ratio_eff[sector, cell]: wind ratio to the mast times the density factor."""
    out = np.zeros(ratio_eff.shape[1])
    for i in range(ratio_eff.shape[0]):
        out += np.interp(ratio_eff[i], S_GRID, tables[i])
    return out


def _complex_model_text(engine: str) -> str:
    """What the wind map's method can and cannot do over steep ground, for the complex-terrain caution."""
    if engine == "cfd":
        return ("The wind map comes from a CFD screening run, which resolves steep ground better than linear theory but has "
                "not yet been checked against a second mast on ground like this.")
    return ("The wind map is a linear screening model and is outside its range there; such models can misjudge the wind "
            "between points a few kilometres apart by far more than the screening rule allows.")


def _summary(gross, net_wake, rated_mw: float, other: float) -> dict:
    net = float(net_wake.sum() * other)
    return {"gross_gwh": float(gross.sum() / 1000), "wake_loss_pct": float(100 * (1 - net_wake.sum() / gross.sum())),
            "after_wake_gwh": float(net_wake.sum() / 1000), "other_loss_pct": float(100 * (1 - other)),
            "p50_gwh": net / 1000, "capacity_factor": net / (rated_mw * wakes.HOURS), "full_load_hours": net / rated_mw}


def design(field: WindField, clim: dict, mask: np.ndarray, turbine: Turbine, n_turbines: int, *,
           lt_factor: float = 1.0, alpha: float | None = None, ti: float | None = None, rho_hub: float = 1.225,
           cross_d: float = 3.0, along_d: float = 7.0, link_m: float | None = 2500.0, wake: str = "niayifar",
           refine: bool = True, refine_seconds: float = 90.0, losses=None, speed_uncertainty: dict | None = None,
           energy_uncertainty: dict | None = None, iav_speed: float = budget.DEFAULT_IAV_SPEED,
           fixed_xy=None, mast_xy=(0.0, 0.0), top_m: float | None = None, profile_clean: bool = True,
           long_term: dict | None = None, log=lambda m: None) -> dict:
    """field: wind field at the hub height (bohemian_wind.flow.fields.wind_field with the same climate, factor and
    shear). mask: buildable cells. fixed_xy: (x, y) arrays to evaluate a given layout instead of designing one.
    top_m: height of the top anemometer (default: the climate's height), for the vertical extrapolation term.
    profile_clean: False when the measured wind profile is not clean (climate's shear warning): the vertical term
    grows faster with height. long_term: {"pct", "corrected", "basis"} from budget.long_term_pct for the campaign
    (None: the default long-term terms)."""
    if abs(field.height_m - turbine.hub_m) > 0.5:
        raise EnergyError(f"The wind field is at {field.height_m:g} m but the hub height is {turbine.hub_m:g} m.")
    if wake not in wakes.WAKE_MODELS:
        raise EnergyError(f"Unknown wake model '{wake}'.")
    grid = field.grid
    _, f, A, k = _climate.at_height(clim, turbine.hub_m, alpha=alpha, factor=lt_factor)
    centres = field.centres
    ti = float(ti if ti else (clim.get("ti") or {}).get("mean_above_3") or 0.12)
    wt = generic_model(turbine, rho_hub, ti)
    tables = sector_energy_tables(wt, f, A, k)
    theta = _climate.energy_direction(centres, f, A, k)
    elev_mast = float(grid.sample(*mast_xy))
    dens = density_factor(grid.elev, elev_mast)
    other = budget.loss_factor(losses)
    notes = list(field.notes)

    exact = fixed_xy is not None

    def at(values, x, y) -> np.ndarray:
        """values[row, col] at the points: read between the cells for positions that were given, on the cell for
        positions the design put there (those sit on cell centres)."""
        if exact:
            return np.asarray(grid.sample(x, y, values), dtype=float)
        r, c = grid.index(x, y)
        return values[r, c]

    def ratios_at(x, y) -> np.ndarray:           # [turbine, sector], density included
        return np.stack([at(field.ratio[i], x, y) for i in range(len(centres))], axis=1) * at(dens, x, y)[:, None]

    def run(x, y, model=wake, ratio=None) -> tuple:
        site = wakes.site_for(ratios_at(x, y) if ratio is None else ratio, centres, f, A, k, ti)
        return wakes.simulate(site, wt, x, y, model)

    refinement = {"moves": [], "gain_pct": 0.0, "seconds": 0.0}
    if fixed_xy is not None:
        x, y = np.asarray(fixed_xy[0], dtype=float), np.asarray(fixed_xy[1], dtype=float)
        if not grid.inside(x, y).all():
            raise EnergyError("Some of the given turbine positions lie outside the terrain grid.")
        n_requested = len(x)
    else:
        n_requested = int(n_turbines)
        rows, cols = np.nonzero(mask)
        if len(rows) == 0:
            raise EnergyError("No buildable cells: the constraints remove the whole search area.")
        cx, cy = grid.x[cols], grid.y[rows]
        value = free_stream_energy(tables, field.ratio[:, rows, cols] * dens[rows, cols][None, :])
        idx = placement.greedy(cx, cy, value, turbine.rotor_m, n_requested, theta, cross_d, along_d, link_m)
        if len(idx) < n_requested:
            notes.append(f"Only {len(idx)} of the {n_requested} turbines asked for fit in the buildable area with this "
                         "spacing" + (f" and a {link_m:g} m link distance." if link_m else "."))
        if len(idx) == 0:
            raise EnergyError("No turbine could be placed.")
        log(f"Placed {len(idx)} turbines (best cells first, {cross_d:g} x {along_d:g} rotor diameters spacing, "
            f"energy from {theta:.0f} deg).")
        if refine and len(idx) > 1:
            t0 = time.time()
            idx, refinement = _refine(cx, cy, value, idx, turbine.rotor_m, theta, cross_d, along_d, link_m, run,
                                      deadline=t0 + refine_seconds, log=log)
            refinement["seconds"] = round(time.time() - t0, 1)
        order = np.lexsort((cx[idx], -cy[idx]))          # north to south, then west to east
        idx = idx[order]
        x, y = cx[idx], cy[idx]

    n = len(x)
    rated_mw = turbine.rated_kw / 1000.0 * n
    gross, net = run(x, y)
    res = _summary(gross, net, rated_mw, other)
    checks = {}
    for m in wakes.WAKE_MODELS:
        if m != wake:
            g2, n2 = run(x, y, model=m)
            checks[m] = {"wake_loss_pct": float(100 * (1 - n2.sum() / g2.sum())), "p50_gwh": float(n2.sum() * other / 1000)}
    # what the flow model adds: the same layout with the mast's own wind at every turbine (density kept)
    mast_only = np.broadcast_to(at(dens, x, y)[:, None], (n, len(centres)))
    g3, n3 = run(x, y, ratio=np.minimum(ratios_at(x, y), mast_only))
    ceiling = {"p50_gwh": float(n3.sum() * other / 1000), "change_pct": float(100 * (n3.sum() / net.sum() - 1))}
    # sensitivity of energy to wind speed: +1 % on every sector
    site_up = wakes.site_for(ratios_at(x, y), centres, f, A * 1.01, k, ti)
    _, n5 = wakes.simulate(site_up, wt, x, y, wake)
    energy_per_speed = float((n5.sum() / net.sum() - 1) / 0.01)
    spread = max([abs(v["p50_gwh"] / res["p50_gwh"] - 1) * 100 for v in checks.values()] or [0.0])
    e_terms = dict(budget.DEFAULT_ENERGY_UNCERTAINTY if energy_uncertainty is None else energy_uncertainty)
    if "Wake model" in e_terms:
        e_terms["Wake model"] = max(e_terms["Wake model"], spread / 2)

    lat, lon = grid.to_latlon(x, y)
    free_ws, ground = at(field.mean_ws, x, y), at(grid.elev, x, y)
    slope = grid.slope_at(x, y) if exact else at(grid.slope_pct, x, y)
    steep_mast = float(grid.steep_share_round(*mast_xy, budget.STEEP_RADIUS_M, budget.STEEP_LIMIT_PCT)[0])
    steep = grid.steep_share_round(x, y, budget.STEEP_RADIUS_M, budget.STEEP_LIMIT_PCT)
    positions = []
    for j in range(n):
        positions.append({
            "id": f"T{j + 1:02d}", "x": float(x[j]), "y": float(y[j]), "lat": float(lat[j]), "lon": float(lon[j]),
            "ground_elev_m": float(ground[j]), "slope_pct": float(slope[j]),
            "dist_mast_km": float(np.hypot(x[j] - mast_xy[0], y[j] - mast_xy[1]) / 1000), "dz_m": float(ground[j] - elev_mast),
            "free_ws": float(free_ws[j]), "ratio_to_mast": float(free_ws[j] / field.mast_mean_ws),
            "gross_mwh": float(gross[j]), "wake_loss_pct": float(100 * (1 - net[j] / gross[j])),
            "after_wake_mwh": float(net[j]), "p50_mwh": float(net[j] * other),
            "capacity_factor": float(net[j] * other / (turbine.rated_kw / 1000.0 * wakes.HOURS)),
            "steep_share_pct": None if np.isnan(steep[j]) else round(float(steep[j]), 1),
            "complex_terrain": budget.is_complex(steep_mast, None if np.isnan(steep[j]) else float(steep[j]))})
    # the uncertainty of carrying the mast's wind to where each turbine stands, then the budget of the whole farm
    if speed_uncertainty is None:
        top = float(clim["height_m"] if top_m is None else top_m)
        ext, each = budget.extrapolation_terms(positions, [q["p50_mwh"] for q in positions], turbine.hub_m, top,
                                               profile_clean)
        speed_terms = {**budget.speed_terms_for(long_term), **ext}
        rest = sum(v * v for k, v in speed_terms.items() if k != budget.HORIZONTAL)
        energy_rest = sum(v * v for v in e_terms.values())
        for q, h in zip(positions, each):
            q["horizontal_unc_pct"] = round(h, 2)                  # of wind speed
            q["sigma_energy_pct"] = round(float(np.sqrt((rest + h * h) * energy_per_speed ** 2 + energy_rest)), 2)
    else:
        speed_terms = speed_uncertainty
    unc = budget.uncertainty(res["p50_gwh"], energy_per_speed, speed_terms, e_terms, iav_speed)
    n_complex = sum(1 for q in positions if q["complex_terrain"])
    if speed_uncertainty is None:
        unc["by_position"] = {"rule": {"base_pct": budget.H_BASE_PCT, "per_km_pct": budget.H_PER_KM_PCT,
                                       "per_50m_height_pct": budget.H_PER_50M_PCT, "share_of_speed_up": budget.H_SPEEDUP_SHARE,
                                       "vertical_base_pct": budget.V_BASE_PCT,
                                       "vertical_per_10m_pct": budget.V_PER_10M_PCT if profile_clean else budget.V_PER_10M_UNCLEAN_PCT,
                                       "steep_limit_pct": budget.STEEP_LIMIT_PCT, "steep_radius_m": budget.STEEP_RADIUS_M,
                                       "complex_share_pct": budget.COMPLEX_SHARE_PCT, "complex_factor": budget.H_COMPLEX_FACTOR},
                              "horizontal_pct_ws": ext[budget.HORIZONTAL], "vertical_pct_ws": ext[budget.VERTICAL],
                              "top_m": top, "hub_m": turbine.hub_m, "profile_clean": bool(profile_clean),
                              "horizontal_range_pct_ws": [round(min(each), 2), round(max(each), 2)],
                              "steep_share_mast_pct": round(steep_mast, 1), "turbines_complex": n_complex,
                              "long_term_pct_ws": speed_terms.get(budget.LONG_TERM),
                              "long_term_basis": (long_term or {}).get("basis", ""),
                              "reference_pct_ws": speed_terms.get(budget.REFERENCE, speed_terms.get(budget.REFERENCE_TWO))}
        if not profile_clean and turbine.hub_m > top:
            notes.append(f"The measured wind profile is not clean (see the shear warning), so carrying the wind from the top "
                         f"anemometer at {top:g} m to the {turbine.hub_m:g} m hub is taken as {budget.V_BASE_PCT:g} % plus "
                         f"{budget.V_PER_10M_UNCLEAN_PCT:g} % per 10 m ({ext[budget.VERTICAL]:.1f} % of the wind speed), not "
                         f"{budget.V_PER_10M_PCT:g} % per 10 m.")
        if long_term and long_term.get("basis"):
            notes.append(f"Long-term uncertainty {speed_terms[budget.LONG_TERM]:g} % of the wind speed: {long_term['basis']}.")
    hi = max(p["ratio_to_mast"] for p in positions)
    cautions = []
    if n_complex:
        where = ("round the mast" if budget.is_complex(steep_mast) else
                 f"round {n_complex} of the {n} turbine{'s' if n > 1 else ''}")
        worst = max([steep_mast] + [q["steep_share_pct"] or 0.0 for q in positions])
        cautions.append(
            f"Complex terrain {where}: up to {worst:.0f} % of the ground within {budget.STEEP_RADIUS_M / 1000:g} km is steeper "
            f"than {budget.STEEP_LIMIT_PCT:g} % (17 degrees). " + _complex_model_text(field.engine) +
            f" The horizontal uncertainty of {'every turbine' if n_complex == n else f'those {n_complex} turbines'} is multiplied by "
            f"{budget.H_COMPLEX_FACTOR:g}. Before relying on the wind at the turbine positions, have an independent flow study "
            "(for example CFD) made by an independent assessor.")
    if ceiling["change_pct"] < -CEILING_CAUTION_PCT:
        cautions.append(f"{-ceiling['change_pct']:.0f} % of the P50 rests on turbine positions being windier than the mast "
                        f"according to the wind map (the windiest by {100 * (hi - 1):.0f} %). With no position windier than "
                        f"the mast the P50 would be {ceiling['p50_gwh']:.0f} GWh a year. Wind that was mapped, not measured, "
                        "needs a measurement at the positions before it is relied on.")
    res.update({
        "turbine": turbine.as_dict(), "n_requested": n_requested, "n_placed": n, "capacity_mw": rated_mw,
        "positions": positions, "wake_model": wake, "wake_model_name": wakes.WAKE_MODELS[wake], "wake_checks": checks,
        "mast_wind_ceiling": ceiling, "energy_per_speed": energy_per_speed, "uncertainty": unc,
        "losses": budget.losses_table(losses), "prevailing_deg": theta, "ti": ti, "rho_hub": float(rho_hub),
        "spacing": {"cross_d": cross_d, "along_d": along_d, "link_m": link_m,
                    "closest_pair_d": placement.min_spacing_d(x, y, turbine.rotor_m)},
        "mast_mean_ws": field.mast_mean_ws, "mean_free_ws": float(np.mean(free_ws)), "refinement": refinement,
        "engine": field.engine, "notes": notes, "cautions": cautions, "hours_per_year": wakes.HOURS})
    return res


def _refine(cx, cy, value, idx, d, theta, cross_d, along_d, link_m, run, deadline, log, n_alt=12, worst=4,
            max_pass=2) -> tuple:
    """Wake-aware swaps. The most-waked turbines are tried on the best free cells that keep the spacing and link
    rules; candidates are screened with the fast Jensen model and a move is kept only if the wind farm total
    under the chosen wake model improves."""
    cur = [int(i) for i in idx]
    g, n = run(cx[cur], cy[cur])
    start = total = float(n.sum())
    moves = []
    for _ in range(max_pass):
        improved = False
        wl = 1 - n / g
        for j in np.argsort(-wl)[:worst]:
            if wl[j] < 0.02 or time.time() > deadline:
                continue
            others = [cur[m] for m in range(len(cur)) if m != j]
            ok = np.isfinite(value)
            ok[cur] = False
            near = np.zeros(len(cx), bool)
            for o in others:
                ok &= placement.spacing_ok(cx, cy, cx[o], cy[o], d, cross_d, along_d, theta)
                if link_m:
                    near |= np.hypot(cx - cx[o], cy - cy[o]) <= link_m
            if link_m:
                ok &= near
            alts = np.flatnonzero(ok)
            alts = alts[np.argsort(-value[alts])][:n_alt]
            _, nj = run(cx[cur], cy[cur], model="jensen075")
            base_j, best = float(nj.sum()), None
            for alt in alts:
                if time.time() > deadline:
                    break
                trial = cur.copy()
                trial[j] = int(alt)
                if not placement.connected_set(cx[trial], cy[trial], link_m):
                    continue
                _, nt = run(cx[trial], cy[trial], model="jensen075")
                if nt.sum() > base_j * 1.001 and (best is None or nt.sum() > best[1]):
                    best = (int(alt), float(nt.sum()))
            if best is not None:
                trial = cur.copy()
                trial[j] = best[0]
                g2, n2 = run(cx[trial], cy[trial])
                if n2.sum() > total * 1.0005:
                    moves.append({"from_xy": [float(cx[cur[j]]), float(cy[cur[j]])],
                                  "to_xy": [float(cx[best[0]]), float(cy[best[0]])],
                                  "gain_pct": float(100 * (n2.sum() / total - 1))})
                    cur, g, n, total = trial, g2, n2, float(n2.sum())
                    improved = True
        if not improved or time.time() > deadline:
            break
    if moves:
        log(f"Fine-tuning moved {len(moves)} turbine{'s' if len(moves) != 1 else ''}: +{100 * (total / start - 1):.2f} % energy.")
    return np.array(cur, dtype=int), {"moves": moves, "gain_pct": float(100 * (total / start - 1))}
