"""Step 1 from monthly summary tables: a mast known only from reports. Invented data only."""
import json
import math
import sys
import zipfile
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bohemian_wind import climate, demo, intake  # noqa: E402
from bohemian_wind.intake import summary  # noqa: E402

TODAY = date(2026, 3, 2)


def _reference(path, years=("2018-01-01", "2020-12-31 23:00"), from_deg=110.0):
    """An invented hourly series: the wind comes mostly from from_deg, and is stronger from there."""
    t = pd.date_range(*years, freq="h")
    rng = np.random.default_rng(5)
    wd = (from_deg + rng.normal(0, 40, len(t))) % 360
    ws = rng.weibull(2.2, len(t)) * (5 + 3 * np.cos(np.radians(wd - from_deg)))
    pd.DataFrame({"valid_time": t, "u100": -ws * np.sin(np.radians(wd)), "v100": -ws * np.cos(np.radians(wd))}).to_csv(path, index=False)


def _spec(folder: Path, months=("2024-03", "2025-06"), thin=3, direction=True, **more) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    rows = []
    for i, p in enumerate(pd.period_range(*months, freq="M")):
        base = 7 + math.sin(i / 2)
        rows.append({"month": str(p), "share": 0.5 if i == thin else 0.98, "mean_ws": {"80": base, "60": base * 0.96, "40": base * 0.90}})
    spec = {"format": summary.FORMAT, "version": 1, "source": "invented monthly reports",
            "site": {"name": "Invented ridge", "lat": -1.0, "lon": 30.0, "elevation_m": 1500, "position_basis": "invented for the test"},
            "months": rows, "weibull_k": 2.4, "weibull_k_basis": "invented", **more}
    if direction:
        _reference(folder / "reference.csv")
        spec["direction"] = {"file": "reference.csv", "what": "an invented hourly series"}
    (folder / summary.NAME).write_text(json.dumps(spec), encoding="utf-8")
    return folder


def test_monthly_tables_become_a_wind_climate_that_says_what_it_rests_on(tmp_path):
    out = intake.run_checks(_spec(tmp_path / "data"), tmp_path / "out", log=lambda m: None)
    c = climate.load(out["climate"])
    assert out["format"] == summary.LABEL and out["summary_tables"] and c["summary"]["measurement_pct"] == summary.MEASUREMENT_PCT
    assert c["height_m"] == 80 and [h["height_m"] for h in c["heights"]] == [80, 60, 40]
    assert c["calendar_months"] == 12 and out["months_usable"] == 15                  # the thin month does not count
    months = json.loads((tmp_path / "data" / summary.NAME).read_text())["months"]
    w = np.array([m["share"] * pd.Period(m["month"], "M").days_in_month for m in months])
    v = np.array([m["mean_ws"]["80"] for m in months])
    assert c["mean_ws"] == pytest.approx((w * v).sum() / w.sum())                     # weighted by the valid time
    assert c["shear"]["alpha"] == pytest.approx(math.log(1 / 0.96) / math.log(80 / 60), rel=1e-6)
    assert "60-80 m pair" in c["shear"]["basis"] and "monthly means" in c["shear"]["basis"]
    _, f, A, k = climate.sector_arrays(c)
    assert climate.mean_speed(f, A, k) == pytest.approx(c["mean_ws"])                 # the sectors add up to the reported mean
    gam = np.vectorize(math.gamma)
    cv2 = (f * A ** 2 * gam(1 + 2 / k)).sum() / c["mean_ws"] ** 2 - 1
    assert cv2 == pytest.approx(summary._cv2(2.4), rel=1e-6) and k[0] > 2.4            # together as wide as the entered k
    best = max(c["sectors"], key=lambda s: s["freq"])
    assert best["centre"] in (90.0, 120.0) and best["mean"] > c["mean_ws"] and not c["no_direction"]
    text = " ".join(out["findings"])
    assert "not on ten-minute records" in text and "reanalysis" in text and "invented for the test" in text and "2024-06" in text
    assert (Path(out["run_dir"]) / "inputs.csv").read_text().count("\n") == 3          # the tables and the direction file, fingerprinted


