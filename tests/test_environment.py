"""Sun position, shadow flicker and noise screening. Run: python -m pytest tests/test_environment.py -q"""
import math
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bohemian_wind import terrain as tr  # noqa: E402
from bohemian_wind.environment import flicker, noise, solar  # noqa: E402

# True (unrefracted) elevation and azimuth from the NREL Solar Position Algorithm, computed once with pvlib.
SPA = [(0.0, 37.0, "2027-03-20T04:00", 5.091, 90.271), (-15.0, 30.0, "2027-06-21T09:00", 48.704, 21.717),
       (52.0, 5.0, "2027-12-21T11:00", 14.106, 171.039), (-30.0, 25.0, "2027-09-10T14:30", 20.242, 288.509),
       (10.0, -5.0, "2031-01-15T16:45", 19.984, 243.094)]


def flat(lat=0.0, lon=0.0, half_km=3.0):
    return tr.from_function(lambda x, y: 1000.0 + 0 * x, lat, lon, half_km=half_km, res=100)


def test_sun_position_against_the_nrel_algorithm():
    for lat, lon, t, el, az in SPA:
        e, a = solar.position(np.array([t], dtype="datetime64[s]"), lat, lon, refraction=False)
        assert e[0] == pytest.approx(el, abs=0.03) and a[0] == pytest.approx(az, abs=0.05)
    low, _ = solar.position(np.array([SPA[0][2]], dtype="datetime64[s]"), 0.0, 37.0, refraction=True)
    assert 0.1 < low[0] - SPA[0][3] < 0.2                       # refraction lifts a sun 5 degrees up by about 0.16 deg
    v = solar.unit_vector(np.array([0.0, 90.0, 0.0]), np.array([90.0, 0.0, 180.0]))
    assert np.allclose(v, [[1, 0, 0], [0, 0, 1], [0, -1, 0]], atol=1e-12)        # east, up, south


def test_flicker_on_the_equator_at_the_equinox():
    """Sun rises due east and climbs 15 deg an hour: a home due west of a turbine sees the sun cross the rotor
    disc, whose apparent diameter fixes the duration."""
    g = flat()
    turb = [{"id": "T01", "x": 0.0, "y": 0.0}]
    homes = [{"name": "west", "x": -500.0, "y": 0.0}, {"name": "north", "x": 0.0, "y": 500.0},
             {"name": "far west", "x": -1200.0, "y": 0.0}]
    hub, rotor = 100.0, 100.0
    one_day = flicker.at_receptors(g, turb, homes, hub, rotor, 2027, step_min=1, days=[78])      # 20 March
    by = {r["name"]: r for r in one_day["receptors"]}
    d = math.hypot(500.0, hub - 2.0)
    expected_min = 2 * math.degrees(math.asin(rotor / 2 / d)) / 15.0 * 60.0
    assert by["west"]["max_minutes_per_day"] == pytest.approx(expected_min, abs=2.0)
    assert by["north"]["hours_per_year"] == 0 and by["far west"]["hours_per_year"] == 0     # beyond 10 rotor diameters
    assert by["west"]["turbines"] == ["T01"] and one_day["with_any_flicker"] == 1
    # whole year at 5-minute steps: flicker only around the two equinoxes
    year = flicker.at_receptors(g, turb, homes[:1], hub, rotor, 2027, step_min=5)
    r = year["receptors"][0]
    assert 20 < r["hours_per_year"] < 45 and 35 < r["days_with_flicker"] < 75
    assert r["over_limit"] and r["over_day_limit"] and year["over_limit"] == 1
    assert year["per_turbine_hours"]["T01"] == pytest.approx(r["hours_per_year"], abs=0.1)
    assert 0 < year["shutdown_upper_bound_pct"] < 1.0
    # a higher minimum sun elevation removes low-sun flicker
    none = flicker.at_receptors(g, turb, homes[:1], hub, rotor, 2027, step_min=5, days=[78], min_sun_elevation=20.0)
    assert none["receptors"][0]["hours_per_year"] == 0
    assert len(flicker.year_times(2028, 60)) == 366 * 24 and len(flicker.year_times(2027, 10, days=[0, 1])) == 288


