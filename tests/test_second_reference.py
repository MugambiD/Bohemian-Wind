"""A second long-term reference from a file, set beside the first. Invented data only."""
import json
import sys
import zipfile
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bohemian_wind import demo, longterm  # noqa: E402
from bohemian_wind.energy import budget  # noqa: E402

TODAY = date(2026, 3, 2)


def _hourly(path, start="2000-01-01", end="2003-12-31 23:00", speed=6.0, gap=None):
    t = pd.date_range(start, end, freq="h")
    ang = np.deg2rad(np.arange(len(t)) % 360)
    v = speed * (1 + 0.1 * np.sin(2 * np.pi * t.month.to_numpy() / 12))
    df = pd.DataFrame({"valid_time": t.strftime("%Y-%m-%d %H:%M:%S"), "u100": v * np.cos(ang), "v100": v * np.sin(ang),
                       "latitude": -5.0, "longitude": 35.0})
    if gap is not None:
        df = df[~((t >= gap[0]) & (t < gap[1]))]
    df.to_csv(path, index=False)
    return v, t


def test_an_hourly_table_of_wind_components_becomes_complete_monthly_means(tmp_path):
    v, t = _hourly(tmp_path / "era.csv", gap=(pd.Timestamp("2001-06-10"), pd.Timestamp("2001-06-14")))
    s, what = longterm.read_reference_file(tmp_path / "era.csv")
    assert "u100 and v100" in what and "hourly values" in what and "2000-01 to 2003-12" in what
    assert len(s) == 47 and pd.Period("2001-06", "M") not in s.index          # four days missing: the month is left out
    jan = pd.Series(v, index=t)["2002-01"].mean()
    assert s[pd.Period("2002-01", "M")] == pytest.approx(jan, rel=1e-9)        # the speed is the length of (u, v)
    with zipfile.ZipFile(tmp_path / "era.zip", "w") as z:                      # the store's zip is read as it comes
        z.write(tmp_path / "era.csv", "data.csv")
    s2, what2 = longterm.read_reference_file(tmp_path / "era.zip")
    assert s2.equals(s) and "era.zip (data.csv)" in what2


def test_monthly_and_speed_tables_are_read_and_bad_ones_say_why(tmp_path):
    m = pd.period_range("2001-01", "2004-12", freq="M")
    (tmp_path / "m.csv").write_text("# a comment line\ntime;wind_speed\n" + "".join(f"{p}-01;{5 + p.month / 10:.2f}\n" for p in m))
    s, what = longterm.read_reference_file(tmp_path / "m.csv")
    assert len(s) == 48 and "monthly values" in what and s.iloc[0] == pytest.approx(5.1)
    (tmp_path / "bad.csv").write_text("time,temperature\n2001-01-01,20\n")
    with pytest.raises(ValueError, match="neither a wind speed column"):
        longterm.read_reference_file(tmp_path / "bad.csv")
    (tmp_path / "short.csv").write_text("time,ws\n" + "".join(f"2001-{k:02d}-01,5\n" for k in range(1, 13)))
    with pytest.raises(ValueError, match="needs years"):
        longterm.read_reference_file(tmp_path / "short.csv")
    with pytest.raises(ValueError, match="not found"):
        longterm.read_reference_file(tmp_path / "none.csv")


def test_the_reference_term_follows_the_distance_between_the_two():
    assert budget.reference_pct(None) == budget.REFERENCE_PCT
    assert budget.reference_pct(0.4) == budget.REFERENCE_TWO_FLOOR_PCT == 1.0
    assert budget.reference_pct(-5.0) == 2.5 and budget.reference_pct(9.0) == 4.5      # two that disagree widen it
    one = budget.speed_terms_for({"pct": 3.0, "corrected": True})
    two = budget.speed_terms_for({"pct": 3.0, "corrected": True, "reference_pct": 1.2})
    assert budget.REFERENCE in one and budget.REFERENCE not in two and two[budget.REFERENCE_TWO] == 1.2
    assert budget.REFERENCE_TWO not in budget.speed_terms_for({"pct": 12.0, "corrected": False, "reference_pct": 1.2})


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    p = demo.create(tmp_path_factory.mktemp("second") / "Demo Ridge", today=TODAY)
    p.run("data", log=lambda m: None)
    p.run("longterm", log=lambda m: None)
    return p


