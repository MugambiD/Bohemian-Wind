"""Turbine noise at homes: a screening calculation after ISO 9613-2 (general method of calculation, A-weighted
levels at 500 Hz, the standard's alternative method for ground attenuation).

For each turbine and receptor:   L = Lw + D_omega - A_div - A_atm - A_gr
  A_div   geometric spreading: 20 log10(d) + 11, d = distance from the hub to the receiver in metres
  A_atm   air absorption: alpha x d / 1000 (alpha 1.9 dB/km at 500 Hz, 10 C, 70 % humidity)
  A_gr    ground attenuation: 4.8 - (2 hm / d)(17 + 300 / d), not below 0; hm = mean height of the sound path
          above the ground, taken along the terrain profile
  D_omega correction for the reflection in the ground near the source, which belongs with this A_gr
Turbines are added on an energy basis. Terrain is used for heights and for hm; screening by hills and
buildings is ignored, which errs on the loud side.

[COMPLIANCE] A screening: no measured background noise, no manufacturer octave-band data, no tonal or
low-frequency assessment. A permit application needs an accredited noise study to the rules of the country.
"""
from __future__ import annotations

import numpy as np

from ..terrain import Grid

ALPHA_500HZ = 1.9            # dB/km
RECEIVER_HEIGHT = 4.0        # m above ground (ISO 9613-2 practice for dwellings)
PROFILE_POINTS = 40
UNCERTAINTY_DB = 2.0         # allowance added to every level for the second verdict ("over with allowance")


def level(lw: float, d, dp, hs, hr, hm, alpha: float = ALPHA_500HZ):
    """Sound pressure level in dB(A) from one source. d: hub-to-receiver distance; dp: its horizontal part;
    hs, hr: source and receiver heights above their own ground; hm: mean path height above ground."""
    d, dp = np.asarray(d, dtype=float), np.asarray(dp, dtype=float)
    a_div = 20.0 * np.log10(d) + 11.0
    a_atm = alpha * d / 1000.0
    a_gr = np.maximum(4.8 - (2.0 * np.asarray(hm, dtype=float) / d) * (17.0 + 300.0 / d), 0.0)
    d_omega = 10.0 * np.log10(1.0 + (dp ** 2 + (hs - hr) ** 2) / (dp ** 2 + (hs + hr) ** 2))
    return lw + d_omega - a_div - a_atm - a_gr


def reach_m(lw: float, limit: float, n_turbines: int, alpha: float = ALPHA_500HZ) -> float:
    """The distance from the nearest turbine beyond which n_turbines together stay under limit, whatever the terrain:
    every turbine is taken to stand at that same distance, with the whole ground reflection (D_omega at its largest,
    3 dB) and no ground attenuation. A home farther away than this from every turbine cannot be over the limit by
    the method of this module."""
    top = lw + 10.0 * np.log10(2.0) + 10.0 * np.log10(max(int(n_turbines), 1)) - 11.0 - limit
    lo, hi = 1.0, 1.0e6
    for _ in range(60):                                   # 20 log10(d) + alpha d / 1000 grows with d: halve the bracket
        mid = float(np.sqrt(lo * hi))
        if 20.0 * np.log10(mid) + alpha * mid / 1000.0 < top:
            lo = mid
        else:
            hi = mid
    return hi


def add_levels(levels, axis=0):
    """Energy sum of levels in dB."""
    return 10.0 * np.log10(np.sum(10.0 ** (np.asarray(levels, dtype=float) / 10.0), axis=axis))


def _mean_path_height(grid: Grid, xs, ys, zs_abs, xr, yr, zr_abs):
    """Mean height of the straight sound path above the ground, from the terrain profile under it. Takes single
    values or arrays that broadcast together (sources against receivers); the last axis added is the profile."""
    t = (np.arange(PROFILE_POINTS) + 0.5) / PROFILE_POINTS
    xs, ys, zs_abs, xr, yr, zr_abs = (np.asarray(v, dtype=float)[..., None] for v in (xs, ys, zs_abs, xr, yr, zr_abs))
    ground = grid.sample(xs + t * (xr - xs), ys + t * (yr - ys))
    hm = np.maximum(zs_abs + t * (zr_abs - zs_abs) - ground, 0.0).mean(axis=-1)
    return float(hm) if hm.ndim == 0 else hm


