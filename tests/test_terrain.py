"""Terrain grid, SRTM tiles and UTM. Run: python -m pytest tests/test_terrain.py -q"""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bohemian_wind import terrain as tr  # noqa: E402


def write_tile(path, lat_sw, lon_sw, fn, n=1201):
    """A synthetic tile: elevation = fn(lat, lon), stored like SRTM (big-endian, rows north to south)."""
    lat = lat_sw + 1 - np.arange(n) / (n - 1)
    lon = lon_sw + np.arange(n) / (n - 1)
    LON, LAT = np.meshgrid(lon, lat)
    np.round(fn(LAT, LON)).astype(">i2").tofile(path)


def plane(lat, lon):
    return 1000.0 + 2000.0 * (lat + 2.0) + 500.0 * (lon - 36.0)      # a tilted plane: exact under bilinear sampling


def test_tile_names_and_needs():
    assert tr.tile_name(1.5, 35.2) == "N01E035" and tr.tile_name(-1.94, 36.64) == "S02E036"
    assert tr.tile_name(-0.2, -0.2) == "S01W001" and tr.tile_corner("S02E036") == (-2, 36)
    assert tr.needed_tiles(-1.5, 36.5, 10) == ["S02E036"]
    assert tr.needed_tiles(-1.02, 36.98, 10) == ["S01E036", "S01E037", "S02E036", "S02E037"]
    assert tr.tile_url("S02E036").endswith("/skadi/S02/S02E036.hgt.gz")


def test_grid_from_tiles_across_a_tile_boundary(tmp_path):
    for la, lo in ((-2, 36), (-1, 36), (-2, 37), (-1, 37)):
        write_tile(tmp_path / f"{tr.tile_name(la + .5, lo + .5)}.hgt", la, lo, plane)
    g = tr.from_srtm(tmp_path, -1.02, 36.98, half_km=8, res=200)
    assert g.shape == (81, 81) and g.x[0] == -8000 and g.y[-1] == 8000 and g.res == 200
    lat, lon = g.to_latlon(*g.mesh())
    assert np.allclose(g.elev, plane(lat, lon), atol=1.0)                # rounding to whole metres in the tile
    assert g.elev[-1, 0] > g.elev[0, 0]                                   # row 0 is the south edge
    # slope of the plane: 2000 m per degree north, 500 m per degree east
    m_lon, m_lat = tr.metres_per_degree(-1.02)
    assert np.median(g.slope_pct) == pytest.approx(100 * np.hypot(2000 / m_lat, 500 / m_lon), rel=0.02)


def test_missing_tile_is_named(tmp_path):
    write_tile(tmp_path / "S02E036.hgt", -2, 36, plane)
    with pytest.raises(tr.TerrainError) as e:
        tr.from_srtm(tmp_path, -1.02, 36.98, half_km=8, res=200)
    assert "S01E037" in str(e.value) and "S02E036" not in str(e.value).split("missing:")[1].split(".")[0]


def test_voids_are_filled(tmp_path):
    def holed(lat, lon):
        z = plane(lat, lon)
        z[(np.abs(lat + 1.5) < 0.01) & (np.abs(lon - 36.5) < 0.01)] = tr.VOID
        return z
    write_tile(tmp_path / "S02E036.hgt", -2, 36, holed)
    g = tr.from_srtm(tmp_path, -1.5, 36.5, half_km=5, res=100)
    lat, lon = g.to_latlon(*g.mesh())
    assert g.voids_filled > 0 and np.isfinite(g.elev).all()
    assert np.abs(g.elev - plane(lat, lon)).max() < 30                    # filled from the neighbours


def test_not_a_tile(tmp_path):
    (tmp_path / "bad.hgt").write_bytes(b"\x00" * 1000)
    with pytest.raises(tr.TerrainError):
        tr.read_hgt(tmp_path / "bad.hgt")


def test_points_sampling_and_round_trip(tmp_path):
    g = tr.from_function(lambda x, y: 1500 + 0.05 * x + 0.02 * y, 1.5, 35.24, half_km=5, res=100)
    assert g.sample(250.0, -730.0) == pytest.approx(1500 + 12.5 - 14.6)
    assert g.sample(1e9, 0.0) == pytest.approx(1500 + 0.05 * 5000)        # clipped to the edge
    r, c = g.index([0.0, 149.0], [0.0, -5000.0])
    assert list(r) == [50, 0] and list(c) == [50, 51]
    assert g.inside(4990, 0) and not g.inside(5200, 0)
    lat, lon = g.to_latlon(3000.0, -4000.0)
    x, y = g.to_xy(lat, lon)
    assert x == pytest.approx(3000.0) and y == pytest.approx(-4000.0)
    assert np.allclose(g.slope_pct, 100 * np.hypot(0.05, 0.02))
    g.save(tmp_path / "g.npz")
    h = tr.Grid.load(tmp_path / "g.npz")
    assert np.allclose(h.elev, g.elev, atol=1e-3) and h.lat0 == 1.5 and h.res == 100


def test_slope_at_a_point_between_cells_is_the_ground_there_not_the_flanks():
    # a ridge whose crest runs between two columns of cells: flat on top, 40 % down each flank
    g = tr.from_function(lambda x, y: 2000.0 - 0.4 * np.maximum(np.abs(x - 50.0) - 50.0, 0.0), 1.5, 35.2, half_km=2, res=100)
    assert g.slope_at(50.0, 0.0) == pytest.approx(0.0, abs=1e-9)                  # on the crest
    r, c = g.index(0.0, 0.0)
    assert float(g.sample(50.0, 0.0, g.slope_pct)) == pytest.approx(20.0)         # what interpolating the cells' slopes says
    assert g.slope_at(0.0, 0.0) == pytest.approx(g.slope_pct[r, c]) == pytest.approx(20.0)   # on a cell: the cell's slope
    assert g.slope_at(400.0, 300.0) == pytest.approx(40.0)                        # on the flank
    assert np.allclose(g.slope_at(np.array([50.0, 400.0]), np.array([0.0, 300.0])), [0.0, 40.0])


def test_utm_matches_reference_values():
    """Reference values from PROJ (pyproj), WGS84."""
    for lat, lon, zone, hem, e, n in ((1.5, 35.25, 36, "N", 750348.144, 165924.175),
                                      (-2.0, 36.75, 37, "S", 249718.219, 9778767.434),
                                      (-16.0, 28.0, 35, "S", 606996.210, 8230807.238),
                                      (52.0, 5.0, 31, "N", 637294.366, 5762926.813),
                                      (-33.9, 18.4, 34, "S", 259583.222, 6245888.045),
                                      (60.0, 10.75, 32, "N", 597603.359, 6652702.206)):
        u = tr.utm(lat, lon)
        assert (u["zone"], u["hemisphere"]) == (zone, hem)
        assert u["easting"] == pytest.approx(e, abs=0.002) and u["northing"] == pytest.approx(n, abs=0.002)
    assert tr.utm(-1.9, 36.6)["epsg"] == 32737 and tr.utm(1.5, 35.2)["epsg"] == 32636