def test_noise_known_answer_on_flat_ground():
    lw, hub, hr, dp = 105.0, 120.0, 4.0, 500.0
    d = math.hypot(dp, hub - hr)
    a_div = 20 * math.log10(d) + 11
    a_atm = 1.9 * d / 1000
    hm = (hub + hr) / 2                                           # flat ground: mean height of a straight path
    a_gr = max(4.8 - (2 * hm / d) * (17 + 300 / d), 0.0)
    d_om = 10 * math.log10(1 + (dp ** 2 + (hub - hr) ** 2) / (dp ** 2 + (hub + hr) ** 2))
    expected = lw + d_om - a_div - a_atm - a_gr
    g = flat()
    res = noise.at_receptors(g, [{"id": "T01", "x": 0.0, "y": 0.0}], [{"name": "home", "x": dp, "y": 0.0}], hub, lw)
    row = res["receptors"][0]
    assert row["level_dba"] == pytest.approx(expected, abs=0.06) and 40.0 < expected < 43.0
    assert row["nearest_turbine"] == "T01" and row["distance_m"] == 500
    assert not row["over_limit"] and row["level_plus_uncertainty_dba"] == pytest.approx(row["level_dba"] + 2.0, abs=0.01)
    # two equal sources at the same distance: +3 dB
    two = noise.at_receptors(g, [{"id": "A", "x": 0.0, "y": 0.0}, {"id": "B", "x": 1000.0, "y": 0.0}],
                             [{"name": "home", "x": 500.0, "y": 0.0}], hub, lw)
    assert two["receptors"][0]["level_dba"] == pytest.approx(expected + 3.01, abs=0.1)
    assert noise.add_levels([40.0, 40.0]) == pytest.approx(43.0103, abs=1e-3)
    # louder turbine, closer home: over a 45 dB(A) limit; the limit is the caller's
    near = noise.at_receptors(g, [{"id": "T01", "x": 0.0, "y": 0.0}], [{"name": "home", "x": 300.0, "y": 0.0}], hub, 107.0)
    assert near["over_limit"] == 1 and near["highest_dba"] > 45
    assert noise.at_receptors(g, [{"id": "T01", "x": 0.0, "y": 0.0}], [{"name": "home", "x": 300.0, "y": 0.0}], hub, 107.0,
                              limit=55.0)["over_limit"] == 0


def test_noise_map_agrees_with_receptor_figures_and_falls_with_distance():
    g = flat()
    t = [{"id": "T01", "x": 0.0, "y": 0.0}]
    m = noise.on_grid(g, t, 120.0, 105.0)
    r, c = g.index(np.array([500.0, 1000.0, 2000.0]), np.array([0.0, 0.0, 0.0]))
    at = noise.at_receptors(g, t, [{"name": "a", "x": 500.0, "y": 0.0}, {"name": "b", "x": 1000.0, "y": 0.0},
                                   {"name": "c", "x": 2000.0, "y": 0.0}], 120.0, 105.0)
    lv = {q["name"]: q["level_dba"] for q in at["receptors"]}
    assert m[r[0], c[0]] == pytest.approx(lv["a"], abs=0.2) and m[r[2], c[2]] == pytest.approx(lv["c"], abs=0.2)
    assert lv["a"] > lv["b"] > lv["c"] and 5.5 < lv["b"] - lv["c"] < 9.5       # about 6 dB a doubling, plus air and ground
    # a home on a hilltop facing the turbine across a valley: more height under the path, less ground attenuation
    valley = tr.from_function(lambda x, y: 1000.0 + 60.0 * np.exp(-((x - 1500.0) / 300.0) ** 2), 0.0, 0.0, half_km=3, res=50)
    hill = noise.at_receptors(valley, t, [{"name": "hilltop", "x": 1500.0, "y": 0.0}], 120.0, 105.0)["receptors"][0]
    flat15 = noise.at_receptors(g, t, [{"name": "flat", "x": 1500.0, "y": 0.0}], 120.0, 105.0)["receptors"][0]
    assert abs(hill["level_dba"] - flat15["level_dba"]) < 2.5