def _second_file(p, scale_long_term: float, name="second.csv"):
    """The demo's own reference as a monthly table of another height, with its long-term years scaled."""
    v = json.loads((p.folder / "longterm" / "demo_reference_monthly.json").read_text())["properties"]["parameter"]["WS50M"]
    y0, y1 = p.result("longterm")["lt_years"]
    rows = []
    for k, x in sorted(v.items()):
        if k[4:] == "13":
            continue
        measured = k[:4] >= "2025"                    # the campaign of the demo: left as it is
        rows.append(f"{k[:4]}-{k[4:]}-15,{1.3 * x * (1.0 if measured or not y0 <= int(k[:4]) <= y1 else scale_long_term):.4f}\n")
    f = p.folder / "longterm" / name
    f.write_text("date,ws\n" + "".join(rows))
    return f


def test_a_second_reference_is_set_beside_the_first_and_narrows_or_widens_the_uncertainty(project):
    first = dict(project.result("longterm"))
    assert "second" not in first
    f = _second_file(project, 0.97)                       # a reference whose long term was 3 % calmer: the mast's long-term mean comes out lower
    project.update("longterm", {"second_file": str(f)})
    assert project.status("longterm") == "stale"          # a setting the step works from was changed
    said = []
    r = project.run("longterm", log=said.append)
    s2 = r["second"]
    assert r["long_term"] == pytest.approx(first["long_term"]) and r["factor"] == pytest.approx(first["factor"])   # the first is carried forward
    assert s2["same_years"] and s2["lt_years"] == list(first["lt_years"])
    assert s2["long_term"] < r["long_term"] and -3.5 < s2["difference_pct"] < -1.5
    assert any("Second reference" in str(m) for m in said)
    u = project.long_term_uncertainty(project.climate())
    assert u["reference_pct"] == pytest.approx(max(1.0, abs(s2["difference_pct"]) / 2)) and "second reference" in u["basis"]
    for step in ("map", "layout"):
        project.run(step, log=lambda m: None)
    terms = project.result("layout")["uncertainty"]["terms_energy_pct"]
    assert budget.REFERENCE_TWO in terms and budget.REFERENCE not in terms
    docx = pytest.importorskip("docx")
    wra = next(project.folder / x["file"] for x in project.result("layout")["reports"] if "Wind_Resource" in x["file"])
    d = docx.Document(str(wra))
    text = "\n".join([q.text for q in d.paragraphs] + [c.text for t in d.tables for row in t.rows for c in row.cells])
    assert "A second reference" in text and f"{s2['long_term']:.2f}" in text and "two reanalysis references" in text


def test_a_changed_or_unreadable_second_file_is_noticed(project):
    f = project.folder / "longterm" / "second.csv"
    f.write_text(f.read_text() + "\n")                     # the file changed after the step ran
    import os
    os.utime(f, (f.stat().st_atime, f.stat().st_mtime + 5))
    assert project.status("longterm") == "stale"
    f.write_text("date,temperature\n2001-01-01,20\n")
    from bohemian_wind.project import ProjectError
    with pytest.raises(ProjectError, match="second long-term reference could not be used"):
        project.run("longterm", log=lambda m: None)
    project.update("longterm", {"second_file": ""})
    assert "second" not in project.run("longterm", log=lambda m: None)


def test_the_file_reference_can_be_the_one_carried_forward(project):
    f = _second_file(project, 1.05, "carried.csv")
    project.s["longterm"]["second_file"] = str(f)
    project.run("longterm", log=lambda m: None)
    usual = project.result("longterm")
    project.s["longterm"]["carry"] = "second"
    assert project.stale_reason("longterm")                                    # the choice belongs to the settings the result rests on
    project.run("longterm", log=lambda m: None)
    lt = project.result("longterm")
    assert lt["carried"] == "second" and "carried.csv" in lt["credit"]
    assert lt["long_term"] == pytest.approx(usual["second"]["long_term"]) and lt["second"]["long_term"] == pytest.approx(usual["long_term"])
    assert lt["second"]["difference_pct"] == pytest.approx(100 * (usual["long_term"] / usual["second"]["long_term"] - 1))
    assert "MERRA-2" in lt["second"]["what"]
    project.s["longterm"].update(second_file="", carry="second")
    with pytest.raises(Exception, match="give its file"):
        project.run("longterm", log=lambda m: None)
    project.s["longterm"]["carry"] = "first"


def _rows(tmp_path, name, frame):
    f = tmp_path / name
    frame.to_csv(f, index=False)
    return f


