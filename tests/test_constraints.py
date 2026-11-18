"""Exclusion zones, buffers and setbacks. Run: python -m pytest tests/test_constraints.py -q"""
import json
import sys
import zipfile
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bohemian_wind import terrain as tr  # noqa: E402
from bohemian_wind.environment import constraints as cs  # noqa: E402


@pytest.fixture
def grid():
    return tr.from_function(lambda x, y: 1500.0 + 0 * x, -1.5, 36.5, half_km=5, res=50)


def square(grid, x0, x1, y0, y1):
    """A square in local metres as (lat, lon) ring."""
    xs, ys = [x0, x1, x1, x0, x0], [y0, y0, y1, y1, y0]
    lat, lon = grid.to_latlon(np.array(xs), np.array(ys))
    return list(zip(lat.tolist(), lon.tolist()))


def test_polygon_area_holes_and_buffer(grid):
    f = cs.Feature("polygon", [square(grid, -1000, 1000, -500, 500)])
    m = cs.polygon_mask(grid, f)
    assert m.sum() * 0.05 ** 2 == pytest.approx(2.0, rel=0.03)                # 2 km x 1 km
    holed = cs.Feature("polygon", [square(grid, -1000, 1000, -500, 500), square(grid, -200, 200, -200, 200)])
    assert cs.polygon_mask(grid, holed).sum() * 0.05 ** 2 == pytest.approx(2.0 - 0.16, rel=0.04)
    b = cs.grow(m, grid.res, 300.0)
    expected = 2.0 + 2 * (2.0 + 1.0) * 0.3 + np.pi * 0.3 ** 2                 # rectangle + strips + round corners
    assert b.sum() * 0.05 ** 2 == pytest.approx(expected, rel=0.03)
    assert cs.grow(m, grid.res, 0).sum() == m.sum()
    off = cs.Feature("polygon", [square(grid, 50000, 51000, 0, 1000)])        # outside the grid: nothing, no error
    assert not cs.polygon_mask(grid, off).any()


def test_point_and_line_buffers(grid):
    m = cs.near_points(grid, np.array([[0.0, 0.0], [3000.0, 3000.0]]), 500.0)
    assert m.sum() * 0.05 ** 2 == pytest.approx(2 * np.pi * 0.5 ** 2, rel=0.03)
    lat, lon = grid.to_latlon(np.array([-2000.0, 2000.0]), np.array([1000.0, 1000.0]))
    line = cs.Feature("line", list(zip(lat.tolist(), lon.tolist())))
    lm = cs.near_line(grid, line, 250.0)
    assert lm.sum() * 0.05 ** 2 == pytest.approx(4.0 * 0.5 + np.pi * 0.25 ** 2, rel=0.03)
    r, c = grid.index(0.0, 1200.0)
    assert lm[r, c] and not lm[grid.index(0.0, 1300.0)]


def test_buildable_area_report(grid):
    g = tr.from_function(lambda x, y: 1500.0 + np.where(x > 3000, 0.4 * (x - 3000), 0.0), -1.5, 36.5, half_km=5, res=50)
    rules = cs.Rules(radius_km=4.0, slope_max_pct=15.0, exclusion_buffer_m=200.0, receptor_setback_m=600.0)
    excl = [cs.Feature("polygon", [square(g, -3000, -2000, -500, 500)], "wetland")]
    rec = cs.points_of([cs.Feature("point", [tuple(float(v) for v in g.to_latlon(0.0, 2000.0))], "Home A")], g)
    ok, rep = cs.buildable(g, rules, exclusions=excl, receptors=rec)
    assert rep["steps"][0]["left_km2"] == pytest.approx(np.pi * 16, rel=0.01)
    by = {s["rule"]: s for s in rep["steps"]}
    assert by["Slope above 15 %"]["removed_km2"] > 1.0                      # the steep eastern strip inside the circle
    # the buffer is exact for turbine positions (cell centres): none within 200 m of the zone survives
    X, Y = g.mesh()
    dist = np.hypot(np.maximum(np.maximum(-3000 - X, X + 2000), 0), np.maximum(np.maximum(-500 - Y, Y - 500), 0))
    assert not ok[dist <= 199.9].any()                                  # (a centre exactly on the limit may go either way)
    exact = 1.4 * 1.4 - (4 - np.pi) * 0.04
    assert ok[(dist > 200.1) & (dist < 400.0) & (np.hypot(X, Y) < 3900)].all()
    assert by["Exclusion zones (1) with a 200 m buffer"]["removed_km2"] == pytest.approx(exact, rel=0.04)
    assert by["Within 600 m of homes and other receptors (1)"]["removed_km2"] == pytest.approx(np.pi * 0.36, rel=0.03)
    assert not ok[g.index(-2500.0, 0.0)] and not ok[g.index(0.0, 2100.0)] and not ok[g.index(3500.0, 0.0)]
    assert ok[g.index(0.0, 0.0)] and not ok[g.index(4500.0, 0.0)]
    assert rep["buildable_km2"] == pytest.approx(ok.sum() * 0.0025)
    # a boundary keeps turbines inside it; a wind speed floor removes weak cells
    bnd = [cs.Feature("polygon", [square(g, -1000, 1000, -1000, 1000)])]
    ok2, rep2 = cs.buildable(g, cs.Rules(radius_km=4.0, min_mean_ws=6.0), boundary=bnd,
                             mean_ws=np.where(g.mesh()[0] > 0, 7.0, 5.0))
    assert rep2["buildable_km2"] == pytest.approx(2.0, rel=0.06)