def test_noise_for_thousands_of_homes_matches_the_sum_pair_by_pair():
    """Homes are worked through in blocks of arrays. Each level must equal the one built up one turbine at a time
    over the terrain profile, as the method is written."""
    hills = tr.from_function(lambda x, y: 900.0 + 80.0 * np.sin(x / 700.0) * np.cos(y / 900.0) + 0.02 * x, 0.0, 0.0, half_km=4, res=50)
    turbines = [{"id": "T1", "x": -600.0, "y": 300.0}, {"id": "T2", "x": 200.0, "y": -100.0}, {"id": "T3", "x": 900.0, "y": 650.0}]
    rng = np.random.default_rng(3)
    homes = [{"name": f"H{i:04d}", "x": float(x), "y": float(y)} for i, (x, y) in enumerate(rng.uniform(-3800, 3800, (7000, 2)))]
    res = noise.at_receptors(hills, turbines, homes, 100.0, 105.0, limit=40.0)        # 7,000 homes: three blocks
    assert res["n_receptors"] == 7000 and [r["level_dba"] for r in res["receptors"]] == sorted((r["level_dba"] for r in res["receptors"]), reverse=True)
    rows = {r["name"]: r for r in res["receptors"]}
    for k in (0, 1, 3332, 3333, 3334, 6665, 6666, 6999):                               # both sides of every block edge
        h = homes[k]
        zr = float(hills.sample(h["x"], h["y"])) + noise.RECEIVER_HEIGHT
        each, dists = [], []
        for t in turbines:
            zs = float(hills.sample(t["x"], t["y"])) + 100.0
            dp = math.hypot(t["x"] - h["x"], t["y"] - h["y"])
            hm = noise._mean_path_height(hills, t["x"], t["y"], zs, h["x"], h["y"], zr)
            assert isinstance(hm, float)
            each.append(float(noise.level(105.0, math.hypot(dp, zs - zr), dp, 100.0, noise.RECEIVER_HEIGHT, hm)))
            dists.append(dp)
        row = rows[h["name"]]
        assert row["level_dba"] == pytest.approx(float(noise.add_levels(each)), abs=0.051)
        assert row["loudest_single_dba"] == pytest.approx(max(each), abs=0.051)
        assert row["distance_m"] == round(min(dists)) and row["nearest_turbine"] == turbines[int(np.argmin(dists))]["id"]
    assert res["nearest_home_m"] == min(r["distance_m"] for r in res["receptors"])
    assert res["over_limit"] == sum(r["level_dba"] > 40.0 for r in res["receptors"]) > 0


def test_homes_out_of_range_of_every_turbine_get_no_flicker_and_cost_nothing():
    g = flat()
    t = [{"id": "T01", "x": 0.0, "y": 0.0}]
    homes = [{"name": "near", "x": 300.0, "y": 0.0}] + [{"name": f"far{i}", "x": 2500.0, "y": -2000.0 + i} for i in range(3000)]
    res = flicker.at_receptors(g, t, homes, 100.0, 120.0, 2027, step_min=10)          # range: 10 rotors = 1,200 m
    assert res["n_receptors"] == 3001 and res["with_any_flicker"] == 1 and res["receptors"][0]["name"] == "near"
    far = res["receptors"][-1]
    assert far["hours_per_year"] == 0.0 and far["turbines"] == [] and not far["over_limit"] and far["nearest_turbine_m"] > 2500
    alone = flicker.at_receptors(g, t, homes[:1], 100.0, 120.0, 2027, step_min=10)     # the near home: the same with or without the others
    assert alone["receptors"][0] == res["receptors"][0] and alone["per_turbine_hours"] == res["per_turbine_hours"]
