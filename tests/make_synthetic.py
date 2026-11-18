"""Build a synthetic SymphoniePRO export set with known faults, for the test suite.

Wind data come from brightwind's demo mast (MIT licence), rewritten in the
SymphoniePRO text-export layout, two files per day like a real logger. Each
planted fault is listed in PLANTED so the tests can check it is found.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

PLANTED = {
    "gap": ("2016-05-10 00:00", "2016-05-12 23:50"),           # 3 days of files missing
    "stuck_cup": ("Ch4", "2016-07-01 00:00", "2016-07-02 23:50"),  # 60 m S cup not turning
    "stuck_vane": ("Ch14", "2016-08-15 00:00", "2016-08-15 23:50"),
    "spike": ("Ch1", "2016-09-10 12:00"),
    "range": ("Ch16", "2016-10-01 00:00"),
    "config_change": ("Ch2", "2016-11-01 00:00"),                # cup swapped: new serial and slope
    "drift": ("Ch5", "2016-12-01 00:00", 0.97),                  # 40 m N reads 3 % low from here on
    "duplicate": "2016-06-05 16:00",                             # one record repeated across files
    # file restarted mid-interval: it repeats the previous timestamp with part-interval values
    "conflict": "2016-08-20 16:00",
    # site visit: until this time the 40 m N cup was wired to Ch7, logged as 40 m NNE with a generic
    # calibration, and vane Ch13 was logged with boom bearing 200 (SSW) instead of 180 (S)
    "rewire": ("2016-03-01 16:00", "Ch5", 7, 22.5, "NNE", 0.0470, 0.3000, "Ch13", 200.0, "SSW"),
    "no_signal": ("Ch17", "2016-04-01 00:00", "2016-04-03 23:50"),  # RH sensor unpowered: 0 V
    "pressure": ("Ch18", "2016-10-15 00:00", "2016-10-15 03:00", 850.0),  # inside 500-1100, far from site
}

CHANNELS = [  # ch, type, column stem, height, bearing deg, compass, units, description, serial, slope, offset
    (1, "Anem", "Spd80mN", 80.0, 0.0, "N", "m/s", "NRG S1", "94120001", 0.0460, 0.2430),
    (2, "Anem", "Spd80mS", 80.0, 180.0, "S", "m/s", "NRG S1", "94120002", 0.0460, 0.2430),
    (3, "Anem", "Spd60mN", 60.0, 0.0, "N", "m/s", "NRG S1", "94120003", 0.0461, 0.2374),
    (4, "Anem", "Spd60mS", 60.0, 180.0, "S", "m/s", "NRG S1", "94120004", 0.0457, 0.2557),
    (5, "Anem", "Spd40mN", 40.0, 0.0, "N", "m/s", "NRG S1", "94120005", 0.0459, 0.2554),
    (6, "Anem", "Spd40mS", 40.0, 180.0, "S", "m/s", "NRG S1", "94120006", 0.0459, 0.2554),
    (13, "Vane", "Dir78mS", 78.0, 180.0, "S", "Deg", "NRG 200M Vane", "10700001", 0.351, 0.0),
    (14, "Vane", "Dir58mS", 58.0, 180.0, "S", "Deg", "NRG 200M Vane", "10700002", 0.351, 0.0),
    (16, "Analog", "T2m", 2.0, 0.0, "N", "C", "NRG T60 Temp", "9400001", 44.7436, -40.8555),
    (17, "Analog", "RH2m", 2.0, 0.0, "N", "%RH", "NRG RH5X Humi", "", 20.0, 0.0),
    (18, "Analog", "P2m", 2.0, 0.0, "N", "hPa", "NRG BP60 Baro", "", 243.871, 494.427),
]


def load_demo(path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["Timestamp"], index_col="Timestamp")
    return df.loc["2016-02-01":"2017-02-28 23:50"]


def build_frame(demo: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=demo.index)
    for ch, typ, stem, h, _b, comp, units, *_ in CHANNELS:
        base = f"Ch{ch}_{typ}_{h:.2f}m_{comp}"
        if typ == "Anem":
            avg, sd = demo[stem], demo[f"{stem}Std"]
            out[f"{base}_Avg_{units}"] = avg
            out[f"{base}_SD_{units}"] = sd
            out[f"{base}_Min_{units}"] = np.minimum((avg - 2.0 * sd).clip(lower=0.0), avg)
            out[f"{base}_Max_{units}"] = demo[f"{stem}Max"].combine(avg, max)
            out[f"{base}_Gust_{units}"] = out[f"{base}_Max_{units}"]
        elif typ == "Vane":
            out[f"{base}_Avg_{units}"] = demo[stem]
            out[f"{base}_SD_{units}"] = demo[f"{stem}Std"]
            out[f"{base}_GustDir_{units}"] = demo[stem]
        else:
            v = demo[stem]
            out[f"{base}_Avg_{units}"] = v
            out[f"{base}_SD_{units}"] = 0.0
            out[f"{base}_Min_{units}"] = v
            out[f"{base}_Max_{units}"] = v
    return out


def plant(out: pd.DataFrame) -> pd.DataFrame:
    def cols(ch):
        return [c for c in out.columns if c.startswith(f"{ch}_")]
    ch, a, b = PLANTED["stuck_cup"]
    for c in cols(ch):
        out.loc[a:b, c] = 0.0 if "_SD_" in c else 0.2557
    ch, a, b = PLANTED["stuck_vane"]
    for c in cols(ch):
        out.loc[a:b, c] = 0.0 if "_SD_" in c else 123.0
    ch, t = PLANTED["spike"]
    for c in cols(ch):
        if "_Avg_" in c or "_Max_" in c or "_Gust_" in c:
            out.loc[t, c] = out.loc[t, c] + 25.0
    ch, t = PLANTED["range"]
    for c in cols(ch):
        if "_SD_" not in c:
            out.loc[t, c] = -60.0
    ch, t, f = PLANTED["drift"]
    for c in cols(ch):
        out.loc[t:, c] = out.loc[t:, c] * f
    ch, a, b = PLANTED["no_signal"]
    for c in cols(ch):
        out.loc[a:b, c] = 0.0
    ch, a, b, v = PLANTED["pressure"]
    for c in cols(ch):
        out.loc[a:b, c] = 0.0 if "_SD_" in c else v
    a, b = PLANTED["gap"]
    return out.drop(out.loc[a:b].index)


def rewire(part: pd.DataFrame, start: pd.Timestamp) -> pd.DataFrame:
    """Before the site visit: write the columns the way the old logger configuration did."""
    t, ch, new_ch, _bdeg, comp, slope0, off0, vch, _vdeg, vcomp = PLANTED["rewire"]
    if start >= pd.Timestamp(t):
        return part
    part = part.copy()
    _c, _t, _s, h, _b, ccomp, _u, _d, _ser, slope1, off1 = next(x for x in CHANNELS if x[0] == int(ch[2:]))
    ren = {}
    for c in [c for c in part.columns if c.startswith(f"{ch}_")]:
        stat = c.split("_")[4]
        if stat == "SD":
            part[c] = part[c] * slope0 / slope1
        else:
            part[c] = (part[c] - off1) / slope1 * slope0 + off0
        ren[c] = c.replace(f"{ch}_", f"Ch{new_ch}_").replace(f"m_{ccomp}_", f"m_{comp}_")
    for c in [c for c in part.columns if c.startswith(f"{vch}_")]:
        if "_SD_" not in c:
            part[c] = (part[c] + 20.0) % 360.0
        ren[c] = c.replace("m_S_", f"m_{vcomp}_")
    return part.rename(columns=ren)


def header(day: pd.Timestamp, change_after: pd.Timestamp) -> str:
    lines = ["SymphoniePRO Desktop Application", "Software Version:\t3.10.1.12", "Export version:\t3",
             f"Generated:\t{day:%Y-%m-%d} 23:59:59", "", "Export Parameters", "Site Number:\t000001",
             "Data Type:\tMeasurements", "Start Date:\t", "End Date:\t", "Filters Applied:\tFalse", "",
             "Site Properties", "Site Description:\tSynthetic Test Mast", "Project:\tBohemian QC tests",
             "Tower:\tT1", "Location:\tTest site", "Coordinate System:\tWGS84", "Latitude:\t1.2000000",
             "Longitude:\t35.4000000", "Elevation:\t500", "Time Zone:\tUTC+3:00", "",
             "Logger History", "Date:\t2016-01-09 00:00:00", "Model:\tSymphoniePRO Logger (8206)",
             "Serial Number:\t820600001", "Firmware:\t3.4.1", "", "iPack History",
             "Date:\t2016-01-09 00:00:00", "Model:\tiPackACCESS (7984)", "Serial Number:\t716900001",
             "Firmware:\t2.2.0", "", "Sensor History"]
    visit, rch, new_ch, rb, _rc, rslope, roff, rvch, rvb, _rvc = PLANTED["rewire"]
    visit = pd.Timestamp(visit)
    for ch, typ, _stem, h, b, _c, units, desc, serial, slope, off in CHANNELS:
        eff = "2016-01-09 00:00:00"
        if ch == int(PLANTED["config_change"][0][2:]) and day >= change_after:
            eff, serial, slope = f"{change_after:%Y-%m-%d %H:%M:%S}", serial + "R", slope + 0.0005
        if ch == int(rch[2:]):
            if day < visit:
                ch, b, slope, off = new_ch, rb, rslope, roff
            else:
                eff = f"{visit:%Y-%m-%d %H:%M:%S}"
        if ch == int(rvch[2:]):
            if day < visit:
                b = rvb
            else:
                eff = f"{visit:%Y-%m-%d %H:%M:%S}"
        t = {"Anem": "Anemometer", "Vane": "Vane", "Analog": "Analog"}[typ]
        lines += [f"Channel:\t{ch}", f"Effective Date:\t{eff}", f"Type:\t{t}", "Data Logging Mode:\tStatistics",
                  f"Description:\t{desc}", f"Serial Number:\t{serial}", f"Height:\t{h:.2f}", f"Bearing:\t{b:.2f}"]
        if typ == "Vane":
            lines.append("Vane Mounting Angle:\t0.00")
        lines += [f"Scale Factor:\t{slope:.5f}", f"Offset:\t{off:.5f}", f"Units:\t{units}"]
        if typ == "Vane":
            lines.append(f"Total Direction Offset:\t{off + b:.2f}")
        lines.append("")
    lines.append("Data")
    return "\n".join(lines)


def write_files(out: pd.DataFrame, folder: Path) -> int:
    folder.mkdir(parents=True, exist_ok=True)
    change_after = pd.Timestamp(PLANTED["config_change"][1])
    dup_at = pd.Timestamp(PLANTED["duplicate"])
    conflict_at = pd.Timestamp(PLANTED["conflict"])
    n, seq = 0, 1
    for day, dayframe in out.groupby(out.index.normalize()):
        for start, stop in (("00:00", "15:50"), ("16:00", "23:50")):
            a = pd.Timestamp(f"{day:%Y-%m-%d} {start}")
            part = dayframe.loc[a:pd.Timestamp(f"{day:%Y-%m-%d} {stop}")]
            if a == dup_at:  # the logger repeats the previous record at the top of this file
                part = pd.concat([dayframe.loc[[a - pd.Timedelta(minutes=10)]], part])
            if a == conflict_at:  # ... or writes a part-interval record for it
                prev = dayframe.loc[[a - pd.Timedelta(minutes=10)]].copy()
                num = prev.select_dtypes("number").columns
                prev[num] = prev[num] * 0.9
                part = pd.concat([prev, part])
            if part.empty:
                continue
            sub = folder / f"{day:%Y%m}"
            sub.mkdir(exist_ok=True)
            name = f"000001_{a:%Y-%m-%d_%H.%M}_{seq:06d}_meas.txt"
            body = rewire(part, a)
            body.insert(0, "Timestamp", body.index.strftime("%Y-%m-%d %H:%M:%S"))
            text = header(a, change_after) + "\n" + body.to_csv(sep="\t", index=False, float_format="%.6f",
                                                                 lineterminator="\n")
            (sub / name).write_text(text, encoding="utf-8")
            n += 1
            seq += 1
    return n


def make(demo_csv, folder) -> int:
    return write_files(plant(build_frame(load_demo(demo_csv))), Path(folder))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("demo_csv")
    ap.add_argument("folder")
    a = ap.parse_args()
    print(make(a.demo_csv, a.folder), "files written")
