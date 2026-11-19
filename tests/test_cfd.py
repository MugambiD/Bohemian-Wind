"""CFD engine: mesh, case files, reading results back, and the stored-run engine. The OpenFOAM run itself is tested
only where OpenFOAM is installed. Invented terrain only. Run: python -m pytest tests/test_cfd.py -q"""
import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bohemian_wind import terrain as tr  # noqa: E402
from bohemian_wind.flow import cfd, fields  # noqa: E402


def climate(n=12):
    return {"height_m": 80.0, "n_sectors": n, "shear": {"alpha": 0.14, "basis": "test"},
            "sectors": [{"centre": 360.0 / n * i, "freq": 1.0 / n, "A": 8.0, "k": 2.0, "mean": 7.1, "records": 100} for i in range(n)]}


def test_axis_and_layers():
    x = cfd.axis_nodes(2000.0, 200.0, 5000.0, 1.2, 600.0)
    assert x[0] == -5000.0 and x[-1] == 5000.0 and np.allclose(x, -x[::-1])
    d = np.diff(x)
    core = d[(x[:-1] >= -2000.0) & (x[1:] <= 2000.0)]
    assert np.allclose(core, 200.0) and d.max() <= 1.3 * 600.0 and (d > 0).all()          # the last cell may take up a sliver
    f = cfd.layer_fractions(20, 4.0, 2000.0)
    assert f[0] == 0.0 and f[-1] == pytest.approx(1.0) and (np.diff(f) > 0).all()
    assert f[1] * 2000.0 == pytest.approx(4.0, rel=1e-3) and np.diff(f)[-1] > np.diff(f)[0]        # grows upwards


def test_the_mesh_follows_the_terrain_and_levels_out_at_the_edges():
    g = tr.from_function(lambda x, y: 1000.0 + 200.0 * np.exp(-(x ** 2 + y ** 2) / (2 * 800.0 ** 2)), 0.0, 30.0, half_km=5, res=50)
    s = cfd.Settings(cell_m=200.0, buffer_m=2000.0, layers=12)
    m = cfd.build_mesh(g, 270.0, 3000.0, s)
    nk, nj, ni = m.shape
    assert m.ground.shape == (nj + 1, ni + 1) and nk == 12
    c = np.argmin(np.abs(m.xs)), np.argmin(np.abs(m.ys))
    assert m.ground[c[1], c[0]] == pytest.approx(200.0 + 1000.0 - m.base, abs=1.0)                    # the hill top
    assert np.allclose(m.ground[0, :], 0.0) and np.allclose(m.ground[:, -1], 0.0)                     # flat at the edges
    pts = m.points()
    assert pts.shape == (nk + 1, nj + 1, ni + 1, 3) and np.allclose(pts[-1, ..., 2], m.top)
    h = cfd.cell_heights(m)
    assert (h > 0).all() and h[0].max() < 10.0


def test_the_turned_frame_carries_results_back_to_the_map():
    g = tr.from_function(lambda x, y: 0 * x, 0.0, 30.0, half_km=3, res=100)
    for direction in (0.0, 30.0, 135.0, 270.0):
        e1, e2 = cfd.frame(direction)
        assert np.dot(e1, e2) == pytest.approx(0.0) and np.linalg.norm(e1) == pytest.approx(1.0)
        # the wind comes from `direction`: e1 points the other way
        assert math.degrees(math.atan2(-e1[0], -e1[1])) % 360.0 == pytest.approx(direction)
        xc = np.linspace(-5000, 5000, 101)
        yc = np.linspace(-5000, 5000, 81)
        sp = np.broadcast_to(10.0 + 0.001 * xc[None, :] + 0.0002 * yc[:, None], (2, 81, 101))
        out = cfd.to_grid({"speeds": sp, "xc": xc, "yc": yc}, direction, g)
        X, Y = g.mesh()
        want = 10.0 + 0.001 * (X * e1[0] + Y * e1[1]) + 0.0002 * (X * e2[0] + Y * e2[1])
        assert np.allclose(out[0], want, atol=1e-6)


def test_stored_speeds_become_ratios_to_the_mast():
    g = tr.from_function(lambda x, y: 0 * x, 0.0, 30.0, half_km=2, res=100)
    heights = np.array([40.0, 80.0, 160.0])
    X, Y = g.mesh()
    base = 1.0 + 0.0001 * X
    speeds = np.stack([np.stack([base * h ** 0.14 for h in heights])] * 3)        # [sector, height, row, col]
    r = cfd.ratios(speeds, heights, 113.0, g)
    assert r.shape == (3,) + g.shape and np.allclose(r[:, 20, 20], 1.0)
    assert np.allclose(r[0], base / float(g.sample(0.0, 0.0, values=base)))
    with pytest.raises(cfd.CfdError):
        cfd.ratios(speeds, heights, 300.0, g)


