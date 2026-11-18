"""Known answers for the linear flow model. Run: python -m pytest tests -q"""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bohemian_wind.flow import linear as lf  # noqa: E402


def grid(n=512, dx=20.0):
    x = (np.arange(n) - n // 2) * dx
    return np.meshgrid(x, x)


def test_inner_layer_depth_solves_its_equation():
    for variant, p in (("jackson-hunt", 1), ("jensen", 2)):
        L = np.array([50.0, 250.0, 1000.0, 5000.0])
        l = lf.inner_layer_depth(L, 0.03, variant)
        assert np.allclose(l * np.log(l / 0.03) ** p, 2 * 0.4 ** 2 * L, rtol=1e-8)
    assert 8 < lf.inner_layer_depth(250.0, 0.03) < 16          # about 12 m for a 250 m hill over grass


def test_potential_flow_over_a_sine_ridge_is_exact():
    """h = a cos(kx), wind along x: du/U = a k exp(-k z) cos(kx)."""
    n, dx, a, waves = 256, 10.0, 5.0, 8
    X, _ = grid(n, dx)
    k = 2 * np.pi * waves / (n * dx)
    h = a * np.cos(k * X)
    for z in (10.0, 80.0):
        r = lf.speedup(h, dx, 270.0, z, 0.03, taper=0, inner_layer=False)
        assert np.allclose(r.speedup, a * k * np.exp(-k * z) * np.cos(k * X), atol=1e-9)
    # wind along the ridge: no speed-up at all
    assert np.allclose(lf.speedup(h, dx, 0.0, 10.0, 0.03, taper=0, inner_layer=False).speedup, 0, atol=1e-12)


def test_potential_flow_over_a_bell_ridge_matches_theory():
    """Witch of Agnesi ridge h = H / (1 + (x/L)^2): crest speed-up at the surface is H/L, and
    H L (L+z) ... at height z it is H L (L + z)^-2 ... evaluated on the crest: H L / (L + z)^2 * ... = H L/(L+z)^2."""
    n, dx, H, L = 4096, 10.0, 20.0, 400.0
    x = (np.arange(n) - n // 2) * dx
    h = np.tile(H / (1 + (x / L) ** 2), (8, 1))
    for z in (5.0, 100.0, 400.0):
        r = lf.speedup(h, dx, 270.0, z, 0.03, taper=0, inner_layer=False)
        assert r.speedup[0, n // 2] == pytest.approx(H * L / (L + z) ** 2, rel=0.03)


def test_same_answer_from_any_direction_on_a_round_hill():
    X, Y = grid(256, 25.0)
    h = 50.0 * np.exp(-(X ** 2 + Y ** 2) / 400.0 ** 2)
    top = [lf.speedup(h, 25.0, d, 10.0, 0.03).speedup[128, 128] for d in (0, 45, 90, 210, 300)]
    assert np.ptp(top) < 0.005 * np.mean(top)


def test_speed_up_on_the_crest_slow_down_at_the_foot_and_symmetry():
    X, Y = grid(256, 25.0)
    h = 50.0 * np.exp(-(X ** 2 + Y ** 2) / 400.0 ** 2)
    r = lf.speedup(h, 25.0, 270.0, 10.0, 0.03)
    assert r.speedup[128, 128] == r.speedup.max() > 0.1
    assert r.speedup[128, 128 - 28] < 0 and r.speedup[128, 128 + 28] < 0     # upwind and downwind foot
    assert np.allclose(r.speedup[128, 128 - 28], r.speedup[128, 128 + 28], atol=1e-6)   # linear: no wake
    assert abs(r.turning_deg[128, 128]) < 1e-6                              # no turning on the axis of symmetry
    assert r.turning_deg[128 + 20, 128 - 20] * r.turning_deg[128 - 20, 128 - 20] < 0  # flow splits round the hill


def test_ridge_guideline_two_h_over_l():
    """Taylor & Lee (1984) guideline: largest speed-up over a 2-D ridge is about 2 h/L (L = half-width at half
    height). The model should land near it for a typical ridge over grass."""
    n, dx, H, L = 2048, 10.0, 30.0, 300.0
    x = (np.arange(n) - n // 2) * dx
    h = np.tile(H / (1 + (x / L) ** 2), (8, 1))
    best = max(lf.speedup(h, dx, 270.0, z, 0.03, taper=0).speedup[0, n // 2] for z in (2, 4, 6, 10, 15))
    assert 1.7 * H / L < best < 2.4 * H / L
    # and it equals the Jackson-Hunt estimate [ln(L/z0)/ln(l/z0)]^2 h/L within the height fall-off
    l = lf.inner_layer_depth(L, 0.03)
    jh = (np.log(L / 0.03) / np.log(l / 0.03)) ** 2 * H / L
    assert best == pytest.approx(jh, rel=0.08)


def test_linear_in_hill_height_and_fades_with_height():
    X, Y = grid(256, 25.0)
    h = 30.0 * np.exp(-(X ** 2 + Y ** 2) / 300.0 ** 2)
    a = lf.speedup(h, 25.0, 270.0, 80.0, 0.03).speedup
    b = lf.speedup(2 * h, 25.0, 270.0, 80.0, 0.03).speedup
    assert np.allclose(np.hypot(1 + 0, 0) * 2 * a[128, 128], b[128, 128], rtol=0.02)
    tops = [lf.speedup(h, 25.0, 270.0, z, 0.03).speedup[128, 128] for z in (10, 40, 80, 150, 400)]
    assert all(x > y for x, y in zip(tops, tops[1:])) and tops[-1] < 0.25 * tops[0]


def test_rougher_ground_gives_more_speed_up_near_the_surface():
    X, Y = grid(256, 25.0)
    h = 50.0 * np.exp(-(X ** 2 + Y ** 2) / 400.0 ** 2)
    smooth = lf.speedup(h, 25.0, 270.0, 10.0, 0.001).speedup[128, 128]
    rough = lf.speedup(h, 25.0, 270.0, 10.0, 0.3).speedup[128, 128]
    assert rough > smooth


def test_mast_transfer_and_sector_map():
    X, Y = grid(128, 50.0)
    h = 60.0 * np.exp(-(X ** 2 + Y ** 2) / 600.0 ** 2)
    r = lf.speedup(h, 50.0, 270.0, 80.0, 0.03)
    assert lf.transfer(r, (64, 64), [(64, 64)])[0] == pytest.approx(1.0)
    assert lf.transfer(r, (64, 20), [(64, 64)])[0] > 1.0                    # hilltop is windier than the plain
    m = lf.sector_map(h, 50.0, (64, 64), 80.0, 0.03, np.ones(12), np.full(12, 7.0))
    assert m["mean_ws"][64, 64] == pytest.approx(7.0)                       # the mast reproduces itself
    assert m["mean_ws"][64, 5] < 7.0
    flat = lf.sector_map(np.zeros((64, 64)) + 100.0, 50.0, (10, 10), 80.0, 0.03, np.ones(12), np.full(12, 7.0))
    assert np.allclose(flat["mean_ws"], 7.0)                                # flat ground: no change anywhere


def test_bad_inputs_and_steepness():
    with pytest.raises(ValueError):
        lf.speedup(np.zeros((4, 4)), 10.0, 0.0, 10.0)
    with pytest.raises(ValueError):
        lf.speedup(np.zeros((16, 16)), 10.0, 0.0, 0.01, 0.03)
    X, Y = grid(128, 25.0)
    assert lf.steep_share(20.0 * np.exp(-(X ** 2 + Y ** 2) / 500.0 ** 2), 25.0) == 0.0
    assert lf.steep_share(400.0 * np.exp(-(X ** 2 + Y ** 2) / 300.0 ** 2), 25.0) > 0.05
