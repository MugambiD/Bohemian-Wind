"""Wind fields on the site grid. Run: python -m pytest tests/test_flow_fields.py -q"""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bohemian_wind import climate as cl  # noqa: E402
from bohemian_wind import terrain as tr  # noqa: E402
from bohemian_wind.flow import fields  # noqa: E402


def climate(mean=7.0, k=2.2, alpha=0.14, height=80.0, freq=None):
    f = np.ones(12) / 12 if freq is None else np.asarray(freq, dtype=float)
    A = mean / float(cl.weibull_mean(1.0, k))
    return {"height_m": height, "n_sectors": 12, "shear": {"alpha": alpha, "basis": "test"},
            "sectors": [{"centre": 30.0 * i, "freq": float(f[i]), "A": A, "k": k, "mean": mean, "records": 1000}
                        for i in range(12)]}


def hill(height=80.0, half_width=600.0, x0=3000.0, y0=0.0):
    return lambda x, y: 1500.0 + height * np.exp(-((x - x0) ** 2 + (y - y0) ** 2) / half_width ** 2)


def test_flat_ground_gives_the_mast_wind_everywhere():
    g = tr.from_function(lambda x, y: 1200.0 + 0 * x, -1.5, 36.5, half_km=6, res=100)
    wf = fields.wind_field(g, climate(), 80.0, "linear")
    assert np.allclose(wf.ratio, 1.0) and np.allclose(wf.mean_ws, 7.0, atol=1e-6)
    assert wf.mast_mean_ws == pytest.approx(7.0, rel=1e-6) and wf.steep_share == 0.0


def test_hill_is_windier_than_the_mast_on_the_plain_and_height_scaling():
    g = tr.from_function(hill(), -1.5, 36.5, half_km=8, res=100)
    wf = fields.wind_field(g, climate(), 80.0, "linear", z0_m=0.03, keep_half_m=6000)
    assert wf.grid.shape == (121, 121) and wf.ratio.shape == (12, 121, 121)
    r0, c0 = wf.grid.index(0.0, 0.0)
    assert np.allclose(wf.ratio[:, r0, c0], 1.0)                       # the mast reproduces itself in every sector
    rt, ct = wf.grid.index(3000.0, 0.0)
    assert wf.mean_ws[rt, ct] > 1.05 * wf.mast_mean_ws
    assert wf.mean_ws[rt, ct] == wf.mean_ws.max()
    # carried to 120 m with the shear exponent and a long-term factor
    wf2 = fields.wind_field(g, climate(), 120.0, "linear", z0_m=0.03, lt_factor=1.04, keep_half_m=6000)
    assert wf2.mast_mean_ws == pytest.approx(7.0 * 1.04 * (120 / 80) ** 0.14, rel=1e-6)
    assert wf2.ratio[:, rt, ct].mean() < wf.ratio[:, rt, ct].mean()    # speed-up fades with height


def test_direction_matters_for_a_ridge():
    """A north-south ridge speeds up east and west winds, not winds blowing along it."""
    g = tr.from_function(lambda x, y: 1500.0 + 60.0 / (1 + ((x - 2500.0) / 400.0) ** 2), -1.5, 36.5, half_km=8, res=100)
    wf = fields.wind_field(g, climate(), 80.0, "linear", keep_half_m=5000)
    r, c = wf.grid.index(2500.0, 0.0)
    east, north = wf.ratio[3, r, c], wf.ratio[0, r, c]
    assert east > 1.08 and abs(north - 1.0) < 0.02


def test_save_load_and_notes(tmp_path):
    g = tr.from_function(lambda x, y: 1500.0 + 0.5 * np.abs(x), -1.5, 36.5, half_km=4, res=100)   # 50 % slopes
    wf = fields.wind_field(g, climate(), 80.0, "linear")
    assert wf.steep_share > 0.9 and any("steeper" in n for n in wf.notes)
    wf.save(tmp_path / "f.npz")
    w2 = fields.WindField.load(tmp_path / "f.npz")
    assert np.allclose(w2.ratio, wf.ratio, atol=1e-6) and w2.engine == "linear" and w2.notes == wf.notes
    assert np.allclose(w2.ratio_at([0.0, 1000.0], [0.0, 0.0]), wf.ratio[:, [40, 40], [40, 50]], atol=1e-6)
    with pytest.raises(fields.FlowError):
        fields.wind_field(g, climate(), 80.0, "magic")


def test_atlas_engine_scales_the_atlas_pattern_to_the_mast(tmp_path):
    rasterio = pytest.importorskip("rasterio")
    from rasterio.transform import from_origin
    res = 0.0025                                                       # the atlas grid: about 250 m
    lon0, lat1, n = 36.3, -1.3, 160
    lon = lon0 + (np.arange(n) + 0.5) * res
    lat = lat1 - (np.arange(n) + 0.5) * res
    LON, LAT = np.meshgrid(lon, lat)
    base = 6.0 + 20.0 * (LON - 36.5)                                   # windier to the east
    for h, fac in ((100, 1.0), (150, 1.08)):
        with rasterio.open(tmp_path / f"XXX_wind-speed_{h}m.tif", "w", driver="GTiff", height=n, width=n, count=1,
                           dtype="float32", crs="EPSG:4326", transform=from_origin(lon0, lat1, res, res)) as dst:
            dst.write((base * fac).astype("float32"), 1)
    assert sorted(fields.find_gwa_layers(tmp_path)) == [100.0, 150.0]
    g = tr.from_function(lambda x, y: 1500.0 + 0 * x, -1.5, 36.5, half_km=5, res=100)
    wf = fields.wind_field(g, climate(alpha=0.2), 100.0, "atlas", gwa_dir=tmp_path)
    r0, c0 = wf.grid.index(0.0, 0.0)
    assert np.allclose(wf.ratio[:, r0, c0], 1.0)
    m_lon, _ = tr.metres_per_degree(-1.5)
    east = wf.ratio[0, r0, wf.grid.index(4000.0, 0.0)[1]]
    assert east == pytest.approx((6.0 + 20.0 * 4000.0 / m_lon) / 6.0, rel=0.02)
    assert np.allclose(wf.ratio[0], wf.ratio[7])                       # the same ratio for every direction
    assert any("Global Wind Atlas" in n for n in wf.notes)
    # between the two layers: power law, so the ratio is unchanged when both layers scale alike
    wf125 = fields.wind_field(g, climate(alpha=0.2), 125.0, "atlas", gwa_dir=tmp_path)
    assert np.allclose(wf125.ratio, wf.ratio, atol=1e-6)
    with pytest.raises(fields.FlowError):
        fields.wind_field(g, climate(), 100.0, "atlas", gwa_dir=tmp_path / "nothing")
