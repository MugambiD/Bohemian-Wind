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


def test_given_positions_are_read_between_the_cells():
    # a plane rising to the east: a position half-way between two cells stands at the mean of their heights and winds
    c = climate()
    g = tr.from_function(lambda x, y: 1500.0 + 0.05 * x, -1.5, 36.5, half_km=6, res=100)
    wf = fields.wind_field(g, c, T.hub_m, "linear")
    wf.mean_ws = wf.mean_ws * (1 + g.mesh()[0] / 50000.0)             # a wind that rises to the east as well
    on = assess.design(wf, c, None, T, 2, rho_hub=1.0, fixed_xy=([1000.0, 1100.0], [0.0, 0.0]))["positions"]
    mid = assess.design(wf, c, None, T, 1, rho_hub=1.0, fixed_xy=([1050.0], [0.0]))["positions"][0]
    assert mid["ground_elev_m"] == pytest.approx(1552.5, abs=0.01) and mid["slope_pct"] == pytest.approx(5.0, abs=0.01)
    assert on[0]["ground_elev_m"] == pytest.approx(1550.0, abs=0.01) and on[1]["ground_elev_m"] == pytest.approx(1555.0, abs=0.01)
    assert mid["free_ws"] == pytest.approx((on[0]["free_ws"] + on[1]["free_ws"]) / 2, rel=1e-6)


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
    # a layout that leans on wind far above the mast's is called out, with what it is worth without the speed-up
    # (seen on a real escarpment, where the atlas put the best positions far above the mast's wind)
    assert (res["cautions"] != []) == (res["mast_wind_ceiling"]["change_pct"] < -10.0)
    wf.ratio = 1.0 + (wf.ratio - 1.0) * 6.0
    wf.mean_ws = wf.mast_mean_ws * (1.0 + (wf.mean_ws / wf.mast_mean_ws - 1.0) * 6.0)
    res3 = assess.design(wf, c, None, T, 2, lt_factor=1.03, rho_hub=1.0, fixed_xy=([2500.0, 0.0], [0.0, 3000.0]))
    hi = max(p["ratio_to_mast"] for p in res3["positions"])
    drop = -res3["mast_wind_ceiling"]["change_pct"]
    assert hi > 1.15 and drop > 10.0 and len(res3["cautions"]) == 1
    assert f"{drop:.0f} % of the P50 rests on turbine positions being windier than the mast" in res3["cautions"][0]
    assert f"would be {res3['mast_wind_ceiling']['p50_gwh']:.0f} GWh a year" in res3["cautions"][0]
    flat = assess.design(flat_field(c), c, None, T, 2, rho_hub=1.0, fixed_xy=([0.0, 0.0], [0.0, 1500.0]))
    assert flat["cautions"] == []                                               # nothing rests on mapped wind there


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


def test_the_uncertainty_of_carrying_the_wind_depends_on_where_each_turbine_stands():
    assert budget.horizontal_pct(0.0, 0.0, 1.0) == pytest.approx(budget.H_BASE_PCT)            # next to the mast
    assert budget.horizontal_pct(4.0, 0.0, 1.0) == pytest.approx(math.hypot(2.0, 4.0))          # 1 % a km
    assert budget.horizontal_pct(0.0, -100.0, 1.0) == pytest.approx(math.hypot(2.0, 2.0))       # 1 % per 50 m, up or down
    assert budget.horizontal_pct(0.0, 0.0, 1.12) == pytest.approx(math.hypot(2.0, 3.0))         # a quarter of 12 % speed-up
    assert budget.vertical_pct(120.0, 124.0) == 1.0 and budget.vertical_pct(120.0, 80.0) == pytest.approx(3.0)
    near = {"dist_mast_km": 1.0, "dz_m": 0.0, "ratio_to_mast": 1.0}
    far = {"dist_mast_km": 7.0, "dz_m": 0.0, "ratio_to_mast": 1.0}
    terms, each = budget.extrapolation_terms([near, far], [3.0, 1.0], 100.0, 80.0)
    assert each == pytest.approx([math.hypot(2, 1), math.hypot(2, 7)])
    assert terms[budget.HORIZONTAL] == pytest.approx(round((3 * math.hypot(2, 1) + math.hypot(2, 7)) / 4, 2))   # weighted by energy
    assert terms[budget.VERTICAL] == 2.0