def test_a_reference_file_is_one_point_and_its_wind_speeds_can_be_wind_speeds(tmp_path):
    """Outside review, 23 Nov 2026: a file with two locations gave 4 or 12 m/s by the order of its rows, and negative
    and infinite wind speeds were taken in. Every such file is now refused with the reason."""
    from bohemian_wind.intake import summary  # noqa: PLC0415
    t = pd.date_range("2020-01-01", "2022-12-31 23:00", freq="h")
    a = pd.DataFrame({"valid_time": t, "ws100": 4.0, "latitude": -1.0, "longitude": 30.0})
    b = pd.DataFrame({"valid_time": t, "ws100": 12.0, "latitude": -1.25, "longitude": 30.0})
    for n, frame in (("ab.csv", pd.concat([a, b])), ("ba.csv", pd.concat([b, a]))):             # either order: refused
        with pytest.raises(ValueError, match="holds 2 locations"):
            longterm.read_reference_file(_rows(tmp_path, n, frame))
    both = pd.concat([a, b]).drop(columns=["latitude", "longitude"])                             # no position columns: caught by the times
    with pytest.raises(ValueError, match="more than once with different wind speeds"):
        longterm.read_reference_file(_rows(tmp_path, "nopos.csv", both))
    monthly, what = longterm.read_reference_file(_rows(tmp_path, "twice.csv", pd.concat([a, a])))   # the same rows twice are one series
    assert monthly.mean() == pytest.approx(4.0) and "at -1, 30" in what
    for n, col, value, why in (("neg.csv", "ws100", -3.0, "negative"), ("inf.csv", "ws100", np.inf, "infinite"), ("big.csv", "ws100", 400.0, "above 75")):
        bad = a.copy()
        bad.loc[100:140, col] = value
        with pytest.raises(ValueError, match=why):
            longterm.read_reference_file(_rows(tmp_path, n, bad))
    uv = pd.DataFrame({"valid_time": t, "u100": -3.0, "v100": 4.0})                                  # components may be negative
    assert longterm.read_reference_file(_rows(tmp_path, "uv.csv", uv))[0].mean() == pytest.approx(5.0)
    uv.loc[5, "u100"] = -np.inf
    with pytest.raises(ValueError, match="infinite"):
        longterm.read_reference_file(_rows(tmp_path, "uvinf.csv", uv))
    # the same rules for the file a summary-tables project takes its wind rose from
    two = pd.concat([a.assign(u100=-4.0, v100=0.0), b.assign(u100=0.0, v100=-12.0)])
    with pytest.raises(summary.SummaryError, match="holds 2 locations"):
        summary.read_directions(_rows(tmp_path, "rose2.csv", two))
    with pytest.raises(summary.SummaryError, match="more than once"):
        summary.read_directions(_rows(tmp_path, "rose3.csv", two.drop(columns=["latitude", "longitude"])))


def test_the_outside_reviews_five_files_read_as_its_acceptance_asks():
    """The five synthetic files of the review of 0.3.1-dev (23 Nov 2026): the control reads, the others are refused,
    and the two row orders of the two-location file end the same way."""
    here = Path(__file__).parent / "fixtures" / "reference_review"
    monthly, _ = longterm.read_reference_file(here / "control.csv")
    assert monthly.mean() == pytest.approx(6.0) and len(monthly) == 24
    said = {}
    for name, why in (("negative", "negative"), ("infinite", "infinite"), ("A-first", "holds 2 locations"), ("B-first", "holds 2 locations")):
        with pytest.raises(ValueError, match=why) as e:
            longterm.read_reference_file(here / f"{name}.csv")
        said[name] = str(e.value).replace(f"{name}.csv", "")
    assert said["A-first"].replace("lon 36; lon 37", "") == said["B-first"].replace("lon 37; lon 36", "")


def test_the_google_earth_files_carry_their_own_marker_icons(tmp_path):
    from bohemian_wind import kmz  # noqa: PLC0415
    f = kmz.write_mast(tmp_path / "mast.kmz", name="Invented", mast={"lat": -1.0, "lon": 30.0}, facts=[("Project", "Invented")])
    z = zipfile.ZipFile(f)
    kml = z.read("doc.kml").decode("utf-8")
    assert "http://" not in kml.replace("http://www.opengis.net/kml/2.2", "") and "files/icon_mast.png" in z.namelist()
    assert z.read("files/icon_mast.png")[:8] == b"\x89PNG\r\n\x1a\n"
