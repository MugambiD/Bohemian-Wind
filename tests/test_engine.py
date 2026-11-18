"""Known-answer and fault-detection tests. Run: python -m pytest tests -q

Needs brightwind's demo_data.csv (MIT) for the synthetic mast: set BW_DEMO_CSV,
or the synthetic-mast tests are skipped. NRG_SAMPLES points at real SymphoniePRO
export samples (nrgpy test files, MIT) for the format test.
"""
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bohemian_wind.analysis import weibull_fit  # noqa: E402
from bohemian_wind.convert import build_command, convert_tree  # noqa: E402
from bohemian_wind.pipeline import run  # noqa: E402
from bohemian_wind.sympro import parse_column, read_dataset  # noqa: E402

DEMO = os.environ.get("BW_DEMO_CSV", "")
NRG = os.environ.get("NRG_SAMPLES", "")


# ---------- format ----------

def test_column_names():
    c = parse_column("Ch15_Vane_10.00m_SSE_GustDir_Deg")
    assert (c.channel, c.ch_type, c.height, c.bearing, c.stat, c.units) == (15, "Vane", 10.0, "SSE", "GustDir", "Deg")
    c = parse_column("Ch4_Anem___Avg_m/s")
    assert (c.channel, c.height, c.bearing, c.stat, c.units) == (4, None, "", "Avg", "m/s")
    assert parse_column("Ch42_ModbusRTU_1.00m_S_Avg_").units == ""
    assert parse_column("Timestamp") is None


@pytest.mark.skipif(not NRG, reason="NRG_SAMPLES not set")
def test_real_nrg_exports():
    files = sorted(Path(NRG).glob("004310_*_meas.txt"))
    ds = read_dataset(files, log=lambda m: None)
    assert len(ds.data) == 850 and ds.duplicates_removed == 0
    assert ds.site["Latitude"] == "44.5000000"
    assert len(ds.loggers) == 1 and ds.loggers[0]["Serial Number"] == "820604310"
    assert "Ch15_Vane_10.00m_SSE_Avg_Deg" in ds.columns


# ---------- converter command ----------

def test_sympro_command():
    cmd = build_command(r"C:\NRG\SymPRODesktop.exe", Path(r"C:\data\202307"), Path("C:\\out\\"), password="pw")
    assert cmd == (r'"C:\NRG\SymPRODesktop.exe" /cmd convert /file "C:\data\202307\*.rld" '
                   r'/pass "pw" /type "meas" /outputdir "C:\out"')
    assert "/pass" not in build_command("x.exe", Path("a"), Path("b"))


def test_convert_skips_done_folders(tmp_path):
    raw = tmp_path / "raw" / "202401"
    raw.mkdir(parents=True)
    (raw / "000001_2024-01-01_00.00_000001.rld").write_bytes(b"x")
    out = tmp_path / "out"
    out.mkdir()
    (out / "000001_2024-01-01_00.00_000001_meas.txt").write_text("x")
    s = convert_tree(tmp_path / "raw", out, sympro_exe="does-not-run.exe", log=lambda m: None)
    assert s["already_converted"] == 1 and s["folders_run"] == 0 and not s["errors"]


def test_failed_and_converted_files_are_not_retried(tmp_path):
    from bohemian_wind.convert import pending_rld
    raw = tmp_path / "raw" / "202308"
    raw.mkdir(parents=True)
    for n in ("000001_2023-08-03_13.05_000057", "000001_2023-08-03_13.30_000058",
              "000001_2023-08-03_13.33_000059", "000001_2023-08-03_16.42_000074"):
        (raw / f"{n}.rld").write_bytes(b"x")
    out = tmp_path / "out"
    out.mkdir()
    (out / "000001_2023-08-03_13.30_000058_meas.txt").write_text("x")
    (out / "000001_2023-08-03_13.05_000057_meas.log").write_text("Error: no scaling values found")
    (out / "000001_2023-08-03_16.42_000074_meas.log").write_text("Decryption failed")
    left = sorted(p.name for p in pending_rld(tmp_path / "raw", out))
    assert left == ["000001_2023-08-03_13.33_000059.rld", "000001_2023-08-03_16.42_000074.rld"]


# ---------- known answers ----------

