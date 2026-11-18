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
