"""Wind climate hand-off: sector Weibulls, shear, monthly means. Run: python -m pytest tests/test_climate.py -q"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bohemian_wind import climate as cl  # noqa: E402


def fake_analysis(n=144 * 400, seed=3):
    """Two sectors with known Weibulls: 70 % of the time from 90 deg (A 9, k 2.5), 30 % from 270 deg (A 6, k 2)."""
    rng = np.random.default_rng(seed)
    t = pd.date_range("2023-01-01", periods=n, freq="10min")
    east = rng.random(n) < 0.7
    ws = np.where(east, 9.0 * rng.weibull(2.5, n), 6.0 * rng.weibull(2.0, n))
    wd = np.where(east, 90.0, 270.0) + rng.uniform(-10, 10, n)
    ws80 = pd.Series(ws, index=t)
    ws60 = ws80 * (60 / 80) ** 0.2
    wd = pd.Series(wd % 360, index=t)
    sd = ws80 * 0.12
    hs = {80.0: {"ws": ws80, "sd": sd, "wd": wd}, 60.0: {"ws": ws60, "sd": sd, "wd": wd}}
    an = {"heights": hs, "top_height": 80.0, "shear": {"alpha_top_bottom": 0.2, "lo": 60.0, "hi": 80.0},
          "density": {"mean": 1.05, "sensor_height": 10.0}}
    tq = {"interval_s": 600, "expected": n, "start": t[0], "end": t[-1]}
    return an, tq


def test_sector_weibulls_are_recovered():
    an, tq = fake_analysis()
    c = cl.from_analysis(an, tq, {"name": "Test", "lat": -1.0, "lon": 36.0, "elevation_m": 1500})
    centres, f, A, k = cl.sector_arrays(c)
    i_e, i_w = list(centres).index(90.0), list(centres).index(270.0)
    assert f[i_e] == pytest.approx(0.7, abs=0.01) and f[i_w] == pytest.approx(0.3, abs=0.01)
    assert A[i_e] == pytest.approx(9.0, rel=0.02) and k[i_e] == pytest.approx(2.5, rel=0.03)
    assert A[i_w] == pytest.approx(6.0, rel=0.02) and k[i_w] == pytest.approx(2.0, rel=0.03)
    assert f.sum() == pytest.approx(1.0)
    # the sector climate reproduces the measured mean
    assert cl.mean_speed(f, A, k) == pytest.approx(c["mean_ws"], rel=0.005)
    assert c["shear"]["alpha"] == pytest.approx(0.2) and c["density"]["mean"] == 1.05
    assert c["ti"]["mean_above_3"] == pytest.approx(0.12, abs=1e-6)
    assert c["calendar_months"] == 12 and c["momm_ws"] is not None
    assert all(v["share"] > 0.99 for p, v in c["monthly"].items() if p != max(c["monthly"]))


def test_height_and_long_term_scaling():
    an, tq = fake_analysis()
    c = cl.from_analysis(an, tq)
    _, f, A, k = cl.at_height(c, 120.0, factor=1.05)
    _, f0, A0, k0 = cl.sector_arrays(c)
    assert np.allclose(A / A0, 1.05 * (120 / 80) ** 0.2) and np.allclose(k, k0) and np.allclose(f, f0)
    c["shear"]["alpha"] = None
    with pytest.raises(ValueError):
        cl.at_height(c, 120.0)
    assert np.allclose(cl.at_height(c, 80.0)[2], A0)


def test_degree_expansion_keeps_sectors_whole_and_rotates():
    an, tq = fake_analysis()
    c = cl.from_analysis(an, tq)
    centres, f, A, k = cl.sector_arrays(c)
    deg, fd, Ad, kd = cl.expand_degrees(centres, f, A, k)
    assert fd.sum() == pytest.approx(1.0) and len(deg) == 360
    assert Ad[90] == A[3] and Ad[76] == A[3] and Ad[104] == A[3] and Ad[105] == A[4]   # sector 3 spans 75..104
    assert cl.mean_speed(fd, Ad, kd) == pytest.approx(cl.mean_speed(f, A, k))
    _, fr, Ar, _ = cl.expand_degrees(centres, f, A, k, rotate_deg=40)
    assert Ar[130] == A[3] and fr.sum() == pytest.approx(1.0)
    assert cl.energy_direction(centres, f, A, k) == pytest.approx(90.0, abs=3.0)


def test_sparse_sector_borrows_the_overall_shape_and_no_vane_case():
    an, tq = fake_analysis(n=144 * 60)
    c = cl.from_analysis(an, tq)
    empty = [s for s in c["sectors"] if s["records"] == 0]
    assert empty and all(s["freq"] == 0 and s["k"] == c["weibull_k"] for s in empty)
    for h in an["heights"].values():
        h["wd"] = None
    c2 = cl.from_analysis(an, tq)
    assert c2["no_direction"] and all(s["freq"] == pytest.approx(1 / 12) for s in c2["sectors"])


def test_round_trip(tmp_path):
    an, tq = fake_analysis(n=144 * 90)
    c = cl.from_analysis(an, tq, {"name": "T"})
    cl.save(tmp_path / "climate.json", c)
    assert cl.load(tmp_path / "climate.json") == c
    assert len(cl.monthly_series(c)) >= 2


def test_shear_pair_must_be_far_enough_apart_and_odd_exponents_are_flagged():
    import pandas as pd
    # two cups 4 m apart near the top: the pair below them carries the shear instead
    means = pd.Series({60.0: 7.10, 90.0: 7.62, 116.0: 8.02, 120.0: 8.05})
    sh = {"heights": [60.0, 90.0, 116.0, 120.0], "means": means, "alpha_top_bottom": 0.18, "lo": 60.0, "hi": 120.0,
          "top_pair": (116.0, 120.0), "alpha_top_pair": float(np.log(8.05 / 8.02) / np.log(120 / 116))}
    alpha, basis = cl.extrapolation_shear(sh)
    assert basis == "90-120 m pair" and alpha == pytest.approx(np.log(8.05 / 7.62) / np.log(120 / 90))
    # three well-spaced heights: the top two, as before
    sh = {"heights": [40.0, 60.0, 80.0], "means": pd.Series({40.0: 6.0, 60.0: 6.4, 80.0: 6.7}), "alpha_top_bottom": 0.16,
          "lo": 40.0, "hi": 80.0}
    alpha, basis = cl.extrapolation_shear(sh)
    assert basis == "60-80 m pair" and alpha == pytest.approx(np.log(6.7 / 6.4) / np.log(80 / 60))
    # only close heights: said, not hidden
    sh = {"heights": [97.0, 100.0], "means": pd.Series({97.0: 7.0, 100.0: 7.03}), "alpha_top_bottom": 0.14, "lo": 97.0, "hi": 100.0}
    assert cl.extrapolation_shear(sh) == (0.14, "97-100 m pair, heights close together")
    assert cl.extrapolation_shear({}) == (None, "one measurement height: no shear measured")


def test_a_profile_that_is_not_a_clean_power_law_is_flagged():
    # seen on two real masts: a top pair far steeper than any normal profile on one; on the other a top pair far
    # from the fit over all heights, with the lowest cup reading more than the two above it
    an, tq = fake_analysis()
    ws80 = an["heights"][80.0]["ws"]
    for name, factors, words in (("clean", {60.0: 0.950, 45.0: 0.900, 30.0: 0.840}, []),
                                 ("steep top", {60.0: 0.89, 45.0: 0.88, 30.0: 0.87}, ["outside the usual range", "far from the"]),
                                 ("crooked", {60.0: 0.94, 45.0: 0.925, 30.0: 0.95}, ["far from the"])):
        hs = {80.0: an["heights"][80.0]}
        for h, fac in factors.items():
            hs[h] = {"ws": ws80 * fac, "sd": an["heights"][80.0]["sd"], "wd": an["heights"][80.0]["wd"]}
        heights = sorted(hs)
        means = pd.Series({h: float(hs[h]["ws"].mean()) for h in heights})
        fit = float(np.polyfit(np.log(heights), np.log(means.to_numpy()), 1)[0])
        c = cl.from_analysis({**an, "heights": hs, "shear": {"heights": heights, "means": means, "alpha_fit": fit,
                                                             "alpha_top_bottom": 0.1, "lo": 30.0, "hi": 80.0}}, tq)
        w = c["shear"]["warning"]
        assert all(x in w for x in words) and (bool(w) == bool(words)), (name, w)
        assert not words or w.count("Check the sensor heights") == 1


def seasonal_analysis(start, end, seed=5):
    """A record with a strong season: windy in January, calm in July."""
    rng = np.random.default_rng(seed)
    t = pd.date_range(start, end, freq="10min")
    season = 1.0 + 0.35 * np.cos(2 * np.pi * (t.month - 1) / 12)
    ws80 = pd.Series(7.0 * season * rng.weibull(2.2, len(t)) / 0.8856, index=t)
    wd = pd.Series(rng.uniform(60, 120, len(t)), index=t)
    sd = ws80 * 0.12
    hs = {80.0: {"ws": ws80, "sd": sd, "wd": wd}, 60.0: {"ws": ws80 * (60 / 80) ** 0.2, "sd": sd, "wd": wd}}
    an = {"heights": hs, "top_height": 80.0, "shear": {"alpha_top_bottom": 0.2, "lo": 60.0, "hi": 80.0},
          "density": {"mean": 1.05, "sensor_height": 10.0}}
    return an, {"interval_s": 600, "expected": len(t), "start": t[0], "end": t[-1]}


def test_the_climate_is_set_to_the_annual_mean_not_to_the_plain_mean_of_the_records():
    # 14 months, June and July twice: the records lean towards the calm season (seen on a real mast, where the
    # wind map stood well below the long-term mean the same project had just worked out)
    c = cl.from_analysis(*seasonal_analysis("2018-06-01", "2019-07-31 23:50"))
    assert c["calendar_months"] == 12 and c["momm_ws"] > 1.03 * c["mean_ws"]
    s = cl.speed_scale(c)
    assert s["mean_ws"] == pytest.approx(c["momm_ws"]) and s["scale"] == pytest.approx(c["momm_ws"] / s["records_mean_ws"])
    assert s["records_mean_ws"] == pytest.approx(c["mean_ws"], rel=0.01) and "some months hold more records than others" in s["lean"]
    _, f, A, k = cl.at_height(c, 80.0, factor=s["scale"])
    assert cl.mean_speed(f, A, k) == pytest.approx(c["momm_ws"])
    # with a long-term correction the climate stands on the long-term mean itself
    lt = {"long_term": 7.40, "measured": 7.10, "factor": 7.40 / 7.10}
    s2 = cl.speed_scale(c, lt)
    _, f, A, k = cl.at_height(c, 80.0, factor=s2["scale"])
    assert cl.mean_speed(f, A, k) == pytest.approx(7.40) and "long-term mean 7.40 m/s at 80 m" in s2["basis"]
    assert cl.mean_speed(*cl.at_height(c, 120.0, alpha=0.2, factor=s2["scale"])[1:]) == pytest.approx(7.40 * 1.5 ** 0.2)


def test_an_even_record_needs_no_seasonal_scaling_and_a_part_year_is_taken_as_it_is():
    year = cl.from_analysis(*seasonal_analysis("2019-01-01", "2019-12-31 23:50"))
    s = cl.speed_scale(year)
    assert abs(s["scale"] - 1.0) < 0.01 and s["lean"] == ""
    part = cl.from_analysis(*seasonal_analysis("2019-03-01", "2019-07-31 23:50"))
    s = cl.speed_scale(part)
    assert s["scale"] == 1.0 and "less than a full year" in s["basis"] and part["calendar_months"] == 5


def test_daily_directions_are_handed_on_for_the_vane_check():
    an, tq = fake_analysis(n=144 * 20)
    hs = an["heights"]
    wd = hs[80.0]["wd"].copy()
    wd.iloc[144 * 3:144 * 4 - 100] = np.nan                    # the fourth day keeps only 100 of its 144 records
    hs[80.0]["wd"] = wd
    c = cl.from_analysis(an, tq)
    dd = c["direction_days"]
    assert dd["first"] == "2023-01-01" and len(dd["deg"]) == len(dd["ws"]) == 20
    assert dd["deg"][3] is None and dd["ws"][3] is None         # under five sixths of the day: not a day to compare
    east = [d for i, d in enumerate(dd["deg"]) if i != 3]
    assert all(60 <= d <= 120 for d in east)                    # 70 % of the wind from 90 degrees, and the stronger
    assert all(isinstance(d, int) for d in east) and all(3.0 < w < 12.0 for i, w in enumerate(dd["ws"]) if i != 3)
    # a mast with no vane hands on nothing
    hs[80.0]["wd"] = None
    hs[60.0]["wd"] = None
    assert cl.from_analysis(an, tq)["direction_days"] is None
