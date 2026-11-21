"""The classed wind map, the Google Earth file and the Word reports, on invented data only."""
import io
import sys
import zipfile
from datetime import date
from pathlib import Path
from xml.dom import minidom

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bohemian_wind import demo, kmz, maps, terrain  # noqa: E402
from bohemian_wind.reports import build, kit  # noqa: E402

docx = pytest.importorskip("docx")
TODAY = date(2026, 3, 2)


def test_wind_classes_are_fixed_and_cover_every_speed():
    assert len(maps.WIND_COLOURS) == len(maps.WIND_EDGES) + 1 == len(maps.wind_class_labels()) == 36
    assert maps.WIND_EDGES == sorted(maps.WIND_EDGES) and len(set(maps.WIND_COLOURS)) == 36
    k = maps.wind_class([0.0, 4.99, 5.0, 5.49, 5.5, 6.0, 6.19, 6.2, 8.0, 10.99, 11.0, 12.99, 13.0, 30.0])
    assert list(k) == [0, 0, 1, 1, 2, 4, 4, 5, 14, 28, 29, 34, 35, 35]            # a speed on a limit is in the class above
    labels = maps.wind_class_labels()
    assert labels[0] == "below 5.00" and labels[5] == "6.20 to 6.39" and labels[-1] == "13.00 and above"
    rgb = maps.wind_rgb(np.array([[4.0, np.nan], [8.05, 20.0]]))
    assert rgb.shape == (2, 2, 3) and tuple(rgb[0, 1]) == (1.0, 1.0, 1.0)         # no value: white
    assert tuple(np.round(rgb[1, 0] * 255)) == (255, 223, 105)                   # 8.0 to 8.19 m/s: yellow


def _field():
    g = terrain.from_function(lambda x, y: 900 + 120 * np.exp(-((x - 1500) ** 2 + y ** 2) / 3e6), -10.0, 30.0, half_km=4, res=100)
    return g, 7.0 + (g.elev - 900) / 60


def test_kmz_holds_the_map_its_key_the_rose_the_mast_and_the_turbines(tmp_path):
    g, ws = _field()
    sectors = [{"centre": i * 30.0, "freq": 1 / 12, "A": 8.0 + (i == 3), "k": 2.1} for i in range(12)]
    facts, lines = kmz.mast_facts("Invented", {"lat": -10.0, "lon": 30.0}, {"period": ["2030-01-01", "2031-01-01"], "height_m": 100.0,
                                                                         "mean_ws": 7.0, "momm_ws": 7.1}, None, 120.0, "linear", 100.0)
    p = kmz.write(tmp_path / "m.kmz", g, ws, name="Invented: wind map", height_m=120.0, mast={"lat": -10.0, "lon": 30.0, "label": "Mast"},
                  sectors=sectors, rose_lines=lines, facts=facts,
                  turbines=[{"id": "T01", "lat": -10.01, "lon": 30.01, "free_ws": 7.9, "gross_mwh": 20000.0, "p50_mwh": 18000.0}])
    z = zipfile.ZipFile(p)
    assert set(z.namelist()) == {"doc.kml", "files/wind_map.png", "files/legend.png", "files/height_sign.png", "files/energy_rose.png"}
    text = z.read("doc.kml").decode("utf-8")
    dom = minidom.parseString(text)
    assert len(dom.getElementsByTagName("GroundOverlay")) == 4
    names = [n.firstChild.data for n in dom.getElementsByTagName("name")]
    assert "Mast" in names and "T01" in names and "Area mapped" in names
    assert "not an accredited or bankable" in text and "18,000 MWh" in text
    box = dom.getElementsByTagName("LatLonBox")[0]                                   # the map lies where the grid lies
    edge = {k: float(box.getElementsByTagName(k)[0].firstChild.data) for k in ("north", "south", "east", "west")}
    lat, lon = g.to_latlon(np.array([g.x[0] - 50, g.x[-1] + 50]), np.array([g.y[0] - 50, g.y[-1] + 50]))
    assert edge["south"] == pytest.approx(lat[0], abs=1e-6) and edge["north"] == pytest.approx(lat[1], abs=1e-6)
    assert edge["west"] == pytest.approx(lon[0], abs=1e-6) and edge["east"] == pytest.approx(lon[1], abs=1e-6)
    from PIL import Image
    im = Image.open(io.BytesIO(z.read("files/wind_map.png")))
    assert im.size == (2 * ws.shape[1], 2 * ws.shape[0])
    top = im.convert("RGB").getpixel((1, 1))                                          # the picture's top row is the grid's north edge
    assert top == tuple(int(round(255 * v)) for v in maps.wind_rgb(ws[-1:, :1])[0, 0])


