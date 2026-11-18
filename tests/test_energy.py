"""Layout, wakes, energy yield, losses and uncertainty. Needs PyWake. Run: python -m pytest tests/test_energy.py -q"""
import math
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytest.importorskip("py_wake")

from bohemian_wind import climate as cl  # noqa: E402
from bohemian_wind import terrain as tr  # noqa: E402
from bohemian_wind.energy import EnergyError, assess, budget, placement, wakes  # noqa: E402
from bohemian_wind.energy.turbine import PRESETS, Turbine, air_density, generic_model  # noqa: E402
from bohemian_wind.flow import fields  # noqa: E402

T = Turbine("Generic 4.5 MW / 163 m", 4500, 163, 120)


def climate(mean=7.5, k=2.2, alpha=0.14, height=80.0, peak=3):
    """East wind (sector 3 = 90 deg) half the time, the rest spread evenly."""
    f = np.full(12, 0.5 / 11)
    f[peak] = 0.5
    A = mean / float(cl.weibull_mean(1.0, k))
    return {"height_m": height, "n_sectors": 12, "shear": {"alpha": alpha, "basis": "test"},
            "ti": {"mean_above_3": 0.12},
            "sectors": [{"centre": 30.0 * i, "freq": float(f[i]), "A": A, "k": k, "mean": mean, "records": 1000}
                        for i in range(12)]}


def flat_field(c, half_km=6):
    g = tr.from_function(lambda x, y: 1500.0 + 0 * x, -1.5, 36.5, half_km=half_km, res=100)
    return fields.wind_field(g, c, T.hub_m, "linear")


def test_turbine_basics():
    assert T.specific_power == pytest.approx(4.5e6 / (math.pi * 163 ** 2 / 4)) and T.tip_height == 201.5
    assert all(p.name.startswith("Generic") for p in PRESETS.values())            # no manufacturer names or curves
    rho, src = air_density(1900.0, 120.0)
    assert rho == pytest.approx(1.225 * (1 - 2.25577e-5 * 2020) ** 4.25588) and "standard atmosphere" in src
    rho2, src2 = air_density(1900.0, 120.0, measured=0.95, measured_height_m=10.0)
    assert rho2 == pytest.approx(0.95 * math.exp(-110 / 10500)) and "measured" in src2
    wt = generic_model(T, 1.0, 0.12)
    p = wt.power(np.array([2.0, 8.0, 14.0, 26.0])) / 1e3
    assert p[0] == 0 and 1500 < p[1] < 3500 and p[2] == pytest.approx(4500, rel=0.03) and p[3] == 0


def test_free_stream_energy_matches_pywake():
    c = climate()
    wf = flat_field(c)
    res = assess.design(wf, c, np.ones(wf.grid.shape, bool), T, 1, rho_hub=1.0, refine=False)
    _, f, A, k = cl.at_height(c, T.hub_m)
    tables = assess.sector_energy_tables(generic_model(T, 1.0, 0.12), f, A, k)
    own = assess.free_stream_energy(tables, np.ones((12, 1)))[0]
    assert res["positions"][0]["gross_mwh"] == pytest.approx(own, rel=0.003)       # own integral against PyWake
    assert res["wake_loss_pct"] == pytest.approx(0.0, abs=1e-6)
    assert res["p50_gwh"] == pytest.approx(own / 1000 * budget.loss_factor(), rel=0.003)
    assert res["capacity_factor"] == pytest.approx(res["p50_gwh"] * 1000 / (4.5 * wakes.HOURS))


def test_wake_behind_a_turbine_and_none_beside_it():
    c = climate()
    wf = flat_field(c)
    d = T.rotor_m
    row = assess.design(wf, c, None, T, 2, rho_hub=1.0, fixed_xy=([0.0, -5 * d], [0.0, 0.0]))   # second is downwind (west)
    side = assess.design(wf, c, None, T, 2, rho_hub=1.0, fixed_xy=([0.0, 0.0], [0.0, 5 * d]))   # side by side
    up, down = row["positions"][0], row["positions"][1]
    # east wind half the time: the western turbine sits in the wake often, the eastern one rarely
    assert down["wake_loss_pct"] > 4.0 and up["wake_loss_pct"] < 1.5 and down["wake_loss_pct"] > 5 * up["wake_loss_pct"]
    assert side["wake_loss_pct"] < row["wake_loss_pct"] / 3
    assert row["gross_gwh"] == pytest.approx(side["gross_gwh"], rel=1e-6)
    for m in ("jensen075", "turbopark"):
        assert row["wake_checks"][m]["wake_loss_pct"] > 2.0
    with pytest.raises(EnergyError):
        assess.design(wf, c, None, T, 1, fixed_xy=([99999.0], [0.0]))