def _write_profile(folder: Path, alpha=0.20, ti=0.12, days=20):
    """Exact power-law profile and constant TI in SymphoniePRO export format."""
    idx = pd.date_range("2024-01-01", periods=days * 144, freq="10min")
    rng = np.random.default_rng(1)
    u80 = rng.weibull(2.0, len(idx)) * 8.0 + 3.0
    wd = rng.uniform(0, 360, len(idx))
    cols = {}
    for ch, h in ((1, 80.0), (3, 60.0), (5, 40.0)):
        u = u80 * (h / 80.0) ** alpha
        b = f"Ch{ch}_Anem_{h:.2f}m_N"
        cols[f"{b}_Avg_m/s"], cols[f"{b}_SD_m/s"] = u, ti * u
        cols[f"{b}_Min_m/s"], cols[f"{b}_Max_m/s"] = 0.5 * u, 1.5 * u
    cols["Ch13_Vane_78.00m_N_Avg_Deg"], cols["Ch13_Vane_78.00m_N_SD_Deg"] = wd, np.full(len(idx), 8.0)
    df = pd.DataFrame(cols, index=idx)
    df.insert(0, "Timestamp", idx.strftime("%Y-%m-%d %H:%M:%S"))
    head = "SymphoniePRO Desktop Application\n\nSite Properties\nSite Description:\tProfile test\nElevation:\t100\n\nSensor History\nData\n"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "profile_meas.txt").write_text(head + df.to_csv(sep="\t", index=False, lineterminator="\n"))


def test_shear_and_ti_known_answers(tmp_path):
    _write_profile(tmp_path / "in")
    an = run(tmp_path / "in", tmp_path / "out", log=lambda m: None, export_timeseries=False)["context"]["an"]
    assert an["shear"]["alpha_fit"] == pytest.approx(0.20, abs=1e-9)
    assert an["shear"]["alpha_top_bottom"] == pytest.approx(0.20, abs=1e-9)
    assert an["shear"]["alpha_top_pair"] == pytest.approx(0.20, abs=1e-9) and an["shear"]["top_pair"] == (60.0, 80.0)
    ti = an["ti"]
    assert np.allclose(ti.loc[ti["count"] >= 10, "mean_TI"], 0.12, atol=1e-9)
    assert np.allclose(ti.loc[ti["count"] >= 10, "rep_TI"], 0.12, atol=1e-9)


def test_weibull_recovers_parameters():
    x = np.random.default_rng(7).weibull(2.2, 200_000) * 8.5
    A, k = weibull_fit(x)
    assert A == pytest.approx(8.5, rel=0.01) and k == pytest.approx(2.2, rel=0.01)


# ---------- planted faults on a real-wind synthetic mast ----------

@pytest.fixture(scope="module")
def mast(tmp_path_factory):
    if not DEMO:
        pytest.skip("BW_DEMO_CSV not set")
    from make_synthetic import make
    root = tmp_path_factory.mktemp("synthetic")
    make(DEMO, root / "mast")
    return run(root / "mast", root / "out", log=lambda m: None, export_timeseries=False)["context"]


def _flag(ctx, label, name, a, b=None):
    s = ctx["qc"]["flags"][label][name]
    return s.loc[a:b or a]


def test_gap_found(mast):
    a, b = (pd.Timestamp(x) for x in ("2016-05-10 00:00", "2016-05-12 23:50"))
    assert any(g[0] <= a and g[1] >= b for g in mast["qc"]["time"]["gaps"])


def test_stuck_cup_found(mast):
    assert _flag(mast, "WS 60 m S (Ch4)", "stuck", "2016-07-01 00:00", "2016-07-02 23:50").all()


def test_stuck_vane_found(mast):
    # flagged only while the wind blows; brightwind documents this vane as failed from 2016-12-26
    assert _flag(mast, "WD 58 m S (Ch14)", "stuck", "2016-08-15 00:00", "2016-08-15 23:50").sum() >= 50
    assert _flag(mast, "WD 58 m S (Ch14)", "stuck", "2016-12-27", "2017-02-28").mean() > 0.7


def test_spike_range_duplicate(mast):
    assert _flag(mast, "WS 80 m N (Ch1)", "spike", "2016-09-10 12:00").all()
    assert _flag(mast, "T 2 m N (Ch16)", "range", "2016-10-01 00:00").all()
    assert mast["ds"].duplicates_removed == 1