def test_the_cfd_engine_reads_a_stored_run_and_refuses_one_that_does_not_fit(tmp_path):
    g = tr.from_function(lambda x, y: 1000.0 + 0 * x, 0.0, 30.0, half_km=2.5, res=100)
    c = climate()
    core = 2000.0
    cg = g.crop(core)
    heights = np.array(cfd.HEIGHTS, dtype=float)
    X, Y = cg.mesh()
    pattern = 1.0 + 0.05 * np.exp(-((X - 500.0) ** 2 + Y ** 2) / 300.0 ** 2)
    speeds = np.stack([np.stack([pattern * (h / 80.0) ** 0.14 for h in heights])] * 12).astype("float32")
    centres = [30.0 * i for i in range(12)]
    np.savez_compressed(tmp_path / "cfd.npz", speeds=speeds, heights=heights, centres=np.array(centres))
    meta = {"version": cfd.VERSION, "centres": centres, "heights": list(heights), "core_half_m": core,
            "settings": cfd.Settings(cell_m=100.0).as_dict(), "sectors": [{"stable": True}] * 12}
    (tmp_path / "cfd.json").write_text(json.dumps(meta))
    wf = fields.wind_field(g, c, 100.0, "cfd", keep_half_m=core, cfd_dir=tmp_path)
    assert wf.engine == "cfd" and wf.ratio.shape == (12,) + cg.shape
    assert wf.ratio_at(np.array([0.0]), np.array([0.0])) == pytest.approx(np.ones((12, 1)))
    assert wf.ratio_at(np.array([500.0]), np.array([0.0]))[0, 0] == pytest.approx(1.05 / float(cg.sample(0.0, 0.0, values=pattern)), rel=1e-3)
    assert any("OpenFOAM" in n for n in wf.notes) and not wf.cautions
    with pytest.raises(fields.FlowError):                                   # the vane correction turned the sectors
        fields.wind_field(g, c, 100.0, "cfd", keep_half_m=core, cfd_dir=tmp_path, rotate_deg=10.0)
    with pytest.raises(fields.FlowError):                                   # another area
        fields.wind_field(g, c, 100.0, "cfd", keep_half_m=1500.0, cfd_dir=tmp_path)
    with pytest.raises(fields.FlowError):
        fields.wind_field(g, c, 100.0, "cfd", keep_half_m=core, cfd_dir=tmp_path / "none")
    meta["sectors"][3] = {"stable": False}
    (tmp_path / "cfd.json").write_text(json.dumps(meta))
    assert fields.wind_field(g, c, 100.0, "cfd", keep_half_m=core, cfd_dir=tmp_path).cautions


def test_the_run_time_estimate_grows_with_the_cells():
    a = cfd.estimate(8000.0, cfd.Settings(cell_m=200.0, workers=4), 12)
    b = cfd.estimate(8000.0, cfd.Settings(cell_m=100.0, workers=4), 12)
    assert b["cells_per_sector"] > 3 * a["cells_per_sector"] and b["hours"] > a["hours"] > 0
    assert cfd.release({"version": "v2606"}) == 2606 and cfd.release({"version": "v1912"}) == 1912 and cfd.release(None) == 0


@pytest.mark.skipif(not cfd.available(), reason="OpenFOAM is not installed here")
def test_openfoam_over_flat_ground_keeps_the_wind_the_same_everywhere(tmp_path):
    g = tr.from_function(lambda x, y: 1000.0 + 0 * x, 0.0, 30.0, half_km=3, res=100)
    s = cfd.Settings(cell_m=300.0, buffer_m=1500.0, layers=12, iterations=150, workers=1)
    meta = cfd.run(g, [270.0], tmp_path, 1500.0, s, log=lambda m: None)
    speeds, heights, centres, _ = cfd.load(tmp_path)
    assert speeds.shape[0] == 1 and list(centres) == [270.0] and meta["sectors"][0]["cells"] > 0
    r = cfd.ratios(speeds, heights, 80.0, g.crop(1500.0))
    assert np.nanmax(np.abs(r - 1.0)) < 0.03
    k = list(heights).index(80.0)
    assert 8.5 < float(np.nanmedian(speeds[0, k])) < 10.5            # the logarithmic inflow kept: about 9.7 m/s at 80 m
