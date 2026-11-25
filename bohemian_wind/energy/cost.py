"""Cost of energy of a layout: a first figure from a handful of the user's own numbers.

    cost of energy = (capital cost x capital recovery factor + yearly running cost) / yearly energy (P50)

  capital cost     per MW installed, plus the collector cables and access roads at a cost per metre along the
                   shortest tree that links every turbine (and the connection point, when one is given)
  running cost     per MW installed and year
  recovery factor  r (1 + r)^n / ((1 + r)^n - 1) for a discount rate r and a life of n years

The length of the tree is what a layout can change: a turbine moved to a windier cell far from the others earns
more energy and costs more cable and road. With the objective "cost", the fine-tuning of the layout keeps a move
only when the cost of energy falls.

[COMPLIANCE] A developer's first figure for comparing layouts. It is not a financial model: no tax, debt,
inflation, degradation, grid charges or price of the turbine in hand. It must not be used for financing decisions.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


class CostError(ValueError):
    pass


@dataclass
class Costs:
    capex_per_mw: float
    opex_per_mw_year: float = 0.0
    link_per_m: float = 0.0              # collector cable and access road together, per metre of the tree
    years: float = 25.0
    discount_pct: float = 8.0
    currency: str = "USD"
    connection_xy: tuple | None = None   # where the cables and the road leave the site, in local metres

    @property
    def recovery(self) -> float:
        r, n = self.discount_pct / 100.0, self.years
        return 1.0 / n if r <= 0 else r * (1 + r) ** n / ((1 + r) ** n - 1)


def read(raw: dict | None) -> Costs | None:
    """The cost settings of a project, or None when no capital cost is entered."""
    raw = raw or {}
    if raw.get("capex_per_mw") in (None, ""):
        return None

    def num(key, default, lo, hi):
        v = raw.get(key)
        try:
            v = float(default if v in (None, "") else v)
        except (TypeError, ValueError):
            raise CostError(f"The cost setting '{key}' is not a number.") from None
        if not lo <= v <= hi:
            raise CostError(f"The cost setting '{key}' must be between {lo:g} and {hi:g}.")
        return v

    return Costs(num("capex_per_mw", None, 1.0, 1e9), num("opex_per_mw_year", 0.0, 0.0, 1e8), num("link_per_m", 0.0, 0.0, 1e5),
                 num("years", 25.0, 1.0, 60.0), num("discount_pct", 8.0, 0.0, 40.0), str(raw.get("currency") or "USD")[:8])


def tree_length(x, y, root=None) -> float:
    """Length in metres of the shortest tree linking the points (and the root, when given): Prim's algorithm."""
    px, py = list(np.asarray(x, dtype=float)), list(np.asarray(y, dtype=float))
    if root is not None:
        px.append(float(root[0]))
        py.append(float(root[1]))
    px, py = np.array(px), np.array(py)
    n = len(px)
    if n < 2:
        return 0.0
    left = np.ones(n, bool)
    left[0] = False
    dist = np.hypot(px - px[0], py - py[0])
    total = 0.0
    for _ in range(n - 1):
        j = int(np.argmin(np.where(left, dist, np.inf)))
        total += float(dist[j])
        left[j] = False
        dist = np.minimum(dist, np.hypot(px - px[j], py - py[j]))
    return total


def of_layout(c: Costs, rated_mw: float, x, y, p50_mwh: float) -> dict:
    """rated_mw: of the whole wind farm. p50_mwh: its energy a year after every loss."""
    length = tree_length(x, y, c.connection_xy)
    capex = c.capex_per_mw * rated_mw + c.link_per_m * length
    yearly = capex * c.recovery + c.opex_per_mw_year * rated_mw
    return {"currency": c.currency, "capex": float(capex), "link_m": round(length, 0), "link_cost": float(c.link_per_m * length),
            "yearly_cost": float(yearly), "recovery_factor": round(c.recovery, 5),
            "cost_of_energy_per_mwh": float(yearly / p50_mwh) if p50_mwh > 0 else None,
            "inputs": {"capex_per_mw": c.capex_per_mw, "opex_per_mw_year": c.opex_per_mw_year, "link_per_m": c.link_per_m,
                       "years": c.years, "discount_pct": c.discount_pct, "connection_point": c.connection_xy is not None},
            "method": "capital cost times the capital recovery factor, plus the yearly running cost, over the P50 energy; "
                      "cables and roads along the shortest tree linking the turbines"
                      + (" and the connection point" if c.connection_xy is not None else "")}