def test_config_change_found(mast):
    ch = mast["changes"]
    two = ch[ch["Channel"] == "2"]
    assert two["changed"].all() and set(two["Serial Number"]) == {"94120002", "94120002R"}


def test_cup_drift_found(mast):
    pair = next(p for p in mast["an"]["pairs"] if p["height"] == 40.0)
    months = dict(pair["drift_months"])
    assert set(months) >= {"2016-12", "2017-01", "2017-02"}
    assert all(-4.0 < v < -2.0 for v in months.values())  # planted -3 %; months vary about ±0.6 %


def test_no_false_inconsistency(mast):
    s = mast["flag_summary"].set_index("sensor")
    assert s["inconsistent"].sum() == 0


def test_rewired_cup_followed_by_serial(mast):
    """The 40 m N cup sat on Ch7 as '40 m NNE' with a generic calibration until the visit."""
    from make_synthetic import build_frame, load_demo
    labels = [s.label for s in mast["sensors"]]
    assert "WS 40 m N (Ch5)" in labels and not any("NNE" in x or "SSW" in x for x in labels)
    truth = build_frame(load_demo(DEMO))
    got = mast["ds"].data.loc["2016-02-01":"2016-02-29", "WS 40 m N (Ch5)|Avg"]
    want = truth.loc[got.index, "Ch5_Anem_40.00m_N_Avg_m/s"]
    assert got.notna().sum() > 4000 and np.allclose(got, want, atol=2e-4, equal_nan=True)
    seg = mast["ds"].points["segments"]
    row = seg[seg["channel"] == 7].iloc[0]
    assert row["point"] == "WS 40 m N (Ch5)" and "moved from Ch7 to Ch5" in row["action"] and "re-scaled" in row["action"]
    assert "WS 40 m N (Ch5)" in mast["ds"].points["moved"]


def test_vane_offset_re_referenced(mast):
    from make_synthetic import build_frame, load_demo
    truth = build_frame(load_demo(DEMO))
    got = mast["ds"].data.loc["2016-02-01":"2016-02-29", "WD 78 m S (Ch13)|Avg"]
    want = truth.loc[got.index, "Ch13_Vane_78.00m_S_Avg_Deg"] % 360.0
    diff = ((got - want + 180.0) % 360.0 - 180.0).abs()
    assert got.notna().sum() > 4000 and diff.max() < 1e-3


def test_site_visit_is_maintenance(mast):
    f = mast["qc"]["flags"]["WS 80 m N (Ch1)"]["maintenance"]
    assert f.loc["2016-03-01 15:00":"2016-03-01 17:00"].all()
    assert not f.loc["2016-03-02":"2016-03-31"].any()
    assert len(mast["ds"].points["visits"]) == 2  # the visit and the Ch2 cup swap


def test_no_signal_and_site_pressure(mast):
    assert _flag(mast, "RH 2 m N (Ch17)", "no_signal", "2016-04-01 00:00", "2016-04-03 23:50").all()
    assert not _flag(mast, "RH 2 m N (Ch17)", "no_signal", "2016-04-05", "2016-04-30").any()
    assert _flag(mast, "P 2 m N (Ch18)", "range", "2016-10-15 00:00", "2016-10-15 03:00").all()


def test_run_log_written_as_it_goes(tmp_path):
    from bohemian_wind.pipeline import PipelineError
    (tmp_path / "empty").mkdir()
    (tmp_path / "empty" / "notes.txt").write_text("nothing here")
    with pytest.raises(PipelineError):
        run(tmp_path / "empty", tmp_path / "out", log=lambda m: None)
    logs = list((tmp_path / "out" / "runs").rglob("run.log"))
    assert logs and "Found 0 RLD files" in logs[0].read_text()


def test_two_sites_refused(tmp_path):
    for n, site in enumerate(("000001", "000002")):
        _write_profile(tmp_path / f"m{n}", days=2)
        f = tmp_path / f"m{n}" / "profile_meas.txt"
        f.write_text(f.read_text().replace("Site Properties\n", f"Site Properties\nSite Number:\t{site}\n"))
    (tmp_path / "notes.txt").write_text("not an export")
    from bohemian_wind.pipeline import PipelineError
    with pytest.raises(PipelineError, match="more than one site"):
        run(tmp_path, tmp_path / "out", log=lambda m: None)


