"""Losses other than wakes, and the uncertainty budget that turns a P50 into P75 and P90.

Every figure here is a developer assumption with its reason next to it. They are defaults to edit for each
project, not measured values.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from . import EnergyError

Z75, Z90 = 0.6745, 1.2816


@dataclass(frozen=True)
class Loss:
    group: str
    item: str
    pct: float
    basis: str


DEFAULT_LOSSES = [
    Loss("Availability", "Turbine availability", 3.0, "97 % level typical of a service contract at a remote site"),
    Loss("Availability", "Balance of plant availability", 0.5, "collection system and substation"),
    Loss("Availability", "Grid availability", 1.0, "network outages (economic curtailment not included)"),
    Loss("Electrical", "Electrical efficiency", 2.5, "collection, step-up and the line to the grid connection point"),
    Loss("Turbine performance", "Power curve adjustment", 1.0, "site inflow: terrain, turbulence, shear and veer"),
    Loss("Environmental", "Degradation, soiling, temperature and altitude derating", 1.0, "no icing assumed"),
    Loss("Curtailment", "Operational curtailment", 0.5, "ramp limits and environmental stops; grid curtailment not included"),
]

# 1 sigma, per cent of wind speed. The two extrapolation terms are the fallbacks for a call that gives no turbine
# positions; a layout works them out from where its turbines stand (extrapolation_terms).
VERTICAL = "Vertical extrapolation from the mast top to hub height"
HORIZONTAL = "Horizontal extrapolation from the mast to the turbines (screening flow model)"
DEFAULT_SPEED_UNCERTAINTY = {
    "Measurement (one mast: sensors, mounting, data recovery)": 2.5,
    "Long-term correction (one reanalysis index)": 3.0,
    VERTICAL: 3.0,
    HORIZONTAL: 8.0,
}

# How far the mast's wind can be carried, 1 sigma in per cent of wind speed. Developer assumptions for a screening
# flow model, not values from a standard; edit them here. A turbine next to the mast still carries H_BASE.
H_BASE_PCT = 2.0            # the flow model at its best, next to the mast
H_PER_KM_PCT = 1.0          # for every km between the turbine and the mast
H_PER_50M_PCT = 1.0         # for every 50 m the turbine's ground lies above or below the mast's
H_SPEEDUP_SHARE = 0.25      # a quarter of the speed-up (or slow-down) the flow model gives the position
V_BASE_PCT = 1.0            # hub height within the heights measured
V_PER_10M_PCT = 0.5         # for every 10 m the hub stands above the top anemometer
# 1 sigma, per cent of energy
DEFAULT_ENERGY_UNCERTAINTY = {
    "Power curve (generic, not warranted by a manufacturer)": 6.0,
    "Wake model": 1.0,
    "Other losses": 2.0,
}
DEFAULT_IAV_SPEED = 6.0      # inter-annual variability of the annual mean wind speed, per cent


def losses_from(items) -> list:
    """Losses from dicts (as edited in the app) or Loss objects. None stands for the defaults; an empty list is a
    budget with no losses, as it says. A loss that is not a number from 0 up to, but not including, 100 is refused:
    a negative loss would add energy, and 100 % or more would leave none."""
    if items is None:
        return list(DEFAULT_LOSSES)
    if not isinstance(items, (list, tuple)):
        raise EnergyError("The losses must be given as a table: one row per loss, each with a name and a percentage.")
    out = []
    for it in items:
        if not isinstance(it, Loss):
            try:
                name = str(it["item"])
            except (KeyError, TypeError, IndexError):
                raise EnergyError("A loss in the table has no name. Each row needs a name and a percentage.") from None
            try:
                it = Loss(str(it.get("group", "")), name, float(it["pct"]), str(it.get("basis", "")))
            except (KeyError, TypeError, ValueError):
                raise EnergyError(f"The loss for '{name}' is not a number. Give a figure from 0 to less than 100.") from None
        if not (math.isfinite(it.pct) and 0.0 <= it.pct < 100.0):
            raise EnergyError(f"The loss for '{it.item}' is {it.pct:g} %. Give a figure from 0 to less than 100.")
        out.append(it)
    return out


def loss_factor(losses=None) -> float:
    """Losses combine by multiplication: 3 % and 2.5 % leave 0.97 x 0.975, not 0.945."""
    fac = 1.0
    for item in losses_from(losses):
        fac *= 1 - item.pct / 100.0
    return fac


def losses_table(losses=None) -> list:
    return [asdict(x) for x in losses_from(losses)]


def horizontal_pct(dist_km: float, dz_m: float, ratio_to_mast: float) -> float:
    """1 sigma, per cent of wind speed, for carrying the mast's wind to one turbine position. The four parts are
    taken as independent: root of the sum of squares."""
    return math.sqrt(H_BASE_PCT ** 2 + (H_PER_KM_PCT * dist_km) ** 2 + (H_PER_50M_PCT * dz_m / 50.0) ** 2
                     + (H_SPEEDUP_SHARE * 100.0 * (ratio_to_mast - 1.0)) ** 2)


def vertical_pct(hub_m: float, top_m: float) -> float:
    """1 sigma, per cent of wind speed, for carrying the wind from the top anemometer to the hub."""
    return V_BASE_PCT + V_PER_10M_PCT * max(0.0, hub_m - top_m) / 10.0


def extrapolation_terms(positions: list, weights, hub_m: float, top_m: float) -> tuple:
    """The two extrapolation terms of a layout, and each turbine's horizontal figure. positions: dicts with
    dist_mast_km, dz_m and ratio_to_mast. The farm's horizontal term is the mean of the turbines' figures weighted by
    their energy: the errors of neighbouring positions go together, so they are not averaged away."""
    each = [horizontal_pct(q["dist_mast_km"], q["dz_m"], q["ratio_to_mast"]) for q in positions]
    w = [max(float(v), 0.0) for v in weights]
    total = sum(w) or 1.0
    farm = sum(e * v for e, v in zip(each, w)) / total if sum(w) else sum(each) / max(len(each), 1)
    return {VERTICAL: round(vertical_pct(hub_m, top_m), 2), HORIZONTAL: round(farm, 2)}, each


def uncertainty(p50: float, energy_per_speed: float, speed_terms: dict | None = None,
                energy_terms: dict | None = None, iav_speed: float = DEFAULT_IAV_SPEED) -> dict:
    """p50 in any energy unit. energy_per_speed: per cent change of energy for one per cent change of wind speed
    (from the layout itself). Speed uncertainties are converted to energy with it; all terms are taken as
    independent and combined as the root of the sum of squares. Energy is taken as normally distributed."""
    s = float(energy_per_speed)
    terms = {name: v * s for name, v in (DEFAULT_SPEED_UNCERTAINTY if speed_terms is None else speed_terms).items()}
    terms.update(DEFAULT_ENERGY_UNCERTAINTY if energy_terms is None else energy_terms)
    hist = math.sqrt(sum(v * v for v in terms.values()))
    sig = {"long_term": hist, "10_year": math.sqrt(hist ** 2 + (iav_speed * s) ** 2 / 10.0),
           "1_year": math.sqrt(hist ** 2 + (iav_speed * s) ** 2)}
    out = {"terms_energy_pct": {k: round(v, 2) for k, v in terms.items()}, "energy_per_speed": round(s, 3),
           "iav_speed_pct": iav_speed, "sigma_pct": {k: round(v, 2) for k, v in sig.items()}, "p50": p50}
    for label, sg in sig.items():
        out[f"p75_{label}"] = p50 * (1 - Z75 * sg / 100.0)
        out[f"p90_{label}"] = p50 * (1 - Z90 * sg / 100.0)
    return out