def test_sector_energy_follows_the_weibull_formula():
    from math import gamma
    e = kmz.sector_energy([{"centre": 0.0, "freq": 1.0, "A": 8.0, "k": 2.0}], rho=1.2)
    assert e[0] == pytest.approx(0.5 * 1.2 * 8.0 ** 3 * gamma(2.5) * 8.76)
    assert kmz.sector_energy([{"centre": 0.0, "freq": 1.0, "A": 8.0, "k": 2.0}], rho=1.2, scale=1.1)[0] == pytest.approx(e[0] * 1.1 ** 3)


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    p = demo.create(tmp_path_factory.mktemp("rep") / "Demo Ridge", today=TODAY)
    p.run("data", log=lambda m: None)
    return p


def _text(path) -> str:
    d = docx.Document(str(path))
    cells = [c.text for t in d.tables for r in t.rows for c in r.cells]
    return "\n".join([p.text for p in d.paragraphs] + cells)


def test_after_step_1_the_wind_resource_report_is_written_and_the_micrositing_report_waits(project):
    r = project.result("data")
    names = [Path(x["file"]).name for x in r["reports"]]
    assert names == ["Wind_Resource_Analysis_Demo_Ridge_invented_data.docx", "Project_Wind_Summary_Demo_Ridge_invented_data.docx"]
    assert any("Micrositing" in s and "step 4" in s for s in r["reports_skipped"])
    text = _text(project.folder / r["reports"][0]["file"])
    assert kit.NOTICE in text and "No long-term correction has been made" in text
    assert "Wind Resource Analysis" in text and "Departures from usual practice" in text


def test_the_whole_chain_leaves_three_reports_and_two_google_earth_files(project):
    for step in ("longterm", "map", "layout", "environment", "pack"):
        project.run(step, log=lambda m: None)
    kml = zipfile.ZipFile(project.folder / project.result("map")["kmz"]).read("doc.kml").decode("utf-8")
    assert "Flow model" in kml and "own linear flow model" in kml
    lay = project.result("layout")
    z = zipfile.ZipFile(project.folder / lay["kmz"])
    assert z.read("doc.kml").decode("utf-8").count("#turbine") == lay["n_placed"]
    reports = {Path(x["file"]).name.split("_Demo")[0]: project.folder / x["file"] for x in project.result("environment")["reports"]}
    assert set(reports) == {"Wind_Resource_Analysis", "Micrositing", "Project_Wind_Summary"}
    wra, mic, summ = (_text(reports[k]) for k in ("Wind_Resource_Analysis", "Micrositing", "Project_Wind_Summary"))
    lt = project.result("longterm")
    assert f"{lt['factor']:.3f}" in wra and f"{lt['long_term']:.2f} m/s" in wra                 # the figures are the steps' own
    assert f"{lay['p50_gwh']:.1f} GWh a year" in mic and f"{lay['p50_gwh']:.1f} GWh a year" in summ
    assert f"{lay['uncertainty']['p90_10_year']:.1f}" in mic and "generic" in mic
    for text in (wra, mic, summ):                                                               # [COMPLIANCE] on every report
        assert text.count(kit.NOTICE) >= 2
        low = text.lower().replace("not an accredited or bankable", "").replace("not the result of an accredited", "").replace(
            "reviewed by an accredited", "")
        assert "bankable" not in low and "accredited" not in low and "certified" not in low
    for p in reports.values():                                                                  # the house template: header logo, numbered headings
        d = docx.Document(str(p))
        assert any(par.style.name == "Heading 1" for par in d.paragraphs)
        assert d.sections[0].footer.paragraphs and "[Month Year]" not in "".join(q.text for q in d.sections[0].first_page_footer.paragraphs)
    pack = [Path(x["file"]).name for x in project.result("pack")["files"]]
    assert "wind_map_and_layout.kmz" in pack and sum(n.endswith(".docx") for n in pack) == 3


def test_a_report_that_cannot_be_written_never_stops_a_step(project, monkeypatch):
    monkeypatch.setattr(build, "wind_resource", lambda *a, **k: (_ for _ in ()).throw(OSError("the file is open in Word")))
    out = build.write_all(project, log=lambda m: None)
    assert any("open in Word" in s for s in out["skipped"]) and len(out["files"]) == 2
    monkeypatch.setattr(kit, "available", lambda: False)
    out = build.write_all(project, log=lambda m: None)
    assert out["files"] == [] and "python-docx" in out["skipped"][0]