def test_without_a_direction_file_every_direction_is_alike_and_bad_files_say_why(tmp_path):
    out = intake.run_checks(_spec(tmp_path / "a", direction=False, months=("2025-01", "2025-08"), thin=99), tmp_path / "out", log=lambda m: None)
    c = climate.load(out["climate"])
    assert c["no_direction"] and c["calendar_months"] == 8 and c["momm_ws"] is None
    assert any("8 of the 12 calendar months" in x for x in out["findings"])
    d = _spec(tmp_path / "b", direction=False)
    spec = json.loads((d / summary.NAME).read_text())
    spec["months"].append(dict(spec["months"][0]))
    (d / summary.NAME).write_text(json.dumps(spec))
    with pytest.raises(intake.IntakeError, match="listed twice"):
        intake.run_checks(d, tmp_path / "out", log=lambda m: None)
    spec["months"] = [{"month": "2025-01", "share": 1.4, "mean_ws": {"80": 7}}]
    (d / summary.NAME).write_text(json.dumps(spec))
    with pytest.raises(intake.IntakeError, match="outside 0 to 1"):
        intake.run_checks(d, tmp_path / "out", log=lambda m: None)
    spec["months"] = [{"month": "2025-01", "share": 1.0, "mean_ws": {"80": 7}}]
    spec["direction"] = {"file": "none.csv"}
    (d / summary.NAME).write_text(json.dumps(spec))
    with pytest.raises(intake.IntakeError, match="not found"):
        intake.run_checks(d, tmp_path / "out", log=lambda m: None)
    with pytest.raises(intake.IntakeError, match="apply to logger files"):
        intake.run_checks(_spec(tmp_path / "c", direction=False), tmp_path / "out", log=lambda m: None, clock=[{"x": 1}])


def test_a_change_to_the_tables_changes_the_folder_signature(tmp_path):
    d = _spec(tmp_path / "data", direction=False)
    a = intake.folder_signature(d)
    f = d / summary.NAME
    f.write_text(f.read_text().replace("invented monthly reports", "invented monthly reports, second issue"))
    assert intake.folder_signature(d) != a


def test_the_steps_and_the_reports_run_on_summary_tables(tmp_path):
    docx = pytest.importorskip("docx")
    p = demo.create(tmp_path / "Demo Ridge", today=TODAY)
    ref = json.loads((p.folder / "longterm" / "demo_reference_monthly.json").read_text())["properties"]["parameter"]["WS50M"]
    keys = sorted(k for k in ref if not k.endswith("13"))[-16:-1]
    folder = p.folder / "tables"
    _spec(folder, months=(f"{keys[0][:4]}-{keys[0][4:]}", f"{keys[-1][:4]}-{keys[-1][4:]}"))
    spec = json.loads((folder / summary.NAME).read_text())
    for row in spec["months"]:                                   # the mast follows the demo's reference, 30 % above it
        v = ref[row["month"].replace("-", "")] * 1.3
        row["mean_ws"] = {"80": v, "60": v * 0.96, "40": v * 0.90}
    spec["site"].update(lat=demo.LAT, lon=demo.LON, elevation_m=demo.ELEV)
    (folder / summary.NAME).write_text(json.dumps(spec))
    p.s["data"]["folder"] = "tables"
    p.save()
    for step in ("data", "longterm", "map"):
        p.run(step, log=lambda m: None)
    lt, wm = p.result("longterm"), p.result("map")
    assert lt["r"] > 0.99 and lt["vane_check"] is None and lt["measured_height_m"] == 80
    assert any("summary tables" in w and "reanalysis" in w for w in wm["warnings"])
    kml = zipfile.ZipFile(p.folder / wm["kmz"]).read("doc.kml").decode("utf-8")
    assert "monthly summary tables" in kml and "directions from a reanalysis" in kml        # the Google Earth file says so too
    reports = {Path(x["file"]).name.split("_Demo")[0]: p.folder / x["file"] for x in wm["reports"] if x["file"].endswith(".docx")}
    assert set(reports) == {"Wind_Resource_Analysis", "Project_Wind_Summary"}

    def text(path):
        d = docx.Document(str(path))
        return "\n".join([q.text for q in d.paragraphs] + [c.text for t in d.tables for r in t.rows for c in r.cells])
    wra, summ = text(reports["Wind_Resource_Analysis"]), text(reports["Project_Wind_Summary"])
    assert "First view from monthly summary tables" in wra and "What this analysis rests on" in wra
    assert "not from the mast's ten-minute records" in wra and "reanalysis, not measured" in wra
    assert f"{lt['long_term']:.2f} m/s" in wra and f"{summary.MEASUREMENT_PCT:.1f}" in wra and "The wind over the site" in wra
    assert "Departures from usual practice" not in wra and "Tower shading" not in wra          # sections only records can fill
    assert "monthly summary tables" in summ and "ten-minute record was checked" in summ and "no record checked" in summ
    from bohemian_wind.reports import kit  # noqa: PLC0415
    for t in (wra, summ):                                                                       # [COMPLIANCE] on every report
        assert t.count(kit.NOTICE) >= 2


def _rose(path, times, ws=6.0, wd=90.0):
    pd.DataFrame({"time": times, "ws": ws, "wd": wd}).to_csv(path, index=False)
    return path


