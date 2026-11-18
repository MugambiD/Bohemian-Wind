"""NRG Symphonie text export reader (Symphonie Data Retriever): known answers on exports written here.
Every number, name and position in this file is invented.

Run: python -m pytest tests/test_nrg_sdr.py -q
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bohemian_wind import intake  # noqa: E402
from bohemian_wind.loggers import nrg_sdr, toa5_mast  # noqa: E402

# channel: (type, description, serial, height, scale, offset, units)
CHANNELS = {
    1: ("1", "NRG #40 Anem. m/s", "SN:1001", "60 m", "0.765", "0.35", "m/s"),
    2: ("1", "NRG #40 Anem. m/s", "SN:1002", "40 m", "0.765", "0.35", "m/s"),
    3: ("1", "NRG #40 Anem. m/s", "SN:", "0", "0.765", "0.35", "m/s"),             # default settings, nothing connected
    4: ("0", "No SCM Installed", "--------", "------", "0", "0", "-----"),
    5: ("0", "No SCM Installed", "--------", "------", "0", "0", "-----"),
    6: ("0", "No SCM Installed", "--------", "------", "0", "0", "-----"),
    7: ("3", "#200P Wind Vane", "SN:", "58 m", "0.351", "0", "deg"),
    8: ("0", "No SCM Installed", "--------", "------", "0", "0", "-----"),
    9: ("4", "NRG #110S Temp C", "SN:", "3 m", "0.136", "-86.383", "C"),
    10: ("4", "BP-20 Barom. mb", "SN:2201", "3 m", "0.426", "650.875", "mb"),
    11: ("4", "Voltage", "SN:0421", "2 m", "0.021", "0", "Volt"),
    12: ("0", "No SCM Installed", "--------", "------", "0", "0", "-----"),
}


def truth(start="2019-03-01 00:00", n=144 * 3, seed=11) -> pd.DataFrame:
    """Invented records, already rounded the way the export rounds them."""
    rng = np.random.default_rng(seed)
    t = pd.date_range(start, periods=n, freq="10min")
    k = np.arange(n)
    v60 = 7.0 + 2.5 * np.sin(2 * np.pi * k / 144.0) + rng.normal(0, 0.6, n)
    v60 = np.clip(v60, 0.4, None)
    df = pd.DataFrame(index=t)
    df["v60"], df["v60sd"] = v60, 0.11 * v60
    df["v60max"], df["v60min"] = 1.3 * v60, 0.7 * v60
    df["v40"], df["v40sd"] = 0.93 * v60, 0.12 * v60
    df["v40max"], df["v40min"] = 1.25 * v60, 0.65 * v60
    df["wd"], df["wdsd"] = (95.0 + 20.0 * np.sin(2 * np.pi * k / 500.0) + rng.normal(0, 4, n)) % 360.0, 6.0 + 0 * k
    df["t"], df["tsd"] = 18.0 + 4.0 * np.sin(2 * np.pi * k / 144.0), 0.2 + 0 * k
    df["p"], df["psd"] = 845.0 + 1.5 * np.sin(2 * np.pi * k / 1000.0), 0.1 + 0 * k
    df["u"] = 12.8 + 0 * k
    return df.round(1)


def write_sdr(path: Path, df: pd.DataFrame, variant: str = "pristine", channels: dict | None = None,
              elevation="1510", speed_factor: float = 1.0, cups: dict | None = None) -> None:
    """variant: 'pristine' (tabs between key and value, m/d/Y stamps, zeros under channels with no module),
    'blank' (as pristine, blanks under those channels) or 'spreadsheet' (every word in its own cell, decimal commas,
    date and time in two cells, the cells of channels with no module closed up).
    cups: {channel: column} for cups beyond CH1 and CH2; df then holds <column>, <column>sd, <column>max, <column>min."""
    ch = {**CHANNELS, **(channels or {})}
    cup_cols = {1: "v60", 2: "v40", **(cups or {})}
    sheet = variant == "spreadsheet"

    def kv(key, value):                                   # a header line
        if sheet:
            return "\t".join((key + " " + value).split()) + "\t\t\t"
        return f"{key}\t{value}"

    def num(x):
        s = f"{x:.1f}".rstrip("0").rstrip(".") if abs(x - round(x)) < 1e-9 else f"{x:.1f}"
        return s.replace(".", ",") if sheet else s

    lines = [kv("SDR", "6.05"), "", "#NAME?\tInformation-----" if sheet else "-----Logger Information-----",
             kv("Model #", "4941"), kv("Serial #", "0421"), kv("Hardware Rev.", "001-002-003"), "",
             "#NAME?\tInformation-----" if sheet else "-----Site Information-----", kv("Site #", "0421"),
             kv("Site Desc", "Test Hill"), kv("Project Code", "T1"), kv("Project Desc", "Invented mast"), kv("Site Location", ""),
             kv("Site Elevation", elevation), kv("Latitude", "N 012? 30.600'" if sheet else "N 012° 30.600'"),
             kv("Longitude", "E 045? 15.300'" if sheet else "E 045° 15.300'"), kv("Time offset (hrs)", "3"), "",
             "#NAME?\tInformation-----" if sheet else "-----Sensor Information-----", ""]
    for n in sorted(ch):
        typ, desc, serial, height, scale, offset, units = ch[n]
        if sheet:
            scale, offset = scale.replace(".", ","), offset.replace(".", ",")
        lines += [kv("Channel #", str(n)), kv("Type", typ), kv("Description", desc), kv("Details", ""),
                  kv("Serial Number", serial), kv("Height", height), kv("Scale Factor", scale), kv("Offset", offset),
                  kv("Units", units), ""]
    cols = [f"CH{n}{s}" for n in sorted(ch) for s in ("Avg", "SD", "Max", "Min")]
    lines.append("\t".join((["Date", "&", "Time", "Stamp"] if sheet else ["Date & Time Stamp"]) + cols))
    f = speed_factor
    for ts, r in df.iterrows():
        cells = {3: [0.4, 0, 0.4, 0.4], 7: [r.wd, r.wdsd, 0, 0], 9: [r.t, r.tsd, r.t + 0.3, r.t - 0.3],
                 10: [r.p, r.psd, r.p + 0.2, r.p - 0.2], 11: [r.u, 0, r.u, r.u]}
        for n, c in cup_cols.items():
            cells[n] = [r[c] * f, r[c + "sd"] * f, r[c + "max"] * f, r[c + "min"] * f]
        row = []
        for n in sorted(ch):
            if ch[n][0] == "0":
                row += [] if sheet else ["0"] * 4 if variant == "pristine" else [""] * 4
            else:
                row += [num(x) for x in cells[n]]
        if sheet:
            row += [""] * (len(cols) - len(row))            # the closed-up row ends in blanks
            lines.append("\t".join([f"{ts:%d.%m.%Y}", f"{ts:%H:%M:%S}"] + row))
        else:
            lines.append("\t".join([f"{ts.month}/{ts.day}/{ts.year} {ts.hour}:{ts:%M:%S}"] + row))
    path.write_bytes(("\r\n".join(lines) + "\r\n").encode("latin-1"))


@pytest.mark.parametrize("variant", ["pristine", "blank", "spreadsheet"])
def test_three_layouts_of_one_export_read_alike(tmp_path, variant):
    df = truth()
    f = tmp_path / f"{variant}.txt"
    write_sdr(f, df, variant)
    assert nrg_sdr.looks_like_sdr(f)
    info, out = nrg_sdr.read_sdr(f)
    assert list(out.index) == list(df.index) and info["bad_lines"] == 0 and info["table"] == "10 min"
    assert set(out.columns) == {"V1_60m_Avg", "V1_60m_Std", "V1_60m_Max", "V1_60m_Min", "V2_40m_Avg", "V2_40m_Std",
                                "V2_40m_Max", "V2_40m_Min", "D7_58m_Avg", "D7_58m_Std", "TA9_3m_Avg", "TA9_3m_Std",
                                "TA9_3m_Max", "TA9_3m_Min", "PA10_3m_Avg", "PA10_3m_Std", "PA10_3m_Max", "PA10_3m_Min",
                                "U_Batt_Avg", "U_Batt_Std", "U_Batt_Max", "U_Batt_Min"}
    for col, src in (("V1_60m_Avg", "v60"), ("V1_60m_Std", "v60sd"), ("V1_60m_Max", "v60max"), ("V1_60m_Min", "v60min"),
                     ("V2_40m_Avg", "v40"), ("D7_58m_Avg", "wd"), ("D7_58m_Std", "wdsd"), ("TA9_3m_Avg", "t"),
                     ("PA10_3m_Avg", "p"), ("U_Batt_Avg", "u")):
        assert np.allclose(out[col].to_numpy(), df[src].to_numpy(), atol=1e-9), (variant, col)
    assert info["station"] == "0421 Test Hill" and info["serial"] == "0421" and "4941" in info["model"]
    assert info["site"]["lat"] == pytest.approx(12.51) and info["site"]["lon"] == pytest.approx(45.255)
    assert info["site"]["elevation_m"] == 1510.0 and info["site"]["utc_offset_h"] == 3.0
    notes = " ".join(info["notes"])
    assert "CH3 NRG #40 Anem. m/s" in notes and "nothing connected" in notes        # default channel, constant reading
    assert info["layout"] == ("closed up" if variant == "spreadsheet" else "in place")
    assert ("saved again from a spreadsheet" in notes) == (variant == "spreadsheet")
    assert ("matched to their channels in order" in notes) == (variant == "spreadsheet")


def test_date_order_is_found_from_the_spacing_of_the_records(tmp_path):
    # 1 to 12 March: day-first and month-first both read every stamp; only one gives ten-minute steps
    df = truth("2019-03-01 00:00", n=144 * 12)
    f = tmp_path / "us.txt"
    write_sdr(f, df, "pristine")
    _, out = nrg_sdr.read_sdr(f)
    assert out.index[0] == pd.Timestamp("2019-03-01 00:00") and out.index[-1] == pd.Timestamp("2019-03-12 23:50")
    assert (out.index.to_series().diff().dropna() == pd.Timedelta("10min")).all()


def test_settings_by_channel_and_units(tmp_path):
    df = truth()
    f = tmp_path / "a.txt"
    write_sdr(f, df, "spreadsheet")
    info, out = nrg_sdr.read_sdr(f, {"ch2": {"height_m": 50}, "CH7": {"direction_offset_deg": 10}})
    assert "V2_50m_Avg" in out.columns and "V2_40m_Avg" not in out.columns
    assert np.allclose(out["D7_58m_Avg"].to_numpy(), ((df["wd"] + 10.0) % 360.0).to_numpy(), atol=1e-9)
    assert np.allclose(out["D7_58m_Std"].to_numpy(), df["wdsd"].to_numpy())                   # the spread is not turned
    notes = " ".join(info["notes"])
    assert "CH2 50 m" in notes and "CH7 #200P Wind Vane +10 deg" in notes
    # a logger set to mph and feet
    g = tmp_path / "b.txt"
    write_sdr(g, df, "pristine", speed_factor=1 / 0.44704,
              channels={1: ("1", "NRG #40 Anem. mph", "SN:1001", "197 ft", "1.711", "0.78", "mph"),
                        2: ("1", "NRG #40 Anem. mph", "SN:1002", "131 ft", "1.711", "0.78", "mph")})
    info, out = nrg_sdr.read_sdr(g)
    col = next(c for c in out.columns if c.startswith("V1_") and c.endswith("_Avg"))
    assert col == f"V1_{197 * 0.3048:g}m_Avg"
    assert np.allclose(out[col].to_numpy(), df["v60"].to_numpy(), atol=0.03)                    # rounding in mph
    assert "converted to metric units" in " ".join(info["notes"])


def test_a_changed_channel_setting_is_a_new_configuration(tmp_path):
    a, b, c = tmp_path / "a.txt", tmp_path / "b.txt", tmp_path / "c.txt"
    write_sdr(a, truth("2019-03-01"), "pristine")
    write_sdr(b, truth("2019-03-04"), "pristine", elevation="1498")                              # a GPS fix is not a setting
    write_sdr(c, truth("2019-03-07"), "pristine", channels={2: ("1", "NRG #40 Anem. m/s", "SN:1002", "50 m", "0.765", "0.35", "m/s")})
    pa, pb, pc = (nrg_sdr.read_sdr(x)[0]["program"] for x in (a, b, c))
    assert pa == pb and pa != pc and pa.startswith("configuration ")


def test_not_an_sdr_export(tmp_path):
    f = tmp_path / "other.txt"
    f.write_text("Timestamp\tCH1Avg\n2019-03-01 00:00\t5.0\n")
    assert not nrg_sdr.looks_like_sdr(f)
    with pytest.raises(ValueError, match="not a Symphonie Data Retriever text export"):
        nrg_sdr.read_sdr(f)
    g = tmp_path / "short.txt"
    df = truth(n=10)
    write_sdr(g, df, "pristine")
    text = g.read_text("latin-1").replace("0\t0\t0\t0\t", "0\t0\t", 3)                           # three rows lose cells
    g.write_text(text, "latin-1")
    info, out = nrg_sdr.read_sdr(g)
    assert len(out) + info["bad_lines"] == 10 and info["bad_lines"] >= 1


def test_runs_through_the_checks_with_the_site_from_the_header(tmp_path):
    """A folder of monthly exports: found by their content, read, checked, and the header's position, elevation and
    clock offset reach the wind climate. Raw .RWD files beside them are named, not read."""
    data = tmp_path / "mast"
    data.mkdir()
    write_sdr(data / "T1_1903.txt", truth("2019-03-01", n=144 * 31, seed=1), "spreadsheet")
    write_sdr(data / "T1_1904.txt", truth("2019-04-01", n=144 * 30, seed=2), "spreadsheet", elevation="1514")
    (data / "042120190301001.RWD").write_bytes(b"\x00" * 64)
    det = intake.detect(data)
    assert det["format"] == "sdr" and det["counts"]["sdr"] == 2 and det["counts"]["rwd"] == 1 and not det["mixed"]
    assert len(det["read_files"]) == 2 and "NRG Symphonie raw .RWD (not read)" in det["found"]
    lines = []
    res = intake.run_checks(data, tmp_path / "out", site_name="Test Hill", log=lines.append)
    assert res["format"] == "NRG Symphonie text export" and res["records"] == 144 * 61
    assert res["lat"] == pytest.approx(12.51) and res["lon"] == pytest.approx(45.255) and res["elevation_m"] == 1512.0
    assert res["top_height_m"] == 60.0 and 6.5 < res["mean_ws"] < 7.5
    assert any("raw .RWD files were not read" in ln for ln in lines)
    found = " | ".join(res["findings"])
    assert "Site data read from the file headers" in found and "logger clock UTC+3" in found
    assert "1510 to 1514 m in the files" in found and "CH3 NRG #40 Anem. m/s" in found
    stats = json.loads((Path(res["run_dir"]) / "stats.json").read_text("utf-8"))
    assert stats["station"] == "0421 Test Hill" and stats["lat"] == pytest.approx(12.51)
    # a position entered for the project wins over the header
    s = tmp_path / "site.json"
    s.write_text(json.dumps({"latitude": 12.6, "longitude": 45.3, "elevation_m": 1500, "source": "survey"}))
    res2 = intake.run_checks(data, tmp_path / "out2", site_name="Test Hill", settings_path=s, log=lambda m: None)
    assert res2["lat"] == 12.6 and res2["elevation_m"] == 1500


def test_folders_that_cannot_be_read_say_why(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "042120190301001.RWD").write_bytes(b"\x00" * 64)
    assert intake.detect(raw)["format"] is None
    with pytest.raises(intake.IntakeError, match="Symphonie Data Retriever"):
        intake.run_checks(raw, tmp_path / "out", log=lambda m: None)
    both = tmp_path / "both"
    both.mkdir()
    write_sdr(both / "a.txt", truth(), "pristine")
    (both / "b.txt").write_text("WindPRO time series export version 1\n45,0m - Test\n45,0\n;Time stamp;MeanWindSpeedUID\n"
                                "Disabled;Time stamp;Mean wind speed\n;dd.MM.yyyy hh:mm;m/s\n;01.03.2019 00:00;5,00\n")
    det = intake.detect(both)
    assert det["mixed"] == ["NRG Symphonie text export", "WindPRO export"]
    with pytest.raises(intake.IntakeError, match="more than one kind"):
        intake.run_checks(both, tmp_path / "out", log=lambda m: None)
    with pytest.raises(SystemExit, match="more than one format"):
        toa5_mast.load([both], log=lambda m: None)


# ---------- what a real campaign throws at the checks: outages, a relabelled cup, a cup that moved ----------

SHEAR = 0.2          # the invented profile: speed at height h = speed at 60 m x (h / 60) ** SHEAR
DEAD = (0.4, 0.0, 0.4, 0.4)      # a cup with no signal: its offset, no spread


def _cup(df: pd.DataFrame, name: str, height: float, dead=None) -> None:
    """Adds a cup at a height on the invented profile; dead: a mask of records in which it gives no signal."""
    v = (df["v60"] * (height / 60.0) ** SHEAR).round(1)
    cols = {name: v, name + "sd": (0.11 * v).round(1), name + "max": (1.3 * v).round(1), name + "min": (0.7 * v).round(1)}
    for (c, x), d in zip(cols.items(), DEAD):
        df[c] = x.where(~dead, d) if dead is not None else x


@pytest.fixture(scope="module")
def campaign(tmp_path_factory):
    """130 days in two files. On day 65 the logger settings change: CH2 goes from 40 m to 50 m in the header while
    the cup stays at 50 m (a label put right), and CH5 really moves from 20 m to 45 m.
    CH3 drops out all the time; CH6 has five short outages; CH13 dies for good on day 62."""
    n, cut = 144 * 130, 144 * 65
    df = truth("2019-03-01", n=n, seed=5)
    k = np.arange(n)
    first = pd.Series(k < cut, index=df.index)
    _cup(df, "v2", 50)                                                # labelled 40 m, then 50 m: it never moved
    _cup(df, "v3", 58, dead=pd.Series(k % 288 >= 18, index=df.index))       # three hours of signal every two days
    _cup(df, "v4", 30)
    _cup(df, "lo", 20)
    _cup(df, "hi", 45)
    for c in ("", "sd", "max", "min"):
        df["v5" + c] = df["lo" + c].where(first, df["hi" + c])           # moved from 20 m to 45 m
    outages = np.zeros(n, bool)
    for day in (10, 30, 50, 80, 100):
        outages[144 * day:144 * day + 48] = True
    _cup(df, "v6", 30, dead=pd.Series(outages, index=df.index))
    _cup(df, "v13", 60, dead=pd.Series(k >= 144 * 62, index=df.index))
    cup = lambda sn, h: ("1", "NRG #40 Anem. m/s", f"SN:{sn}", f"{h} m", "0.765", "0.35", "m/s")  # noqa: E731
    cups = {2: "v2", 3: "v3", 4: "v4", 5: "v5", 6: "v6", 13: "v13"}
    data = tmp_path_factory.mktemp("campaign") / "mast"
    data.mkdir()
    for name, part, h2, h5 in (("T1_a.txt", df.iloc[:cut], 40, 20), ("T1_b.txt", df.iloc[cut:], 50, 45)):
        write_sdr(data / name, part, "pristine", cups=cups,
                  channels={2: cup(1002, h2), 3: cup(1003, 58), 4: cup(1004, 30), 5: cup(1005, h5), 6: cup(1006, 30),
                            13: cup(1013, 60)})
    res = intake.run_checks(data, data.parent / "out", site_name="Test Hill", log=lambda m: None)
    return res, json.loads((Path(res["run_dir"]) / "stats.json").read_text("utf-8"))


def _finding(res: dict, start: str) -> str:
    hits = [f for f in res["findings"] if f.startswith(start)]
    assert len(hits) == 1, (start, hits)
    return hits[0]


def test_a_cup_that_keeps_dropping_out_is_left_out_altogether(campaign):
    res, stats = campaign
    f = _finding(res, "V3 WS 58 m:")
    assert "dead or fixed for 94% of the records it logged (65 spells between 01 Mar 2019 and 08 Jul 2019" in f
    assert "never clear of an outage for 60 days in a row" in f and "left out of the statistics" in f
    assert "58" not in stats["monthly"]                              # no 58 m height in the results
    assert sum(f.startswith("V3 WS 58 m") for f in res["findings"]) == 1      # one line, not 65


def test_a_cup_that_died_once_keeps_its_good_months(campaign):
    res, stats = campaign
    f = _finding(res, "V13 WS 60 m:")
    assert "dead or fixed from 02 May 2019 00:00 to 08 Jul 2019 23:50 (68.0 days; reading 0.40 m/s)" in f
    assert "left out" not in f
    # five short outages: one line for the sensor, the spells in the workbook
    g = _finding(res, "V6 WS 30 m:")
    assert "dead or fixed in 5 spells between 11 Mar 2019 and 09 Jun 2019, 1.7 days in all" in g and "workbook" in g
    assert {"60", "30"} <= set(stats["monthly"])


def test_a_label_put_right_is_told_from_a_cup_that_moved(campaign):
    res, stats = campaign
    relabel = _finding(res, "V2 logged at 40 m")
    assert "and at 50 m from 05 May 2019" in relabel and "V1 WS 60 m + V13 WS 60 m (mean)" in relabel
    assert "The readings did not change with the label" in relabel and "The earlier spell is left out" in relabel
    assert "it read the same before the change as in the same months later" in relabel
    assert "a move from 40 m to 50 m would make it 4.4% lower before than after (shear 0.200)" in relabel
    moved = _finding(res, "V5 logged at 20 m")
    assert "and at 45 m from 05 May 2019" in moved and "That fits a move" in moved
    assert "it read 15.0% lower before the change than in the same months later" in moved
    assert "a move from 20 m to 45 m would make it 15.0% lower before than after" in moved
    # heights in the results: the mislabelled spell is not one of them, both spells of the moved cup are
    assert set(stats["monthly"]) == {"60", "50", "45", "30", "20"}
    # the profile is fitted where the heights were measured together, and says what it leaves out
    assert res["shear_alpha"] == pytest.approx(SHEAR, abs=0.03) and res["shear_basis"].startswith("50-60 m pair")
    assert "never measured together with the rest: 20 m" in res["shear_basis"] and not res["shear_warning"]
    change = _finding(res, "Logger program configuration")
    assert "from 05 May 2019 00:00: added V2_50m, V5_45m; removed V2_40m, V5_20m" in change
    assert "logger settings changed: CH2 height 40 m to 50 m; CH5 height 20 m to 45 m" in change


def test_settings_changes_in_words():
    a = {"CH2": {"Height": "40 m", "Serial Number": "SN:"}, "CH10": {"Height": "3 m"}, "CH4": {"Height": "30 m"}}
    b = {"CH2": {"Height": "50 m", "Serial Number": "SN:7"}, "CH10": {"Height": "3 m"}, "CH5": {"Height": "20 m"}}
    assert toa5_mast.settings_changes(a, b) == ["CH2 height 40 m to 50 m", "CH2 serial number SN: to SN:7",
                                                "CH4 removed", "CH5 added"]
    assert toa5_mast.settings_changes(None, b) == [] and toa5_mast.settings_changes(a, a) == []


def test_a_voltage_channel_left_in_counts_is_not_checked_as_volts(tmp_path):
    df = truth()
    df["u"] = 640.0
    f = tmp_path / "counts.txt"
    write_sdr(f, df, "pristine", channels={11: ("4", "Voltage", "SN:0421", "2 m", "1", "0", "Volt")})
    info, out = nrg_sdr.read_sdr(f)
    assert not [c for c in out.columns if c.startswith("U_Batt")]
    assert "left out of the supply check: CH11 Voltage." in " ".join(info["notes"]) and "raw counts, not volts" in " ".join(info["notes"])
    g = tmp_path / "volts.txt"                                       # scaled in volts: checked as before
    write_sdr(g, truth(), "pristine")
    assert "U_Batt_Avg" in nrg_sdr.read_sdr(g)[1].columns
