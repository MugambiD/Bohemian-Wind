"""The public validation set: invented cases whose answers are known without the program.

    bohemian-wind validate                 run the cases and print the table
    bohemian-wind validate --write DIR     also write DIR/<version>.json and DIR/latest.md

Each case sets a figure of the program against a figure worked out another way: from geometry, from the formula of
the published method written out afresh, or from arithmetic by hand. The cases use invented positions on flat
ground; no measurement, site or client is in them. The results of every version are kept in docs/validation/, so
that a change in any figure between versions is on record.

What this set does not show: how close the program comes to measurements or to another program on a real site.
Comparisons of that kind need data whose owner has agreed to their publication, and none is published yet.

[COMPLIANCE] Passing these cases says the calculations do what their method says. It does not make any result an
accredited or bankable assessment.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

from . import APP_NOTICE, __version__


def _flat():
    from . import terrain  # noqa: PLC0415
    return terrain.from_function(lambda x, y: 0.0 * x, 0.0, 37.0, half_km=3.0, res=100.0, source="flat, invented")


def cases() -> list:
    """[{"case", "quantity", "unit", "expected", "got", "tolerance", "basis"}, ...]"""
    from .energy import cost  # noqa: PLC0415
    from .environment import flicker, modes, noise  # noqa: PLC0415
    out = []

    def add(case, quantity, unit, expected, got, tol, basis):
        out.append({"case": case, "quantity": quantity, "unit": unit, "expected": round(float(expected), 4),
                    "got": round(float(got), 4), "tolerance": tol, "basis": basis,
                    "passed": bool(abs(float(got) - float(expected)) <= tol)})

    g = _flat()
    hub, rotor = 100.0, 100.0
    t1 = [{"id": "T1", "x": 0.0, "y": 0.0}]
    west = [{"name": "west", "x": -500.0, "y": 0.0}]

    # 1 shadow flicker, worst case: on the equator at the equinox the sun climbs 15 degrees an hour through the east
    day = flicker.at_receptors(g, t1, west, hub, rotor, 2027, step_min=1, days=[78])
    d = math.hypot(500.0, hub - flicker.EYE_HEIGHT)
    add("Shadow flicker, worst case", "minutes in flicker on 20 March, home 500 m west of one turbine on the equator", "min",
        2 * math.degrees(math.asin(rotor / 2 / d)) / 15.0 * 60.0, day["receptors"][0]["max_minutes_per_day"], 2.0,
        "geometry: the rotor's apparent diameter divided by the sun's 15 degrees an hour")

    # 2-4 shadow flicker, expected case
    cen = np.arange(0.0, 360.0, 30.0)
    east, north = np.eye(12)[3], np.eye(12)[0]
    year = dict(step_min=5)
    facing = flicker.at_receptors(g, t1, west, hub, rotor, 2027, expected={"centres": cen, "freq": east}, **year)
    edge = flicker.at_receptors(g, t1, west, hub, rotor, 2027, expected={"centres": cen, "freq": north}, **year)
    part = flicker.at_receptors(g, t1, west, hub, rotor, 2027, **year,
                                expected={"centres": cen, "freq": east, "sunshine": 0.5, "run": 0.8})
    e_face = facing["receptors"][0]["expected_hours_per_year"]
    add("Shadow flicker, expected case", "hours a year with the wind always from the north (rotor edge-on to the home)", "h",
        0.0, edge["receptors"][0]["expected_hours_per_year"], 0.0, "geometry: a disc seen edge-on casts no shadow on the home")
    add("Shadow flicker, expected case", "hours with half the sunshine and running 80 % of the time, over hours with all of both", "ratio",
        0.4, part["receptors"][0]["expected_hours_per_year"] / e_face, 0.005, "arithmetic: 0.5 x 0.8")
    add("Shadow flicker, expected case", "hours with the rotor always facing the home, over the worst case", "ratio",
        1.0, e_face / facing["receptors"][0]["hours_per_year"], 0.08,
        "geometry: a disc facing the home fills nearly the sphere the worst case assumes")
    budget = flicker.at_receptors(g, t1, west, hub, rotor, 2027, expected={"centres": cen, "freq": east}, budget_hours=10.0, **year)
    add("Shadow flicker, budget", "hours the turbine is stopped to bring the home down to 10 expected hours", "h",
        e_face - 10.0, budget["budget"]["per_turbine_stop_hours"]["T1"], 0.15, "arithmetic: expected hours less the budget")

    # 5-6 noise: ISO 9613-2 written out afresh for one turbine on flat ground
    lw, hs, hr, dp = 105.0, hub, noise.RECEIVER_HEIGHT, 500.0
    dd = math.hypot(dp, hs - hr)
    hm = (hs + hr) / 2.0
    hand = (lw + 10 * math.log10(1 + (dp ** 2 + (hs - hr) ** 2) / (dp ** 2 + (hs + hr) ** 2))
            - (20 * math.log10(dd) + 11) - noise.ALPHA_500HZ * dd / 1000.0 - max(4.8 - (2 * hm / dd) * (17 + 300 / dd), 0.0))
    one = noise.at_receptors(g, t1, west, hub, lw)
    add("Noise, ISO 9613-2", "level 500 m from one turbine of 105 dB(A), flat ground", "dB(A)", hand, one["receptors"][0]["level_dba"], 0.06,
        "the method's formula written out by hand: spreading, air absorption, ground, reflection near the source")
    two = noise.at_receptors(g, t1 + [{"id": "T2", "x": 0.0, "y": 1.0}], west, hub, lw)
    add("Noise, ISO 9613-2", "two equal turbines at one place against one", "dB", 10 * math.log10(2.0),
        two["receptors"][0]["level_dba"] - one["receptors"][0]["level_dba"], 0.11, "arithmetic: 10 log10(2)")

    # 7 quiet modes: one turbine, one home 2 dB over the limit, modes 3 and 6 dB quieter
    prop = noise.propagation(g, t1, west, hub)
    limit = float(np.round(prop[0, 0] + lw, 1)) - 2.0
    table = modes.read_modes([{"name": "A", "sound_power_dba": lw - 3, "energy_pct": 96}, {"name": "B", "sound_power_dba": lw - 6, "energy_pct": 88}], lw)
    q = modes.strategy(prop, limit, table)
    add("Noise, quiet modes", "energy given up to bring a home 2 dB over the limit within it (modes: 3 dB for 4 %, 6 dB for 12 %)", "%",
        4.0, q["energy_loss_pct"], 0.0, "by hand: the 3 dB mode is enough and costs 4 %")

    # 8-10 cost of energy
    sq = ([0.0, 1000.0, 1000.0, 0.0], [0.0, 0.0, 1000.0, 1000.0])
    add("Cost of energy", "shortest tree linking the corners of a 1,000 m square", "m", 3000.0, cost.tree_length(*sq), 0.0,
        "geometry: three sides")
    c = cost.Costs(capex_per_mw=1_400_000, opex_per_mw_year=40_000, link_per_m=400, years=25, discount_pct=8)
    crf = 0.08 * 1.08 ** 25 / (1.08 ** 25 - 1)
    add("Cost of energy", "capital recovery factor, 8 % over 25 years", "", crf, c.recovery, 1e-9, "the annuity formula written out by hand")
    res = cost.of_layout(c, 18.0, *sq, p50_mwh=60_000.0)
    add("Cost of energy", "four 4.5 MW turbines on that square, 60,000 MWh a year", "per MWh",
        ((1_400_000 * 18 + 400 * 3000) * crf + 40_000 * 18) / 60_000.0, res["cost_of_energy_per_mwh"], 1e-6,
        "arithmetic by hand from the same inputs")
    return out


def table(rows: list) -> str:
    lines = ["| Case | Quantity | Expected | Got | Within | Result |", "|---|---|---|---|---|---|"]
    for r in rows:
        unit = f" {r['unit']}" if r["unit"] else ""
        lines.append(f"| {r['case']} | {r['quantity']} | {r['expected']:g}{unit} | {r['got']:g}{unit} | {r['tolerance']:g} | "
                     f"{'pass' if r['passed'] else 'FAIL'} |")
    return "\n".join(lines)


def main(argv=None) -> int:
    argv = list(argv or [])
    rows = cases()
    ok = all(r["passed"] for r in rows)
    print(f"Bohemian Wind {__version__}: validation cases with known answers\n")
    print(table(rows))
    print(f"\n{sum(r['passed'] for r in rows)} of {len(rows)} passed.\n{APP_NOTICE}")
    if "--write" in argv:
        folder = Path(argv[argv.index("--write") + 1])
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{__version__}.json").write_text(json.dumps({"version": __version__, "cases": rows}, indent=1) + "\n", encoding="utf-8")
        (folder / "latest.md").write_text(
            f"# Validation cases: results of version {__version__}\n\n"
            "Written by `bohemian-wind validate --write docs/validation`. What the cases are and what they do not show: "
            "[validation.md](../validation.md).\n\n" + table(rows) + "\n\n"
            + "\n".join(f"- **{r['quantity']}**: {r['basis']}." for r in rows) + f"\n\n> **[COMPLIANCE]** {APP_NOTICE}\n", encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
