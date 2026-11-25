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
    out += _wake_cases(add)
    return out


BENCH_D = 163.0            # rotor of the benchmark turbine, m


def _bench():
    from .energy import wakes  # noqa: PLC0415
    from .energy.turbine import Turbine, generic_model  # noqa: PLC0415
    wt = generic_model(Turbine("Generic 4.5 MW / 163 m", 4500, BENCH_D, 120), 1.225, 0.10)
    cen = np.arange(0.0, 360.0, 30.0)
    A, k = np.full(12, 8.5), np.full(12, 2.2)

    def run(x, y, freq, model="jensen075"):
        site = wakes.site_for(np.ones((len(x), 12)), cen, np.asarray(freq, dtype=float), A, k, 0.10)
        return wakes.simulate(site, wt, np.asarray(x, dtype=float), np.asarray(y, dtype=float), model)
    return wt, run, wakes


def _layouts() -> dict:
    D = BENCH_D
    return {"one turbine": ([0.0], [0.0]), "two in line with the wind, 5 rotors apart": ([0.0, 5 * D], [0.0, 0.0]),
            "two across the wind, 5 rotors apart": ([0.0, 0.0], [0.0, 5 * D]),
            "three rows of three, 7 rotors along and 5 across": ([i * 7 * D for i in range(3) for _ in range(3)],
                                                                 [j * 5 * D for _ in range(3) for j in range(3)])}


def _wake_cases(add) -> list:
    """Energy and wakes on flat ground with the generic 4.5 MW, 163 m turbine and one wind climate (Weibull A 8.5 m/s,
    k 2.2 in every sector). What must hold is known without the program: the energy of one turbine from its curve
    and the wind distribution, no loss where no wake can reach, and the same loss when the layout and the wind are
    both turned round."""
    wt, run, wakes = _bench()
    D = BENCH_D
    even, west, east = np.ones(12) / 12, np.eye(12)[9], np.eye(12)[3]
    lay = _layouts()
    g, n = run(*lay["one turbine"], even)
    v = np.arange(0.005, 40.0, 0.01)
    pdf = (2.2 / 8.5) * (v / 8.5) ** 1.2 * np.exp(-(v / 8.5) ** 2.2)
    hand = float((pdf * np.asarray(wt.power(v), dtype=float) / 1e6).sum() * 0.01 * wakes.HOURS)
    add("Energy, one turbine", "gross energy a year against the power curve summed over the wind distribution in 0.01 m/s steps", "ratio",
        1.0, g[0] / hand, 0.002, "the same curve and Weibull distribution summed by hand, finer than the program's bins")
    add("Wakes, one turbine", "wake loss of a turbine standing alone", "%", 0.0, 100 * (1 - n[0] / g[0]), 1e-9, "nothing stands upwind")
    for model in wakes.WAKE_MODELS:
        g, n = run(*lay["two in line with the wind, 5 rotors apart"], west, model)
        g2, n2 = run(*lay["two in line with the wind, 5 rotors apart"], east, model)
        add(f"Wakes, two in line ({model})", "wake loss of the upwind turbine, wind from the west only", "%", 0.0, 100 * (1 - n[0] / g[0]), 1e-6,
            "nothing stands upwind of it")
        add(f"Wakes, two in line ({model})", "loss of the downwind turbine with the wind from the west, less the other's with the wind from the east", "%",
            0.0, 100 * (1 - n[1] / g[1]) - 100 * (1 - n2[0] / g2[0]), 1e-6, "symmetry: turning the wind round swaps the two")
    g, n = run(*lay["two across the wind, 5 rotors apart"], west, "jensen075")
    add("Wakes, two across the wind (jensen075)", "wake loss of either turbine, wind from the western sector only", "%", 0.0,
        float(100 * (1 - n / g).max()), 1e-9,
        "geometry: within 15 degrees of west the other turbine is at most 1.3 rotors downwind and 4.8 across, outside a wake 0.6 rotors wide")
    g, n = run(*lay["three rows of three, 7 rotors along and 5 across"], west, "jensen075")
    loss = 100 * (1 - n / g)
    add("Wakes, three rows of three (jensen075)", "largest wake loss in the front row, wind from the west only", "%", 0.0, float(loss[:3].max()), 1e-9,
        "nothing stands upwind of the front row")
    add("Wakes, three rows of three (jensen075)", "does the back row lose more than the row before it (1 = yes)", "",
        1.0, 1.0 if loss[7] > loss[4] else 0.0, 0.0, "two turbines upwind take more wind than one")
    wide = ([i * 10 * D for i in range(3) for _ in range(3)], lay["three rows of three, 7 rotors along and 5 across"][1])
    g3, n3 = run(*wide, west, "jensen075")
    add("Wakes, three rows of three (jensen075)", "farm wake loss with the rows 10 rotors apart, over that with 7", "ratio",
        0.6, (1 - n3.sum() / g3.sum()) / (1 - n.sum() / g.sum()), 0.25, "wakes recover with distance: wider rows must lose less")
    return []


def benchmark() -> dict:
    """The figures of the wake benchmark, turbine by turbine and model by model, for anyone to set another
    calculation against: flat ground, the generic 4.5 MW / 163 m turbine at 120 m, air density 1.225 kg/m3,
    turbulence intensity 10 %, Weibull A 8.5 m/s and k 2.2. Positions in metres, x to the east and y to the north."""
    _, run, wakes = _bench()
    roses = {"wind from the western sector only": np.eye(12)[9], "every sector alike": np.ones(12) / 12}
    out = {"turbine": "generic 4.5 MW, 163 m rotor, 120 m hub (bohemian_wind.energy.turbine.generic_model)", "air_density": 1.225,
           "turbulence_intensity": 0.10, "weibull": {"A": 8.5, "k": 2.2}, "sectors": 12, "hours_per_year": wakes.HOURS, "cases": []}
    for name, (x, y) in _layouts().items():
        for rose, f in roses.items():
            for model in wakes.WAKE_MODELS:
                g, n = run(x, y, f, model)
                out["cases"].append({"layout": name, "x_m": [round(v, 1) for v in x], "y_m": [round(v, 1) for v in y], "wind": rose,
                                     "wake_model": model, "gross_mwh": [round(float(v), 1) for v in g],
                                     "after_wakes_mwh": [round(float(v), 1) for v in n],
                                     "wake_loss_pct": [round(float(100 * (1 - b / a)), 3) for a, b in zip(g, n)],
                                     "farm_wake_loss_pct": round(float(100 * (1 - n.sum() / g.sum())), 3)})
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
        (folder / f"wake-benchmark-{__version__}.json").write_text(json.dumps({"version": __version__, **benchmark()}, indent=1) + "\n",
                                                                   encoding="utf-8")
        (folder / "latest.md").write_text(
            f"# Validation cases: results of version {__version__}\n\n"
            "Written by `bohemian-wind validate --write docs/validation`. What the cases are and what they do not show: "
            "[validation.md](../validation.md).\n\n" + table(rows) + "\n\n"
            + "\n".join(f"- **{r['quantity']}**: {r['basis']}." for r in rows) + f"\n\n> **[COMPLIANCE]** {APP_NOTICE}\n", encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