def test_reads_geojson_kml_kmz_and_csv(tmp_path, grid):
    gj = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"name": "Reserve"}, "geometry": {"type": "Polygon", "coordinates": [[[36.5, -1.5], [36.51, -1.5], [36.51, -1.49], [36.5, -1.5]]]}},
        {"type": "Feature", "properties": {}, "geometry": {"type": "MultiPolygon", "coordinates": [[[[36.4, -1.4], [36.41, -1.4], [36.41, -1.39], [36.4, -1.4]]]]}},
        {"type": "Feature", "properties": {"name": "Road"}, "geometry": {"type": "LineString", "coordinates": [[36.45, -1.5], [36.55, -1.5]]}},
        {"type": "Feature", "properties": {"name": "Home 1"}, "geometry": {"type": "Point", "coordinates": [36.52, -1.51]}}]}
    (tmp_path / "a.geojson").write_text(json.dumps(gj))
    fs = cs.read_features(tmp_path / "a.geojson")
    assert [f.kind for f in fs] == ["polygon", "polygon", "line", "point"]
    assert fs[0].name == "Reserve" and fs[0].coords[0][0] == (-1.5, 36.5) and fs[3].coords == [(-1.51, 36.52)]
    kml = """<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>
      <Placemark><name>Zone</name><Polygon><outerBoundaryIs><LinearRing><coordinates>
        36.5,-1.5,0 36.51,-1.5,0 36.51,-1.49,0 36.5,-1.5,0</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
      <Placemark><name>Home 2</name><Point><coordinates>36.53,-1.52,0</coordinates></Point></Placemark>
      <Placemark><name>Line</name><LineString><coordinates>36.4,-1.5 36.6,-1.5</coordinates></LineString></Placemark>
    </Document></kml>"""
    (tmp_path / "b.kml").write_text(kml)
    fk = cs.read_features(tmp_path / "b.kml")
    assert sorted(f.kind for f in fk) == ["line", "point", "polygon"]
    assert next(f for f in fk if f.kind == "point").coords == [(-1.52, 36.53)]
    with zipfile.ZipFile(tmp_path / "c.kmz", "w") as z:
        z.writestr("doc.kml", kml)
    assert len(cs.read_features(tmp_path / "c.kmz")) == 3
    (tmp_path / "homes.csv").write_text("Name,Latitude,Longitude\nH1,-1.51,36.52\nH2,-1.49,36.48\nbad,x,y\n")
    pts = cs.points_of(cs.read_features(tmp_path / "homes.csv"), grid)
    assert [p["name"] for p in pts] == ["H1", "H2"] and pts[0]["x"] == pytest.approx(grid.to_xy(-1.51, 36.52)[0])
    (tmp_path / "x.shp").write_text("x")
    with pytest.raises(cs.ConstraintError):
        cs.read_features(tmp_path / "x.shp")
    with pytest.raises(cs.ConstraintError):
        cs.read_features(tmp_path / "missing.kml")
    (tmp_path / "nocols.csv").write_text("a,b\n1,2\n")
    with pytest.raises(cs.ConstraintError):
        cs.read_features(tmp_path / "nocols.csv")
