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


def level(lw: float, d, dp, hs, hr, hm, alpha: float = ALPHA_500HZ):
    """Sound pressure level in dB(A) from one source. d: hub-to-receiver distance; dp: its horizontal part;
    hs, hr: source and receiver heights above their own ground; hm: mean path height above ground."""
    d, dp = np.asarray(d, dtype=float), np.asarray(dp, dtype=float)
    a_div = 20.0 * np.log10(d) + 11.0
    a_atm = alpha * d / 1000.0
    a_gr = np.maximum(4.8 - (2.0 * np.asarray(hm, dtype=float) / d) * (17.0 + 300.0 / d), 0.0)
    d_omega = 10.0 * np.log10(1.0 + (dp ** 2 + (hs - hr) ** 2) / (dp ** 2 + (hs + hr) ** 2))
    return lw + d_omega - a_div - a_atm - a_gr


def add_levels(levels, axis=0):
    """Energy sum of levels in dB."""
    return 10.0 * np.log10(np.sum(10.0 ** (np.asarray(levels, dtype=float) / 10.0), axis=axis))


def _mean_path_height(grid: Grid, xs, ys, zs_abs, xr, yr, zr_abs) -> float:
    t = (np.arange(PROFILE_POINTS) + 0.5) / PROFILE_POINTS
    ground = grid.sample(xs + t * (xr - xs), ys + t * (yr - ys))
    return float(np.maximum(zs_abs + t * (zr_abs - zs_abs) - ground, 0.0).mean())


def at_receptors(grid: Grid, turbines: list, receptors: list, hub_m: float, lw: float, limit: float = 45.0,
                 uncertainty_db: float = 2.0, alpha: float = ALPHA_500HZ, receiver_height: float = RECEIVER_HEIGHT) -> dict:
    """turbines, receptors: dicts with x, y (local metres) and id / name. Returns a row per receptor and totals."""
    rows = []
    tx = np.array([t["x"] for t in turbines], dtype=float)
    ty = np.array([t["y"] for t in turbines], dtype=float)
    tz = grid.sample(tx, ty) + hub_m
    for rc in receptors:
        zr_ground = float(grid.sample(rc["x"], rc["y"]))
        zr = zr_ground + receiver_height
        dp = np.hypot(tx - rc["x"], ty - rc["y"])
        d = np.sqrt(dp ** 2 + (tz - zr) ** 2)
        hm = np.array([_mean_path_height(grid, tx[i], ty[i], tz[i], rc["x"], rc["y"], zr) for i in range(len(tx))])
        each = level(lw, d, dp, hub_m, receiver_height, hm, alpha)
        total = float(add_levels(each))
        j = int(np.argmin(dp))
        rows.append({"name": rc["name"], "level_dba": round(total, 1), "level_plus_uncertainty_dba": round(total + uncertainty_db, 1),
                     "nearest_turbine": turbines[j].get("id", str(j + 1)), "distance_m": round(float(dp[j]), 0),
                     "loudest_single_dba": round(float(each.max()), 1), "over_limit": bool(total > limit),
                     "over_limit_with_uncertainty": bool(total + uncertainty_db > limit),
                     "lat": rc.get("lat"), "lon": rc.get("lon"), "x": rc["x"], "y": rc["y"]})
    rows.sort(key=lambda r: -r["level_dba"])
    return {"receptors": rows, "limit_dba": limit, "sound_power_dba": lw, "uncertainty_db": uncertainty_db,
            "over_limit": sum(r["over_limit"] for r in rows),
            "over_limit_with_uncertainty": sum(r["over_limit_with_uncertainty"] for r in rows),
            "highest_dba": rows[0]["level_dba"] if rows else None, "n_receptors": len(rows),
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
