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
    assert zipfile.ZipFile(p.folder / wm["kmz"]).read("doc.kml")
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
    assert "monthly summary tables" in summ and "ten-minute record was checked" in summ
    from bohemian_wind.reports import kit  # noqa: PLC0415
    for t in (wra, summ):                                                                       # [COMPLIANCE] on every report
        assert t.count(kit.NOTICE) >= 2