# ---------- v0.1.2: overlapping files, one mast and one clock, settings, fingerprints, shear test ----------

def test_conflicting_records_quarantined(mast):
    from make_synthetic import PLANTED
    t = pd.Timestamp(PLANTED["conflict"]) - pd.Timedelta(minutes=10)
    ds = mast["ds"]
    assert list(ds.conflict_times) == [t] and ds.conflict_rows == 2
    assert ds.duplicates_removed == 1  # the identical repeat is still merged, not quarantined
    assert ds.data.loc[t].isna().all()
    f = mast["qc"]["flags"]["WS 80 m N (Ch1)"]
    assert f["conflict"].loc[t] and not f["missing"].loc[t] and int(f["conflict"].sum()) == 1
    assert not mast["qc"]["valid"]["WS 80 m N (Ch1)"].loc[t]
    assert len(ds.conflicts) == 1 and ds.conflicts.iloc[0]["records"] == 2


def test_merge_duplicates_rules():
    from bohemian_wind.sympro import merge_duplicates
    t = pd.to_datetime(["2024-01-01 00:00", "2024-01-01 00:00", "2024-01-01 00:10", "2024-01-01 00:10",
                        "2024-01-01 00:20", "2024-01-01 00:20", "2024-01-01 00:30"])
    df = pd.DataFrame({"a": [5.0, np.nan, 6.0, 6.0, 7.0, 7.5, 8.0], "b": [np.nan, 1.0, 2.0, np.nan, 3.0, 3.0, 4.0],
                       "_src": [0, 1, 2, 3, 4, 5, 6]}, index=t)
    out, removed, ct, rows, tab = merge_duplicates(df, [f"f{i}" for i in range(7)])
    # 00:00: values present in one copy only do not conflict; 00:10 identical; 00:20 differs
    assert removed == 2 and list(ct) == [pd.Timestamp("2024-01-01 00:20")] and rows == 2
    assert len(out) == 4 and out.index.is_unique
    assert out.loc["2024-01-01 00:10", "b"] == 2.0  # the most complete copy is kept
    assert out.loc["2024-01-01 00:20", ["a", "b"]].isna().all() and out.loc["2024-01-01 00:30", "a"] == 8.0
    assert tab.iloc[0]["files"] == "f4, f5" and tab.iloc[0]["differing_columns"] == 1


def _set_site(folder: Path, lat=None, lon=None, tz=None, number=None):
    f = folder / "profile_meas.txt"
    extra = "".join(f"{k}:\t{v}\n" for k, v in (("Site Number", number), ("Latitude", lat), ("Longitude", lon),
                                                 ("Time Zone", tz)) if v is not None)
    f.write_text(f.read_text().replace("Site Properties\n", "Site Properties\n" + extra))


def _two_masts(tmp_path, a: dict, b: dict):
    for n, kw in enumerate((a, b)):
        _write_profile(tmp_path / f"m{n}", days=2)
        _set_site(tmp_path / f"m{n}", **kw)
        if n:  # the second mast continues the first one's record
            f = tmp_path / "m1" / "profile_meas.txt"
            f.write_text(f.read_text().replace("2024-01-0", "2024-02-0"))


def test_same_site_number_different_place_refused(tmp_path):
    from bohemian_wind.pipeline import PipelineError
    _two_masts(tmp_path, dict(lat="1.2000000", lon="35.4000000", number="000001"),
               dict(lat="-3.5000000", lon="39.0000000", number="000001"))
    with pytest.raises(PipelineError, match="mixes masts"):
        run(tmp_path, tmp_path / "out", log=lambda m: None)


def test_different_time_zones_refused(tmp_path):
    from bohemian_wind.pipeline import PipelineError
    _two_masts(tmp_path, dict(lat="1.2", lon="35.4", tz="UTC+3:00"), dict(lat="1.2", lon="35.4", tz="UTC+0:00"))
    with pytest.raises(PipelineError, match="time zones"):
        run(tmp_path, tmp_path / "out", log=lambda m: None)