def at_receptors(grid: Grid, turbines: list, receptors: list, hub_m: float, lw: float, limit: float = 45.0,
                 uncertainty_db: float = UNCERTAINTY_DB, alpha: float = ALPHA_500HZ,
                 receiver_height: float = RECEIVER_HEIGHT) -> dict:
    """turbines, receptors: dicts with x, y (local metres) and id / name. Returns a row per receptor and totals.
    lw: the sound power level of every turbine, or one per turbine (quiet modes). Receptors are worked through in
    blocks, so that tens of thousands of homes take seconds, not minutes."""
    rows = []
    lw = np.asarray(lw, dtype=float) if np.ndim(lw) else float(lw)
    tx = np.array([t["x"] for t in turbines], dtype=float)
    ty = np.array([t["y"] for t in turbines], dtype=float)
    tz = grid.sample(tx, ty) + hub_m
    rx_all = np.array([rc["x"] for rc in receptors], dtype=float)
    ry_all = np.array([rc["y"] for rc in receptors], dtype=float)
    block = max(1, int(400_000 // max(len(tx) * PROFILE_POINTS, 1)))        # about 400,000 profile points at a time
    for i0 in range(0, len(receptors), block):
        rx, ry = rx_all[i0:i0 + block, None], ry_all[i0:i0 + block, None]     # [receptor, turbine]
        zr = grid.sample(rx, ry) + receiver_height
        dp = np.hypot(tx[None, :] - rx, ty[None, :] - ry)
        d = np.sqrt(dp ** 2 + (tz[None, :] - zr) ** 2)
        hm = _mean_path_height(grid, tx[None, :], ty[None, :], tz[None, :], rx, ry, zr)
        each = level(lw, d, dp, hub_m, receiver_height, hm, alpha)
        total = add_levels(each, axis=1)
        near = np.argmin(dp, axis=1)
        for k, rc in enumerate(receptors[i0:i0 + block]):
            j, tot = int(near[k]), round(float(total[k]), 1)      # the verdict goes by the level as it is shown
            rows.append({"name": rc["name"], "level_dba": tot, "level_plus_uncertainty_dba": round(tot + uncertainty_db, 1),
                         "nearest_turbine": turbines[j].get("id", str(j + 1)), "distance_m": round(float(dp[k, j]), 0),
                         "loudest_single_dba": round(float(each[k].max()), 1), "over_limit": bool(tot > limit),
                         "over_limit_with_uncertainty": bool(round(tot + uncertainty_db, 1) > limit),
                         "lat": rc.get("lat"), "lon": rc.get("lon"), "x": rc["x"], "y": rc["y"]})
    rows.sort(key=lambda r: -r["level_dba"])
    return {"receptors": rows, "limit_dba": limit, "uncertainty_db": uncertainty_db,
            "sound_power_dba": float(lw.max()) if np.ndim(lw) else lw,
            "over_limit": sum(r["over_limit"] for r in rows),
            "over_limit_with_uncertainty": sum(r["over_limit_with_uncertainty"] for r in rows),
            "highest_dba": rows[0]["level_dba"] if rows else None, "n_receptors": len(rows),
            "nearest_home_m": min((r["distance_m"] for r in rows), default=None),
            "method": "ISO 9613-2 general method, A-weighted at 500 Hz, alternative ground attenuation; "
                      f"air absorption {alpha:g} dB/km; receiver {receiver_height:g} m above ground; terrain screening ignored"}


def on_grid(grid: Grid, turbines: list, hub_m: float, lw: float, alpha: float = ALPHA_500HZ,
            receiver_height: float = RECEIVER_HEIGHT) -> np.ndarray:
    """Noise level at every cell, for a map. The mean path height is taken between the two end points (no terrain
    profile), which is close enough for a picture; receptor figures use the profile."""
    X, Y = grid.mesh()
    zr = grid.elev + receiver_height
    energy = np.zeros(grid.shape)
    for t in turbines:
        zs = float(grid.sample(t["x"], t["y"])) + hub_m
        dp = np.maximum(np.hypot(X - t["x"], Y - t["y"]), 1.0)
        d = np.sqrt(dp ** 2 + (zs - zr) ** 2)
        hm = np.maximum((hub_m + receiver_height) / 2.0, 0.0)
        energy += 10.0 ** (level(lw, d, dp, hub_m, receiver_height, hm, alpha) / 10.0)
    return 10.0 * np.log10(np.maximum(energy, 1e-12))


def propagation(grid: Grid, turbines: list, receptors: list, hub_m: float, alpha: float = ALPHA_500HZ,
                receiver_height: float = RECEIVER_HEIGHT) -> np.ndarray:
    """[receptor, turbine]: what the path adds to a turbine's sound power level to give its level at the home (a
    negative number of dB). The same calculation as at_receptors, kept apart so that the turbines' sound power
    levels can be varied without walking the terrain again."""
    tx = np.array([t["x"] for t in turbines], dtype=float)
    ty = np.array([t["y"] for t in turbines], dtype=float)
    tz = grid.sample(tx, ty) + hub_m
    out = np.zeros((len(receptors), len(turbines)))
    block = max(1, int(400_000 // max(len(tx) * PROFILE_POINTS, 1)))
    for i0 in range(0, len(receptors), block):
        rx = np.array([rc["x"] for rc in receptors[i0:i0 + block]], dtype=float)[:, None]
        ry = np.array([rc["y"] for rc in receptors[i0:i0 + block]], dtype=float)[:, None]
        zr = grid.sample(rx, ry) + receiver_height
        dp = np.hypot(tx[None, :] - rx, ty[None, :] - ry)
        d = np.sqrt(dp ** 2 + (tz[None, :] - zr) ** 2)
        hm = _mean_path_height(grid, tx[None, :], ty[None, :], tz[None, :], rx, ry, zr)
        out[i0:i0 + block] = level(0.0, d, dp, hub_m, receiver_height, hm, alpha)
    return out
