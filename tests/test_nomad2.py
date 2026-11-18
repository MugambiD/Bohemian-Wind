"""Nomad2 Multi-Track export reader: known answers on a small export written here (no client data).

Run: python -m pytest tests/test_nomad2.py -q
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import nomad2  # noqa: E402
import toa5_mast  # noqa: E402

HEADER = ("TimeStamp,C1-m_anemo_80mw(m/s)-10 min  Average,C1-m_anemo_80mw(m/s)-10 min  Max Value,"
          "C1-m_anemo_80mw(m/s)-10 min  Std Dev,C2-m_anemo_50m(m/s)-10 min  Average,A1-old_gill_(m/s)-10 min  Average,"
          "A4-Thies(°)-10 min  Vec Average,A4-Thies(°)-10 min  Vec Std Dev,A7-Humidity(%RH)-10 min  Average,"
          "A7-Humidity(%RH)-10 min  Sample,BAT1-Battery 1(V)-10 min  Average,A6-Setra 276(mB)-10 min  Sampl")


def write_export(path: Path, n=288, missing=(10, 11, 12), encoding="latin-1") -> pd.DataFrame:
    """n ten-minute records from 2012-02-25 23:00, so the series crosses midnight twice. Returns the truth."""
    t = pd.date_range("2012-02-25 23:00", periods=n, freq="10min")
    k = np.arange(n)
    truth = pd.DataFrame({"v80": 6.0 + 0.01 * k, "mx": 9.0 + 0.01 * k, "sd": 0.8 + 0 * k, "v50": 5.0 + 0.01 * k,
                          "wd": (350.0 + k) % 360.0, "wsd": 8.0 + 0 * k, "rh": 60.0 + 0.1 * (k % 50),
                          "bat": 9.4 + 0 * k, "pa": 760.0 + 0.01 * k}, index=t)
    lines = ['"Multi-Track Export - 12345 Test_1"', "", '"Site Name: Test_1"', "Nomad2 Name:12345", "", "Notes: ", "",
             HEADER]
    for i, ts in enumerate(t):
        stamp = f"{ts:%d.%m.%Y}" if (ts.hour, ts.minute) == (0, 0) else f"{ts:%d.%m.%Y %H:%M:%S}"
        r = truth.iloc[i]
        if i in missing:
            vals = [0] * 11
        else:
            rh_avg, rh_smp = (r.rh, 0) if i < 20 else (0, r.rh)       # the humidity channel was set up again
            bat = 0 if i < 20 else r.bat                               # battery logged only from record 20
            vals = [r.v80, r.mx, r.sd, r.v50, 0, r.wd, r.wsd, rh_avg, rh_smp, bat, r.pa]
        lines.append(f'"{stamp}",' + ", ".join(f"{v:.6g}" for v in vals) + ", ")
    path.write_bytes(("\r\n".join(lines) + "\r\n").encode(encoding))
    truth.loc[t[list(missing)]] = np.nan
    return truth


def test_header_names():
    p = nomad2.parse_header("C1-p1_anemo_80mw(m/s)-10 min  Average")
    assert (p["port"], p["prefix"], p["height"], p["boom"], p["stat"], p["minutes"]) == ("C1", "V", 80.0, "W", "Avg", 10)
    assert nomad2.parse_header("A4-Thies(°)-10 min  Vec Std Dev")["prefix"] == "D"
    assert nomad2.parse_header("A4-Thies(°)-10 min  Vec Std Dev")["stat"] == "Std"
    assert nomad2.parse_header("A3-SWI 10k Probe(°C)-10 min  Average")["prefix"] == "TA"
    assert nomad2.parse_header("A3-SWI 10k Probe(°C)-10 min  Average")["height"] is None   # "10k" is not a height
    assert nomad2.parse_header("A6-p1_baro-10 min  Sample")["prefix"] == "PA"                  # no unit given
    assert nomad2.parse_header("A6-Setra 276(mB)-10 min  Sampl")["stat"] == "Avg"              # header cut short
    assert nomad2.parse_header("A1-p1_gill_76m(m/s)-10 min  Max Value")["prefix"] == "W"       # vertical anemometer
    assert nomad2.parse_header("XPwr-12V Power(V)-10 min  Averag")["prefix"] is None
    assert nomad2.parse_header("TimeStamp") is None


def test_read_known_answers(tmp_path):
    f = tmp_path / "test_1_raw.dat"
    truth = write_export(f)
    assert nomad2.looks_like_nomad2(f)
    info, df = nomad2.read_nomad2(f, {"A4": {"height_m": 78}, "a6": {"height_m": 2}})
    assert info["station"] == "12345 Test_1" and info["format"] == nomad2.FORMAT and info["bad_lines"] == 0
    # records of zeros are missing records; midnight stamps without a time are read
    assert len(df) == 288 - 3 and info["empty_records"] == 3
    assert pd.Timestamp("2012-02-26 00:00") in df.index and pd.Timestamp("2012-02-27 00:00") in df.index
    assert df.index.is_monotonic_increasing and (df.index.to_series().diff().dropna() >= pd.Timedelta("10min")).all()
    ok = truth.dropna()
    for col, src in (("V1_80m_Avg", "v80"), ("V1_80m_Max", "mx"), ("V1_80m_Std", "sd"), ("V2_50m_Avg", "v50"),
                     ("D1_78m_Avg", "wd"), ("D1_78m_Std", "wsd"), ("PA1_2m_Avg", "pa")):
        assert np.allclose(df[col].to_numpy(), ok[src].to_numpy(), atol=1e-4), col
    # the channel that never logged is dropped; the two humidity set-ups are one series
    assert not [c for c in df.columns if c.startswith("W")]
    assert np.allclose(df["RH1_0m_Avg"].to_numpy(), ok["rh"].to_numpy(), atol=1e-4)
    # zeros before the battery channel was set up are blanks, not readings
    assert df["U_Batt_Avg"].iloc[:17].isna().all() and np.isclose(df["U_Batt_Avg"].iloc[-1], 9.4)
    notes = " ".join(info["notes"])
    assert "3 records are zeros in every column" in notes and "A1 old_gill_" in notes
    assert "A7 Humidity" in notes and "carried at 0 m" in notes and "V1 = W" in notes


def test_vane_offset_and_utf8(tmp_path):
    f = tmp_path / "test_1_raw.dat"
    truth = write_export(f, encoding="utf-8").dropna()
    _, df = nomad2.read_nomad2(f, {"A4": {"height_m": 78, "direction_offset_deg": -28}})
    assert np.allclose(df["D1_78m_Avg"].to_numpy(), ((truth["wd"] - 28.0) % 360.0).to_numpy(), atol=1e-4)
    assert np.allclose(df["D1_78m_Std"].to_numpy(), truth["wsd"].to_numpy(), atol=1e-4)   # spread is not turned


def test_not_nomad2(tmp_path):
    f = tmp_path / "other.dat"
    f.write_text("2012,02,25,00,00, -999.00, -999.00\n")
    assert not nomad2.looks_like_nomad2(f)
    with pytest.raises(ValueError):
        nomad2.read_nomad2(f)


def test_runs_through_the_kit(tmp_path):
    """The whole screening run reads a file given by name and reports the format's notes as findings."""
    f = tmp_path / "data" / "test_1_raw.dat"
    f.parent.mkdir()
    write_export(f, n=144 * 12, missing=tuple(range(300, 330)))
    (tmp_path / "data" / "test_1_clean.dat").write_text("2012,02,25,00,00, -999.00\n")   # ignored: file given by name
    s = tmp_path / "site.json"
    s.write_text('{"site_name": "Test 1", "elevation_m": 2300, "channels": {"A4": {"height_m": 78}}}')
    run = toa5_mast.main([str(f), "--out", str(tmp_path / "out"), "--name", "Test 1", "--settings", str(s)])
    stats = __import__("json").loads((run / "stats.json").read_text("utf-8"))
    assert stats["station"] == "12345 Test_1"
    assert any("30 records are zeros in every column" in x for x in stats["findings"])
    ts = pd.read_csv(run / "timeseries_by_height.csv", index_col=0, parse_dates=True)
    assert {"ws_80m", "ws_50m", "wd_78m"} <= set(ts.columns) and len(ts) == 144 * 12 - 30
    assert "data file,test_1_raw.dat" in (run / "inputs.csv").read_text("utf-8")


def test_two_masts_in_one_folder_refused(tmp_path):
    write_export(tmp_path / "a_raw.dat")
    text = (tmp_path / "a_raw.dat").read_bytes().replace(b"12345", b"54321").replace(b"Test_1", b"Test_2")
    (tmp_path / "b_raw.dat").write_bytes(text)
    with pytest.raises(SystemExit) as e:
        toa5_mast.load([tmp_path], log=lambda m: None)
    assert "more than one station" in str(e.value)