def test_small_coordinate_change_reported(tmp_path):
    # 0.002° of latitude is about 220 m: same mast, coordinates corrected; reported, not refused
    _two_masts(tmp_path, dict(lat="1.2000000", lon="35.4"), dict(lat="1.2020000", lon="35.4"))
    ctx = run(tmp_path, tmp_path / "out", log=lambda m: None, export_timeseries=False)["context"]
    assert 200 < ctx["ds"].site_checks["spread_m"] < 240
    from bohemian_wind.report import findings
    assert any("coordinates changed" in x for x in findings(ctx))


def test_site_settings_file(tmp_path):
    import json
    from bohemian_wind.pipeline import PipelineError, default_settings
    from bohemian_wind.qc import site_pressure
    _write_profile(tmp_path / "in", days=2)
    sf = default_settings(tmp_path / "in", tmp_path / "out")
    sf.parent.mkdir(parents=True)
    sf.write_text(json.dumps({"elevation_m": 1500, "site_name": "Settings test", "source": "survey"}))
    res = run(tmp_path / "in", tmp_path / "out", log=lambda m: None, export_timeseries=False)
    ctx = res["context"]
    ds = ctx["ds"]
    assert ds.site["Elevation"] == "1500" and ds.site_logged["Elevation"] == "100"
    assert ctx["site_name"] == "Settings test"
    assert site_pressure(ds.site) == pytest.approx(845.6, abs=0.5)
    html_text = res["report"].read_text(encoding="utf-8")
    assert "1500 (site settings; logger files: 100)" in html_text and "Site settings file applied" in html_text
    sf.write_text(json.dumps({"elevaton_m": 1500}))  # misspelt key: refuse rather than ignore
    with pytest.raises(PipelineError, match="unknown setting elevaton_m"):
        run(tmp_path / "in", tmp_path / "out", log=lambda m: None)


def test_inputs_fingerprinted(tmp_path):
    import hashlib
    import json
    _write_profile(tmp_path / "in", days=2)
    res = run(tmp_path / "in", tmp_path / "out", log=lambda m: None, export_timeseries=False)
    inv = pd.read_csv(res["run_dir"] / "inputs.csv")
    want = hashlib.sha256((tmp_path / "in" / "profile_meas.txt").read_bytes()).hexdigest()
    assert list(inv["file"]) == ["profile_meas.txt"] and inv.loc[0, "sha256"] == want
    meta = json.loads((res["run_dir"] / "run.json").read_text())
    raw = (res["run_dir"] / "inputs.csv").read_bytes()
    assert meta["inputs"]["inventory_sha256"] == hashlib.sha256(raw).hexdigest() and b"\r" not in raw


def test_shear_test_known_answer(tmp_path):
    _write_profile(tmp_path / "in")
    an = run(tmp_path / "in", tmp_path / "out", log=lambda m: None, export_timeseries=False)["context"]["an"]
    st = an["shear_test"].set_index("from_m")
    assert set(st.index) == {40.0, 60.0}
    assert np.allclose(st["alpha_train"], 0.20, atol=1e-9) and np.allclose(st["alpha_test"], 0.20, atol=1e-9)
    assert (st["MAE_mps"] < 1e-9).all() and (st["baseline_MAE_mps"] > 0.1).all()
    assert np.allclose(st["alpha_all_heights"], 0.20, atol=1e-9) and (st["all_heights_mean_error_%"].abs() < 1e-7).all()
    assert (st["test_records"] / (st["train_records"] + st["test_records"])).between(0.19, 0.21).all()


def test_flag_periods_order_is_deterministic():
    from bohemian_wind.qc import flag_periods
    idx = pd.date_range("2024-01-01", periods=10, freq="10min")
    def flags(on):
        f = {k: pd.Series(False, index=idx) for k in ("missing", "conflict", "maintenance", "no_signal", "range",
                                                       "inconsistent", "stuck", "spike")}
        f["maintenance"] = pd.Series(on, index=idx)
        return f
    run_ = [True, True, True] + [False] * 7
    qc = {"flags": {name: flags(run_) for name in ("WS 80 m W (Ch1)", "T 9 m W (Ch16)", "RH 9 m W (Ch17)")}}
    got = flag_periods(qc)
    assert list(got["sensor"]) == ["RH 9 m W (Ch17)", "T 9 m W (Ch16)", "WS 80 m W (Ch1)"]  # ties: by sensor name