def test_the_long_term_term_follows_what_the_campaign_covers():
    assert budget.long_term_pct(12)[0] == 3.0 and budget.long_term_pct(9)[0] == 6.0 and budget.long_term_pct(6)[0] == 6.0
    assert budget.long_term_pct(5)[0] == 10.0 and budget.long_term_pct(None)[0] == 10.0
    assert "only 9 of the twelve" in budget.long_term_pct(9)[1]
    assert budget.long_term_pct(12, corrected=False, months=12)[0] == pytest.approx(6.0)               # one year: year-to-year wind
    assert budget.long_term_pct(12, corrected=False, months=48)[0] == pytest.approx(3.0)               # 6 / sqrt(4)
    assert budget.long_term_pct(12, corrected=False, months=200)[0] == 3.0                             # never below a full year corrected
    assert budget.long_term_pct(8, corrected=False)[0] == 12.0 and budget.long_term_pct(3, corrected=False)[0] == 20.0
    on = budget.speed_terms_for({"pct": 6.0, "corrected": True})
    assert on[budget.LONG_TERM] == 6.0 and on[budget.REFERENCE] == budget.REFERENCE_PCT
    off = budget.speed_terms_for({"pct": 12.0, "corrected": False})
    assert off[budget.LONG_TERM] == 12.0 and budget.REFERENCE not in off                               # no reference to doubt
    assert budget.speed_terms_for(None) == budget.DEFAULT_SPEED_UNCERTAINTY


def test_an_unclean_profile_and_complex_terrain_widen_the_extrapolation_terms():
    assert budget.vertical_pct(120.0, 80.0, clean_profile=False) == pytest.approx(1.0 + 2.5 * 4)
    assert budget.vertical_pct(80.0, 80.0, clean_profile=False) == 1.0                                 # within the heights measured
    assert budget.horizontal_pct(4.0, 0.0, 1.0, complex_terrain=True) == pytest.approx(3 * math.hypot(2.0, 4.0))
    assert not budget.is_complex(2.0, None, 10.0) and budget.is_complex(2.0, 10.5)
    a = {"dist_mast_km": 1.0, "dz_m": 0.0, "ratio_to_mast": 1.0}
    b = {**a, "complex_terrain": True}
    terms, each = budget.extrapolation_terms([a, b], [1.0, 1.0], 120.0, 80.0, clean_profile=False)
    assert each == pytest.approx([math.hypot(2, 1), 3 * math.hypot(2, 1)]) and terms[budget.VERTICAL] == 11.0


def test_steep_ground_round_a_point():
    flat = tr.from_function(lambda x, y: 1000.0 + 0 * x, 0.0, 30.0, half_km=5, res=100)
    assert flat.steep_share_round(0.0, 0.0)[0] == 0.0
    ramp = tr.from_function(lambda x, y: 1000.0 + 0.5 * x, 0.0, 30.0, half_km=5, res=100)               # 50 % everywhere
    assert ramp.steep_share_round([0.0, 2000.0], [0.0, 0.0]) == pytest.approx([100.0, 100.0])
    cliff = tr.from_function(lambda x, y: 1000.0 + 0.5 * np.maximum(x, 0.0), 0.0, 30.0, half_km=5, res=100)
    assert 40.0 < cliff.steep_share_round(0.0, 0.0)[0] < 60.0                                             # steep east half only
    assert cliff.steep_share_round(-4500.0, 0.0, radius_m=500.0)[0] == 0.0


