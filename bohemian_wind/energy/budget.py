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

# 1 sigma, per cent of wind speed. The long-term and the two extrapolation terms are the fallbacks for a call that
# gives no campaign or turbine positions; a layout works them out from the campaign's length (long_term_pct) and
# from where its turbines stand (extrapolation_terms).
MEASUREMENT = "Measurement (one mast: sensors, mounting, data recovery)"
LONG_TERM = "Long-term correction (one reanalysis index)"
REFERENCE = "Long-term reference (one reanalysis, not checked against a second)"
VERTICAL = "Vertical extrapolation from the mast top to hub height"
HORIZONTAL = "Horizontal extrapolation from the mast to the turbines (screening flow model)"
DEFAULT_SPEED_UNCERTAINTY = {
    MEASUREMENT: 2.5,
    LONG_TERM: 3.0,
    REFERENCE: 3.0,
    VERTICAL: 3.0,
    HORIZONTAL: 8.0,
}

# The long-term term by what the campaign covers. Corrected with the reanalysis index, a part-year campaign errs
# several times more than a full year (in-house tests on measured masts, not published: corrected parts of long
# campaigns against the whole campaign). Not corrected, the measured period stands for the long term: a full year
# errs by the year-to-year variability (DEFAULT_IAV_SPEED), a part-year by its missing seasons as well.
LT_CORRECTED_PCT = ((12, 3.0), (6, 6.0), (0, 10.0))      # (calendar months measured, at least) -> per cent
LT_UNCORRECTED_PART_YEAR_PCT = ((6, 12.0), (0, 20.0))
# One reanalysis is one view of the long term, and two can disagree by more than the correction term allows. Until
# the tool checks against a second reanalysis this line stays in.
REFERENCE_PCT = 3.0

# How far the mast's wind can be carried, 1 sigma in per cent of wind speed. Developer assumptions for a screening
# flow model, not values from a standard; edit them here. A turbine next to the mast still carries H_BASE.
H_BASE_PCT = 2.0            # the flow model at its best, next to the mast
H_PER_KM_PCT = 1.0          # for every km between the turbine and the mast
H_PER_50M_PCT = 1.0         # for every 50 m the turbine's ground lies above or below the mast's
H_SPEEDUP_SHARE = 0.25      # a quarter of the speed-up (or slow-down) the flow model gives the position
V_BASE_PCT = 1.0            # hub height within the heights measured
V_PER_10M_PCT = 0.5         # for every 10 m the hub stands above the top anemometer
V_PER_10M_UNCLEAN_PCT = 2.5 # the same when the measured profile is not clean (climate's shear warning): where the
                            # profile bends, a power law from lower cups misses the top one by several per cent
# Complex terrain. The share of the ground within STEEP_RADIUS_M of a point steeper than STEEP_LIMIT_PCT. Above
# COMPLEX_SHARE_PCT round the mast or a turbine, the linear screening model is outside its range there: that
# turbine's horizontal figure is multiplied by H_COMPLEX_FACTOR. Set from in-house tests on measured masts (not
# published): gentle sites have a few per cent of such ground at most, escarpments a quarter or more.
STEEP_LIMIT_PCT = 30.0
STEEP_RADIUS_M = 3500.0
COMPLEX_SHARE_PCT = 10.0
H_COMPLEX_FACTOR = 3.0
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


def horizontal_pct(dist_km: float, dz_m: float, ratio_to_mast: float, complex_terrain: bool = False) -> float:
    """1 sigma, per cent of wind speed, for carrying the mast's wind to one turbine position. The four parts are
    taken as independent: root of the sum of squares. Complex terrain round the mast or the turbine multiplies the
    figure by H_COMPLEX_FACTOR."""
    v = math.sqrt(H_BASE_PCT ** 2 + (H_PER_KM_PCT * dist_km) ** 2 + (H_PER_50M_PCT * dz_m / 50.0) ** 2
                  + (H_SPEEDUP_SHARE * 100.0 * (ratio_to_mast - 1.0)) ** 2)
    return v * H_COMPLEX_FACTOR if complex_terrain else v


def is_complex(*steep_shares_pct) -> bool:
    """Complex terrain when the steep share round any of the points given (the mast, a turbine) passes the limit."""
    return any(s is not None and s > COMPLEX_SHARE_PCT for s in steep_shares_pct)


def vertical_pct(hub_m: float, top_m: float, clean_profile: bool = True) -> float:
    """1 sigma, per cent of wind speed, for carrying the wind from the top anemometer to the hub. A measured profile
    that is not clean (climate's shear warning) takes the steeper rate per 10 m."""
    per = V_PER_10M_PCT if clean_profile else V_PER_10M_UNCLEAN_PCT
    return V_BASE_PCT + per * max(0.0, hub_m - top_m) / 10.0


def long_term_pct(calendar_months: int | None, corrected: bool = True, months: int | None = None,
                  iav_speed: float = DEFAULT_IAV_SPEED) -> tuple:
    """(1 sigma in per cent of wind speed, why) for the long-term term. calendar_months: how many of the twelve
    calendar months the campaign covers; months: how many months it holds in all (for an uncorrected campaign of a
    year or more, whose error shrinks with the years it spans)."""
    cal = int(calendar_months or 0)
    if corrected:
        for least, pct in LT_CORRECTED_PCT:
            if cal >= least:
                break
        why = ("corrected with one reanalysis index, all twelve calendar months measured" if cal >= 12 else
               f"corrected with one reanalysis index, but only {cal} of the twelve calendar months measured")
        return pct, why
    if cal >= 12:
        years = max(float(months or 12) / 12.0, 1.0)
        pct = max(LT_CORRECTED_PCT[0][1], iav_speed / math.sqrt(years))
        return pct, (f"no long-term correction: the {years:.1f} years measured stand for the long term, so the "
                     "year-to-year variability of the wind stays in")
    for least, pct in LT_UNCORRECTED_PART_YEAR_PCT:
        if cal >= least:
            break
    return pct, (f"no long-term correction and only {cal} of the twelve calendar months measured: the seasons not "
                 "measured are missing as well as the long term")


def speed_terms_for(long_term: dict | None = None) -> dict:
    """The fixed speed terms with the long-term pair set for a campaign: long_term is {"pct", "corrected"} from
    long_term_pct (None keeps the defaults). An uncorrected campaign has no reference to doubt."""
    terms = dict(DEFAULT_SPEED_UNCERTAINTY)
    if long_term:
        terms[LONG_TERM] = float(long_term["pct"])
        if not long_term.get("corrected", True):
            terms.pop(REFERENCE, None)
    return terms


def extrapolation_terms(positions: list, weights, hub_m: float, top_m: float, clean_profile: bool = True) -> tuple:
    """The two extrapolation terms of a layout, and each turbine's horizontal figure. positions: dicts with
    dist_mast_km, dz_m and ratio_to_mast, and complex_terrain when the ground round the mast or the turbine is steep.
    The farm's horizontal term is the mean of the turbines' figures weighted by their energy: the errors of
    neighbouring positions go together, so they are not averaged away."""
    each = [horizontal_pct(q["dist_mast_km"], q["dz_m"], q["ratio_to_mast"], bool(q.get("complex_terrain")))
            for q in positions]
    w = [max(float(v), 0.0) for v in weights]
    total = sum(w) or 1.0
    farm = sum(e * v for e, v in zip(each, w)) / total if sum(w) else sum(each) / max(len(each), 1)
    return {VERTICAL: round(vertical_pct(hub_m, top_m, clean_profile), 2), HORIZONTAL: round(farm, 2)}, each


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
