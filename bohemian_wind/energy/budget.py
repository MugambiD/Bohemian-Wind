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

# 1 sigma, per cent of wind speed
DEFAULT_SPEED_UNCERTAINTY = {
    "Measurement (one mast: sensors, mounting, data recovery)": 2.5,
    "Long-term correction (one reanalysis index)": 3.0,
    "Vertical extrapolation from the mast top to hub height": 3.0,
    "Horizontal extrapolation from the mast to the turbines (screening flow model)": 8.0,
}
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