def test_placement_rules():
    rng = np.random.default_rng(0)
    xs, ys = rng.uniform(-3000, 3000, 4000), rng.uniform(-3000, 3000, 4000)
    value = 100 - np.hypot(xs, ys) / 100
    idx = placement.greedy(xs, ys, value, 150.0, 8, theta=90.0, cross_d=3, along_d=7, link_m=2000.0)
    assert len(idx) == 8 and value[idx[0]] == value.max()
    for a in idx:
        for b in idx:
            if a != b:
                al, cr = placement.along_cross(xs[b] - xs[a], ys[b] - ys[a], 90.0)
                assert (cr / 450.0) ** 2 + (al / 1050.0) ** 2 >= 1.0
    assert placement.connected_set(xs[idx], ys[idx], 2000.0)
    assert not placement.connected_set(np.array([0.0, 5000.0]), np.array([0.0, 0.0]), 2000.0)
    assert placement.min_spacing_d(np.array([0.0, 600.0]), np.array([0.0, 0.0]), 150.0) == 4.0
    al, cr = placement.along_cross(1000.0, 0.0, 90.0)          # 1 km east, wind from the east: all along-wind
    assert al == pytest.approx(1000.0) and cr == pytest.approx(0.0, abs=1e-9)
    few = placement.greedy(np.array([0.0, 10.0]), np.array([0.0, 0.0]), np.array([2.0, 1.0]), 150.0, 5, 90.0)
    assert list(few) == [0]                                    # the second cell breaks the spacing rule


def test_design_on_a_hill_end_to_end():
    c = climate()
    g = tr.from_function(lambda x, y: 1500.0 + 90.0 * np.exp(-((x - 2500) ** 2 + (y / 2.5) ** 2) / 700.0 ** 2), -1.5, 36.5,
                         half_km=8, res=100)
    wf = fields.wind_field(g, c, T.hub_m, "linear", lt_factor=1.03, keep_half_m=6000)
    X, Y = wf.grid.mesh()
    mask = (np.hypot(X, Y) <= 5500) & (wf.grid.slope_pct <= 15)
    mask &= ~((np.abs(X - 2500) < 300) & (np.abs(Y) < 300))                     # an exclusion zone on the summit
    logs = []
    res = assess.design(wf, c, mask, T, 6, lt_factor=1.03, rho_hub=1.0, refine=True, refine_seconds=40, log=logs.append)
    assert res["n_placed"] == 6 and res["capacity_mw"] == 27.0
    xs = np.array([p["x"] for p in res["positions"]]); ys = np.array([p["y"] for p in res["positions"]])
    assert mask[wf.grid.index(xs, ys)].all()                                    # every turbine on a buildable cell
    assert not ((np.abs(xs - 2500) < 300) & (np.abs(ys) < 300)).any()
    assert np.mean(xs) > 1000                                                   # drawn to the hill
    assert res["spacing"]["closest_pair_d"] >= 3.0 - 1e-9
    assert res["gross_gwh"] > res["after_wake_gwh"] > res["p50_gwh"] > 0
    assert res["other_loss_pct"] == pytest.approx(100 * (1 - budget.loss_factor()))
    u = res["uncertainty"]
    assert res["p50_gwh"] > u["p75_10_year"] > u["p90_10_year"] > u["p90_1_year"] > 0
    assert 1.0 < res["energy_per_speed"] < 3.0
    assert res["mast_wind_ceiling"]["p50_gwh"] < res["p50_gwh"] and res["mast_wind_ceiling"]["change_pct"] < 0
    assert res["prevailing_deg"] == pytest.approx(90.0, abs=5.0)
    assert [p["id"] for p in res["positions"]] == [f"T{i:02d}" for i in range(1, 7)]
    assert res["positions"][0]["y"] >= res["positions"][-1]["y"]                # listed north to south
    assert 0.2 < res["capacity_factor"] < 0.7 and any("Placed 6 turbines" in m for m in logs)
    # asking for more than fits is said, not hidden
    tiny = np.zeros_like(mask); tiny[wf.grid.index(0.0, 0.0)] = True
    res2 = assess.design(wf, c, tiny, T, 3, lt_factor=1.03, rho_hub=1.0, refine=False)
    assert res2["n_placed"] == 1 and any("Only 1 of the 3" in n for n in res2["notes"])
    with pytest.raises(EnergyError):
        assess.design(wf, c, np.zeros_like(mask), T, 3)
    with pytest.raises(EnergyError):
        assess.design(wf, c, mask, Turbine("x", 4500, 163, 100), 3)             # wind field at another height


def test_losses_and_uncertainty_arithmetic():
    assert budget.loss_factor() == pytest.approx(0.97 * 0.995 * 0.99 * 0.975 * 0.99 * 0.99 * 0.995)
    assert budget.loss_factor([{"item": "a", "pct": 10}, {"item": "b", "pct": 10}]) == pytest.approx(0.81)
    u = budget.uncertainty(100.0, 2.0, {"speed": 3.0}, {"curve": 8.0}, iav_speed=5.0)
    assert u["sigma_pct"]["long_term"] == pytest.approx(10.0)                   # sqrt(6^2 + 8^2)
    assert u["sigma_pct"]["1_year"] == pytest.approx(math.sqrt(200.0), abs=0.01)   # + (5 x 2)^2
    assert u["sigma_pct"]["10_year"] == pytest.approx(math.sqrt(110.0), abs=0.01)
    assert u["p90_long_term"] == pytest.approx(100 * (1 - 1.2816 * 0.10))
    assert u["p75_long_term"] == pytest.approx(100 * (1 - 0.6745 * 0.10))
    assert set(budget.DEFAULT_SPEED_UNCERTAINTY) and len(budget.losses_table()) == 7