def test_the_rose_file_is_one_table_and_a_year_of_hours_by_the_clock(tmp_path):
    """Outside retest of 0.3.2-dev (23 Nov 2026). An archive with two tables gave an east or a west rose by the order
    of its members; 8,760 one-minute rows, and 8,760 rows whose times were not times, passed for a year."""
    import zipfile  # noqa: PLC0415
    year = pd.date_range("2025-01-01", periods=8760, freq="h")
    east, west = _rose(tmp_path / "point0.csv", year, 4.0, 90.0), _rose(tmp_path / "point1.csv", year, 12.0, 270.0)
    said = []
    for name, order in (("east-first.zip", (east, west)), ("west-first.zip", (west, east))):
        with zipfile.ZipFile(tmp_path / name, "w") as z:
            for f in order:
                z.write(f, f.name)
        with pytest.raises(summary.SummaryError, match="holds 2 CSV files") as e:
            summary.read_directions(tmp_path / name)
        said.append(str(e.value).replace(name, ""))
    assert said[0] == said[1]                                            # either order: the same refusal
    with zipfile.ZipFile(tmp_path / "one.zip", "w") as z:
        z.write(east, east.name)
    one = summary.read_directions(tmp_path / "one.zip")                 # an archive with one table still loads
    assert len(one) == 8760 and one["ws"].mean() == pytest.approx(4.0) and one["wd"].iloc[0] == pytest.approx(90.0)

    with pytest.raises(summary.SummaryError, match="146 different hours"):                            # six days of minutes
        summary.read_directions(_rose(tmp_path / "minutes.csv", pd.date_range("2025-01-01", periods=8760, freq="min")))
    with pytest.raises(summary.SummaryError, match="could not be read as dates and times"):
        summary.read_directions(_rose(tmp_path / "bad.csv", [f"bad-{i}" for i in range(8760)]))
    with pytest.raises(summary.SummaryError, match="8,000 different hours"):                          # short of a year
        summary.read_directions(_rose(tmp_path / "short.csv", year[:8000]))
    three = pd.date_range("2023-01-01", "2025-12-31 23:00", freq="h")
    with pytest.raises(summary.SummaryError, match="has gaps"):                                        # a year of hours spread over three
        summary.read_directions(_rose(tmp_path / "gapped.csv", three[::3]))
    assert len(summary.read_directions(_rose(tmp_path / "hourly.csv", year))) == 8760
    ten = pd.date_range("2025-01-01", periods=8760 * 6, freq="10min")                                  # ten-minute values: six to the hour
    sub = summary.read_directions(_rose(tmp_path / "ten.csv", ten, np.tile([2.0, 4.0, 6.0, 8.0, 10.0, 12.0], 8760), 90.0))
    assert len(sub) == 8760 and sub["ws"].to_numpy() == pytest.approx(7.0) and sub["wd"].to_numpy() == pytest.approx(90.0)
    # every hour counts once, however often it was sampled: a second year at ten minutes does not outweigh an hourly first
    mixed = pd.concat([pd.DataFrame({"time": year, "ws": 6.0, "wd": 90.0}),
                       pd.DataFrame({"time": pd.date_range("2026-01-01", periods=8760 * 6, freq="10min"), "ws": 6.0, "wd": 270.0})])
    mixed.to_csv(tmp_path / "mixed.csv", index=False)
    m = summary.read_directions(tmp_path / "mixed.csv")
    assert (m["wd"].round() == 90).mean() == pytest.approx(0.5)


def test_a_repeated_row_is_one_record_and_two_winds_at_one_time_are_refused_in_both_readers(tmp_path):
    """Outside retest of 0.3.2-dev: the same row twice was taken by the long-term reader and refused by the rose
    reader. Both now follow one rule, on the time and on everything read of the wind."""
    from bohemian_wind import longterm  # noqa: PLC0415
    t = pd.date_range("2020-01-01", "2022-12-31 23:00", freq="h")
    uv = pd.DataFrame({"valid_time": t, "u100": -3.0, "v100": 4.0})
    sd = pd.DataFrame({"time": t, "ws": 5.0, "wd": 90.0})
    for frame, name in ((uv, "uv"), (sd, "sd")):
        twice = tmp_path / f"{name}-twice.csv"
        pd.concat([frame, frame]).to_csv(twice, index=False)                                   # exact repeats: one record each
        assert len(summary.read_directions(twice)) == len(t)
        if name == "uv":
            assert longterm.read_reference_file(twice)[0].mean() == pytest.approx(5.0)
    turned = uv.assign(u100=3.0, v100=-4.0)                                                    # the same speed from the opposite side
    clash = tmp_path / "uv-clash.csv"
    pd.concat([uv, turned]).to_csv(clash, index=False)
    with pytest.raises(ValueError, match="more than once with different wind"):
        longterm.read_reference_file(clash)
    with pytest.raises(summary.SummaryError, match="more than once with different wind"):
        summary.read_directions(clash)
    clash2 = tmp_path / "sd-clash.csv"
    pd.concat([sd, sd.assign(wd=270.0)]).to_csv(clash2, index=False)                           # the same speed, another direction
    with pytest.raises(summary.SummaryError, match="more than once with different wind"):
        summary.read_directions(clash2)
