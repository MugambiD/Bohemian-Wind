"""WindPRO time series export reader: known answers on exports written here.
Every number and name in this file is invented.

Run: python -m pytest tests/test_windpro.py -q
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bohemian_wind import intake  # noqa: E402
from bohemian_wind.loggers import toa5_mast, windpro  # noqa: E402

MISSING = "-9,99999993381581E+36"


def truth(start="2017-05-01 00:00", n=144 * 4, seed=4) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    t = pd.date_range(start, periods=n, freq="10min")
    k = np.arange(n)
    ws = np.clip(6.5 + 2.0 * np.sin(2 * np.pi * k / 144.0) + rng.normal(0, 0.7, n), 0.3, None).round(2)
    wd = ((120.0 + 25.0 * np.sin(2 * np.pi * k / 700.0) + rng.normal(0, 5, n)) % 360.0).round(1)
    ti = np.clip(0.11 + rng.normal(0, 0.02, n), 0.02, None).round(4)
    return pd.DataFrame({"ws": ws, "wd": wd, "ti": ti}, index=t)


def write_export(path: Path, df: pd.DataFrame, variant="exported", missing=(), disabled=(), gap=None, extra=False) -> None:
    """variant 'exported': ';dd.MM.yyyy hh:mm;values'. 'edited': no leading cell, the date and the time in two cells,
    and now and then an empty cell between them. missing: row numbers written with WindPRO's missing-data code.
    disabled: row numbers marked disabled. gap: (first, last) row numbers left out. extra: one column more."""
    com = lambda x, nd: f"{x:.{nd}f}".replace(".", ",")  # noqa: E731
    names = "Disabled;Time stamp;Mean wind speed;Wind direction;Turbulence intensity" + (";Battery" if extra else "")
    lines = ["WindPRO time series export version 1", "45,0m - Test", "45,0",
             ";Time stamp;MeanWindSpeedUID;DirectionUID;TurbIntUID" + (";BatUID" if extra else ""), names,
             ";dd.MM.yyyy hh:mm;m/s;Degrees;" + (";V" if extra else "")]
    for i, (ts, r) in enumerate(df.iterrows()):
        if gap and gap[0] <= i <= gap[1]:
            continue
        vals = [MISSING] * 3 if i in missing else [com(r.ws, 2), com(r.wd, 1), com(r.ti, 4)]
        if extra:
            vals.append("12,6")
        if variant == "exported":
            lines.append(";".join(["1" if i in disabled else "", f"{ts:%d.%m.%Y %H:%M}"] + vals))
        else:
            sep = ";;" if i % 97 == 5 else ";"
            lines.append(f"{ts:%d.%m.%Y}{sep}{ts:%H:%M};" + ";".join(vals))
        lines.append("")                                  # edited files often carry a blank line after every record
    path.write_bytes("\n".join(lines).encode("latin-1"))


@pytest.mark.parametrize("variant", ["exported", "edited"])
def test_export_and_edited_export_read_alike(tmp_path, variant):
    df = truth()
    f = tmp_path / f"{variant}.txt"
    write_export(f, df, variant, missing=(7, 8))
    assert windpro.looks_like_windpro(f)
    info, out = windpro.read_windpro(f)
    assert list(out.index) == list(df.index) and info["bad_lines"] == 0 and info["table"] == "10 min"
    assert list(out.columns) == ["V1_45m_Avg", "V1_45m_Std", "D1_45m_Avg"]
    ok = np.ones(len(df), bool)
    ok[[7, 8]] = False
    assert out["V1_45m_Avg"].isna().to_numpy().tolist() == (~ok).tolist() and info["missing_values"] == 6
    assert np.allclose(out["V1_45m_Avg"].to_numpy()[ok], df["ws"].to_numpy()[ok])
    assert np.allclose(out["D1_45m_Avg"].to_numpy()[ok], df["wd"].to_numpy()[ok])
    assert np.allclose(out["V1_45m_Std"].to_numpy()[ok], (df["ti"] * df["ws"]).to_numpy()[ok])      # SD = TI x mean
    notes = " ".join(info["notes"])
    assert "6 values at WindPRO's missing-data code" in notes and "rebuilt as turbulence intensity x mean wind speed" in notes
    assert "gusts cannot be checked" in notes and "air density is not measured" in notes
    assert ("edited after it was exported" in notes) == (variant == "edited")
    assert info["station"] == f"{variant} (45,0m - Test)" and info["format"] == windpro.FORMAT


def test_disabled_records_settings_and_columns_not_known(tmp_path):
    df = truth()
    f = tmp_path / "a.txt"
    write_export(f, df, "exported", disabled=(3, 4, 5), extra=True)
    info, out = windpro.read_windpro(f, {"d1": {"height_m": 43, "direction_offset_deg": -15}})
    assert len(out) == len(df) - 3 and info["disabled_records"] == 3 and df.index[4] not in out.index
    assert "D1_43m_Avg" in out.columns and "V1_45m_Avg" in out.columns
    keep = np.ones(len(df), bool)
    keep[[3, 4, 5]] = False
    assert np.allclose(out["D1_43m_Avg"].to_numpy(), ((df["wd"] - 15.0) % 360.0).to_numpy()[keep])
    notes = " ".join(info["notes"])
    assert "3 records marked disabled" in notes and "left out: Battery" in notes and "-15 deg" in notes


def test_not_a_windpro_export(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("Time stamp;Mean wind speed\n01.05.2017 00:00;5,0\n")
    assert not windpro.looks_like_windpro(f)
    with pytest.raises(ValueError, match="not a WindPRO time series export"):
        windpro.read_windpro(f)
    g = tmp_path / "y.txt"
    g.write_text("WindPRO time series export version 1\n45,0m\n45,0\n;Time stamp;DirUID\nDisabled;Time stamp;Wind direction\n"
                 ";dd.MM.yyyy hh:mm;Degrees\n;01.05.2017 00:00;120,0\n")
    with pytest.raises(ValueError, match="no mean wind speed column"):
        windpro.read_windpro(g)


def test_runs_through_the_checks_as_a_mast_with_one_height(tmp_path):
    """One cup and one vane at one height, no thermometer: the run finishes, says what it could not do, and hands on a
    wind climate with no shear. The position has to come from the project."""
    data = tmp_path / "mast"
    data.mkdir()
    df = truth("2017-05-01", n=144 * 75, seed=9)
    write_export(data / "series 45m.txt", df, "edited", missing=tuple(range(500, 520)), gap=(3000, 3300))
    det = intake.detect(data)
    assert det["format"] == "windpro" and det["counts"]["windpro"] == 1 and len(det["read_files"]) == 1
    res = intake.run_checks(data, tmp_path / "out", site_name="Test 45", log=lambda m: None)
    assert res["format"] == "WindPRO time series export" and res["top_height_m"] == 45.0
    assert res["records"] == 144 * 75 - 301 - 20 and res["lat"] is None and res["shear_alpha"] is None
    assert res["mean_ws"] == pytest.approx(float(df["ws"].drop(df.index[500:520]).drop(df.index[3000:3301]).mean()), rel=1e-3)
    assert res["density"] is None and not res["no_direction"]
    found = " | ".join(res["findings"])
    assert "No data from" in found and "(2.1 days)" in found                                  # the gap of 301 records
    assert "20 values at WindPRO's missing-data code" not in found and "60 values at WindPRO's missing-data code" in found
    # the same file given by name, straight to the run
    run = toa5_mast.main([str(data / "series 45m.txt"), "--out", str(tmp_path / "out2"), "--name", "Test 45"], log=lambda m: None)
    assert (run / "climate.json").is_file() and (run / "summary.xlsx").is_file()