def test_the_barometer_checks_where_the_mast_stands():
    from bohemian_wind.energy.turbine import position_check, reference_air_at
    air = {"t2m_c": 22.0, "ps_kpa": 87.0, "elevation_m": 1300.0, "source": "test reanalysis"}
    expected = reference_air_at(1500.0, 10.0, air)[0] / 100.0
    ok = position_check(expected + 3.0, 98.0, 10.0, 1500.0, air)
    assert ok["caution"] == "" and ok["difference_hpa"] == pytest.approx(3.0, abs=0.05) and abs(ok["height_offset_m"]) < 40
    off = position_check(expected + 20.0, 98.0, 10.0, 1500.0, air)                                     # sits ~200 m lower
    assert off["caution"] and "lower" in off["caution"] and -260 < off["height_offset_m"] < -160
    high = position_check(expected - 15.0, 98.0, None, 1490.0, air)
    assert "higher" in high["caution"] and "not known" in high["caution"]
    assert position_check(expected, 10.0, 10.0, 1500.0, air) is None                                   # too few valid records
    assert position_check(expected, 98.0, 10.0, 1500.0, None) is None                                  # no reanalysis air


def test_a_layout_in_complex_terrain_says_so_and_is_less_certain():
    c = climate()
    flat = flat_field(c, half_km=6)
    g = tr.from_function(lambda x, y: 1500.0 + 0.4 * np.abs(x), -1.5, 36.5, half_km=6, res=100)        # a V-shaped valley, 40 % sides
    steep = fields.wind_field(g, c, T.hub_m, "linear")
    xy = ([1000.0, 2000.0], [0.0, 1000.0])
    a = assess.design(flat, c, None, T, 2, rho_hub=1.0, fixed_xy=xy)
    b = assess.design(steep, c, None, T, 2, rho_hub=1.0, fixed_xy=xy)
    assert a["uncertainty"]["by_position"]["turbines_complex"] == 0 and not any("Complex terrain" in w for w in a["cautions"])
    assert b["uncertainty"]["by_position"]["turbines_complex"] == 2 and all(q["complex_terrain"] for q in b["positions"])
    assert any("Complex terrain round the mast" in w and "independent" in w for w in b["cautions"])
    lt = {"pct": 6.0, "corrected": True, "basis": "test basis"}
    u = assess.design(flat, c, None, T, 2, rho_hub=1.0, fixed_xy=xy, profile_clean=False, long_term=lt)
    bp = u["uncertainty"]["by_position"]
    assert bp["profile_clean"] is False and bp["rule"]["vertical_per_10m_pct"] == 2.5 and bp["long_term_pct_ws"] == 6.0
    assert u["uncertainty"]["sigma_pct"]["long_term"] > a["uncertainty"]["sigma_pct"]["long_term"]
    assert any("not clean" in n for n in u["notes"]) and any("test basis" in n for n in u["notes"])


def test_a_layout_farther_from_the_mast_is_less_certain():
    c = climate()
    wf = flat_field(c, half_km=9)
    near = assess.design(wf, c, None, T, 2, rho_hub=1.0, fixed_xy=([0.0, 1500.0], [1500.0, 0.0]))
    far = assess.design(wf, c, None, T, 2, rho_hub=1.0, fixed_xy=([6000.0, 7500.0], [0.0, 1500.0]))
    for r in (near, far):
        bp = r["uncertainty"]["by_position"]
        assert bp["hub_m"] == 120.0 and bp["top_m"] == c["height_m"]
        assert all(q["horizontal_unc_pct"] > 0 and q["sigma_energy_pct"] > q["horizontal_unc_pct"] for q in r["positions"])
    assert far["uncertainty"]["by_position"]["horizontal_pct_ws"] > near["uncertainty"]["by_position"]["horizontal_pct_ws"] + 3
    assert far["uncertainty"]["sigma_pct"]["long_term"] > near["uncertainty"]["sigma_pct"]["long_term"]
    assert far["uncertainty"]["p90_10_year"] / far["p50_gwh"] < near["uncertainty"]["p90_10_year"] / near["p50_gwh"]
    given = assess.design(wf, c, None, T, 2, rho_hub=1.0, fixed_xy=([6000.0, 7500.0], [0.0, 1500.0]),
                          speed_uncertainty={"all of it": 5.0})                                  # a budget given as a whole is kept
    assert "by_position" not in given["uncertainty"] and list(given["uncertainty"]["terms_energy_pct"])[0] == "all of it"
