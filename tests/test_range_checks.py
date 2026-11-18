"""v0.1.4: the range check covers a record's Max, Min, gust and standard deviation, not the average alone.

Run: python -m pytest tests/test_range_checks.py -q
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bohemian_wind.points import Sensor  # noqa: E402
from bohemian_wind.qc import run_qc  # noqa: E402
from bohemian_wind.sympro import Dataset  # noqa: E402


def mast(n=300):
    t = pd.date_range("2012-03-01", periods=n, freq="10min")
    rng = np.random.default_rng(1)
    ws = 8.0 + rng.normal(0, 1.0, n)
    d = pd.DataFrame({"ws": ws, "ws_max": ws + 3.0, "ws_min": ws - 3.0, "ws_sd": 0.9 + rng.normal(0, 0.05, n),
                      "ws2": ws * 0.95, "wd": (120 + rng.normal(0, 8, n)) % 360, "wd_sd": 9.0 + rng.normal(0, 0.5, n),
                      "t": 15 + rng.normal(0, 0.3, n).cumsum() * 0.05, "t_max": 0.0, "t_min": 0.0}, index=t)
    d["t_max"], d["t_min"] = d["t"] + 0.2, d["t"] - 0.2
    meta = {"Description": "", "Serial Number": "", "Scale Factor": "", "Offset": "", "Units": ""}
    sensors = [
        Sensor(1, "wind_speed", "Anem", 80.0, "", "m/s", {"Avg": "ws", "Max": "ws_max", "Min": "ws_min", "SD": "ws_sd"},
               dict(meta), name="WS 80"),
        Sensor(2, "wind_speed", "Anem", 60.0, "", "m/s", {"Avg": "ws2"}, dict(meta), name="WS 60"),
        Sensor(3, "wind_direction", "Vane", 78.0, "", "Deg", {"Avg": "wd", "SD": "wd_sd"}, dict(meta), name="WD 78"),
        Sensor(4, "temperature", "Temp", 2.0, "", "degC", {"Avg": "t", "Max": "t_max", "Min": "t_min"}, dict(meta),
               name="T 2"),
    ]
    ds = Dataset(files=[], data=d, columns={}, site={}, loggers=[], ipacks=[], channel_history=pd.DataFrame())
    return ds, sensors


def flagged(ds, sensors, label, flag="range"):
    q = run_qc(ds, sensors)
    return q["flags"][label][flag], q["valid"][label]


def test_clean_mast_has_no_range_flags():
    ds, sensors = mast()
    q = run_qc(ds, sensors)
    assert not any(q["flags"][s.label]["range"].any() for s in sensors)


def test_glitch_gust_and_sd_are_flagged():
    ds, sensors = mast()
    i, j, k = ds.data.index[50], ds.data.index[120], ds.data.index[200]
    ds.data.loc[i, ["ws", "ws_max", "ws_sd"]] = [11.2, 1500.0, 61.0]   # the kind of record a logger glitch leaves
    ds.data.loc[j, "ws_sd"] = 15.5                                         # SD alone
    ds.data.loc[k, "ws_max"] = 90.5                                        # gust alone
    f, valid = flagged(ds, sensors, "WS 80")
    assert list(f.index[f]) == [i, j, k] and not valid[[i, j, k]].any()
    # the neighbours and the other cup are untouched
    assert valid.sum() == len(valid) - 3
    assert not flagged(ds, sensors, "WS 60")[0].any()


def test_believable_storm_is_kept():
    ds, sensors = mast()
    i = ds.data.index[60]
    ds.data.loc[i, ["ws", "ws_max", "ws_min", "ws_sd", "ws2"]] = [42.0, 68.0, 20.0, 9.5, 40.0]
    f, _ = flagged(ds, sensors, "WS 80")
    assert not f.any()


def test_negative_minimum_and_vane_sd():
    ds, sensors = mast()
    i, j = ds.data.index[10], ds.data.index[20]
    ds.data.loc[i, "ws_min"] = -2.0
    ds.data.loc[j, "wd_sd"] = 140.0
    assert list(flagged(ds, sensors, "WS 80")[0].pipe(lambda f: f.index[f])) == [i]
    assert list(flagged(ds, sensors, "WD 78")[0].pipe(lambda f: f.index[f])) == [j]


def test_temperature_extremes_use_the_same_range():
    ds, sensors = mast()
    i = ds.data.index[30]
    ds.data.loc[i, "t_max"] = 75.0   # average still believable
    f, _ = flagged(ds, sensors, "T 2")
    assert list(f.index[f]) == [i]


def test_missing_average_is_missing_not_range():
    ds, sensors = mast()
    i = ds.data.index[40]
    ds.data.loc[i, ["ws", "ws_max"]] = [np.nan, 1500.0]
    q = run_qc(ds, sensors)
    assert q["flags"]["WS 80"]["missing"][i] and not q["flags"]["WS 80"]["range"][i]
