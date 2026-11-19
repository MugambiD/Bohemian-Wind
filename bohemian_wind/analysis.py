"""Wind statistics on QC-valid data: paired cups and mast shadow, shear, turbulence,
direction, Weibull, air density and seasonal profiles."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .qc import Sensor, site_pressure
from .sympro import Dataset

IEC_IREF = {"A+": 0.18, "A": 0.16, "B": 0.14, "C": 0.12}
R_DRY, R_VAP = 287.05, 461.5


def sector(direction: pd.Series, n: int) -> pd.Series:
    width = 360.0 / n
    return (((direction % 360.0) + width / 2) // width % n).astype("Int64")


def weibull_fit(x) -> tuple[float, float]:
    """Maximum-likelihood Weibull scale A and shape k."""
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x) & (x > 0)]
    if len(x) < 50:
        return np.nan, np.nan
    ln = np.log(x)
    k = 2.0
    for _ in range(200):
        xk = x ** k
        s0, s1, s2 = xk.sum(), (xk * ln).sum(), (xk * ln * ln).sum()
        f = s1 / s0 - 1.0 / k - ln.mean()
        df = (s2 * s0 - s1 ** 2) / s0 ** 2 + 1.0 / k ** 2
        k_new = max(k - f / df, 0.05)
        if abs(k_new - k) < 1e-10:
            k = k_new
            break
        k = k_new
    return float(np.mean(x ** k) ** (1.0 / k)), float(k)


def _vane_for(height, vanes, valid):
    if not vanes:
        return None
    return min(vanes, key=lambda v: (abs((v.height or 0) - (height or 0)), -int(valid[v.label].sum())))


def direction_series(data, valid, height, vanes) -> pd.Series | None:
    """Direction from the vane nearest in height; where it is invalid, the next nearest fills in."""
    if not vanes:
        return None
    order = sorted(vanes, key=lambda v: (abs((v.height or 0) - (height or 0)), -int(valid[v.label].sum())))
    d = data[order[0].avg_col].where(valid[order[0].label])
    for v in order[1:]:
        d = d.fillna(data[v.avg_col].where(valid[v.label]))
    return d


def _circ_dist(a, b):
    return np.abs((np.asarray(a, dtype=float) - b + 180.0) % 360.0 - 180.0)


def wake_check(tab: pd.DataFrame, ref: float, cup: Sensor, side: str, min_n=30, min_deficit=0.03) -> dict:
    """Where does this cup sit in the mast wake, against where its boom bearing puts it?

    The ratio A/B dips where cup A is downwind of the mast and peaks where B is. A cup on
    a boom pointing at bearing b is in the wake when the wind blows from b + 180°.
    """
    ok = tab["count"].fillna(0) >= min_n
    r = tab["median_ratio"]
    deficit = ((1.0 - r / ref) if side == "a" else (1.0 - ref / r)).where(ok)
    boom = cup.boom_deg
    expected = (boom + 180.0) % 360.0 if boom is not None else None
    row = {"cup": cup.label, "boom": cup.bearing, "boom_bearing_deg": boom, "expected_wake_deg": expected,
           "observed_wake_deg": np.nan, "wake_deficit_%": np.nan, "difference_deg": np.nan}
    secs = tab.index.to_numpy(dtype=float)
    n_expected = int(tab["count"].fillna(0)[_circ_dist(secs, expected) <= 15].sum()) if expected is not None else 0
    if deficit.notna().sum() == 0 or deficit.max() < min_deficit:
        if n_expected >= min_n:
            row["verdict"] = "no wake found: check mounting"
            row["note"] = (f"no mast wake found in any direction, although {n_expected:,} records came from where the "
                           "logged boom puts its wake. Check how this cup is mounted (top-mounted, or on a longer boom?)")
        else:
            row["verdict"] = "too few records to check"
            row["note"] = "too few records from the expected wake direction to check the boom"
        return row
    peak = float(deficit.idxmax())
    near = (_circ_dist(secs, peak) <= 20) & (deficit.fillna(0).to_numpy() > 0)
    w = deficit.fillna(0).to_numpy()[near]
    ang = np.radians(secs[near])
    centre = float(np.degrees(np.arctan2((w * np.sin(ang)).sum(), (w * np.cos(ang)).sum())) % 360.0)
    row.update({"observed_wake_deg": centre, "wake_deficit_%": float(deficit.max() * 100.0)})
    if expected is None:
        row["verdict"] = row["note"] = "no boom bearing in the logger configuration"
    else:
        diff = (centre - expected + 180.0) % 360.0 - 180.0
        row["difference_deg"] = diff
        if abs(diff) <= 20:
            row["verdict"] = row["note"] = "consistent with the logged boom bearing"
        else:
            row["verdict"] = "check vane north and boom bearing"
            row["note"] = (f"wake seen at {centre:.0f}°, but the logged boom bearing ({boom:.0f}°) puts it at "
                           f"{expected:.0f}°. Check the vane's north reference and the boom bearing")
    return row


def pair_analysis(data, valid, a: Sensor, b: Sensor, wd: pd.Series | None, vane_label: str = "", n_sectors=36, min_ws=4.0):
    xa, xb = data[a.avg_col], data[b.avg_col]
    m = valid[a.label] & valid[b.label] & (xa >= min_ws) & (xb >= min_ws)
    ratio = (xa / xb)[m]
    res = {"a": a.label, "b": b.label, "n": int(m.sum()),
           "median_ratio": float(ratio.median()) if len(ratio) else np.nan}
    if wd is not None and len(ratio):
        sec = sector(wd[m], n_sectors)
        grp = ratio.groupby(sec)
        tab = pd.DataFrame({"median_ratio": grp.median(), "count": grp.size()})
        tab = tab.reindex(range(n_sectors))
        tab.index = [i * 360.0 / n_sectors for i in range(n_sectors)]
        tab.index.name = "sector_centre_deg"
        res["sectors"] = tab
        res["vane"] = vane_label
        ok = tab["count"] >= 20
        # reference ratio: median over direction sectors, so the prevailing direction
        # (which may sit in one cup's wake) does not set it
        ref = float(tab.loc[ok, "median_ratio"].median()) if ok.any() else res["median_ratio"]
        res["ref_ratio"] = ref
        if ok.any():
            lo, hi = tab.loc[ok, "median_ratio"].idxmin(), tab.loc[ok, "median_ratio"].idxmax()
            res["shadow_a_deg"], res["shadow_a_ratio"] = lo, float(tab.loc[lo, "median_ratio"])
            res["shadow_b_deg"], res["shadow_b_ratio"] = hi, float(tab.loc[hi, "median_ratio"])
            res["wake"] = [wake_check(tab, ref, a, "a"), wake_check(tab, ref, b, "b")]
        free = ok & ((tab["median_ratio"] - ref).abs() < 0.02)
        free_secs = [int(round(c / (360.0 / n_sectors))) % n_sectors for c in tab.index[free]]
        mf = sec.isin(free_secs).fillna(False).astype(bool)
        monthly = ratio[mf].groupby(ratio[mf].index.to_period("M")).agg(["median", "size"])
        monthly = monthly[monthly["size"] >= 100]
        res["monthly"] = monthly
        if len(monthly):
            # the yardstick is the middle month, each usable month counting once. The median of all records leans
            # towards the fault itself: on the test mast, a cup reading 3 % low over the last quarter of the campaign
            # pulled it by 0.4 %, and the first faulty month then showed as 1.8 % and slipped under the threshold
            ref = float(monthly["median"].median())
            res["monthly_ref"] = ref
            dev = (monthly["median"] / ref - 1.0) * 100.0
            res["drift_months"] = [(str(p), float(v)) for p, v in dev.items() if abs(v) > 2.0]
        # per sector, prefer the cup that reads higher: it is the one outside the mast wake
        pref_a = (tab["median_ratio"] >= 1.0) | ~ok
        res["prefer_a_sectors"] = [i for i, flag in enumerate(pref_a.to_numpy()) if flag]
    return res


def combine_pair(data, valid, a: Sensor, b: Sensor, wd: pd.Series | None, pair: dict, n_sectors=36):
    va, vb = valid[a.label], valid[b.label]
    xa, xb = data[a.avg_col].where(va), data[b.avg_col].where(vb)
    sa = data[a.cols["SD"]].where(va) if "SD" in a.cols else pd.Series(np.nan, index=data.index)
    sb = data[b.cols["SD"]].where(vb) if "SD" in b.cols else pd.Series(np.nan, index=data.index)
    if wd is not None and "prefer_a_sectors" in pair:
        sec = sector(wd, n_sectors)
        use_a = sec.isin(pair["prefer_a_sectors"]).fillna(False).astype(bool)
        use_a[sec.isna().to_numpy()] = True  # direction unknown: keep the cup with better recovery
    else:
        use_a = pd.Series(True, index=data.index)
    pick_a = (use_a & va) | (~vb & va)
    ws = xb.where(~pick_a, xa)
    sd = sb.where(~pick_a, sa)
    return ws, sd


def height_series(ds: Dataset, qc: dict, sensors: list[Sensor]) -> tuple[dict, list]:
    data, valid = ds.data, qc["valid"]
    anems = [s for s in sensors if s.kind == "wind_speed" and s.label in valid and s.height]
    vanes = [s for s in sensors if s.kind == "wind_direction" and s.label in valid]
    by_h: dict[float, list[Sensor]] = {}
    for s in anems:
        by_h.setdefault(round(s.height, 1), []).append(s)
    out, pairs = {}, []
    for h, group in sorted(by_h.items()):
        group = sorted(group, key=lambda s: -int(valid[s.label].sum()))
        vane = _vane_for(h, vanes, valid)
        wd = direction_series(data, valid, h, vanes)
        if len(group) >= 2:
            a, b = group[0], group[1]
            pr = pair_analysis(data, valid, a, b, wd, vane_label=f"{vane.label} (gaps filled from other vanes)" if vane else "")
            pr["height"] = h
            pairs.append(pr)
            ws, sd = combine_pair(data, valid, a, b, wd, pr)
            src = f"{a.label} + {b.label} (upwind cup by sector)"
        else:
            a = group[0]
            ws = data[a.avg_col].where(valid[a.label])
            sd = data[a.cols["SD"]].where(valid[a.label]) if "SD" in a.cols else pd.Series(np.nan, index=data.index)
            src = a.label
        out[h] = {"ws": ws, "sd": sd, "source": src, "vane": vane.label if vane else None, "wd": wd}
    return out, pairs


def momm(ws: pd.Series) -> tuple[float, int]:
    monthly = ws.groupby([ws.index.year, ws.index.month]).mean()
    by_cal = monthly.groupby(level=1).mean()
    return (float(by_cal.mean()) if len(by_cal) == 12 else np.nan), int(len(by_cal))


def shear(hs: dict, min_ws=3.0, wd: pd.Series | None = None, min_n=100) -> dict:
    """The profile over the records in which every height is valid and at least min_ws. Heights that were never
    measured together (a cup moved to a new height, a sensor fitted after another came down) have no such
    records: the height whose absence leaves the others the most records in common is then left out, one at a
    time, and named in "left_out". Nothing measured together: no shear ({})."""
    heights = sorted(hs)
    if len(heights) < 2:
        return {}
    frame = pd.DataFrame({h: hs[h]["ws"] for h in heights})
    ok = frame.notna() & (frame >= min_ws)
    common = lambda hh: int(ok[hh].all(axis=1).sum())  # noqa: E731
    left_out = []
    while len(heights) > 2 and common(heights) < min_n:
        drop = max(heights, key=lambda h: (common([x for x in heights if x != h]), -h))
        heights.remove(drop)
        left_out.append(drop)
    m = ok[heights].all(axis=1)
    if not m.any():
        return {}
    frame = frame[heights]
    means = frame[m].mean()
    alpha_fit = float(np.polyfit(np.log(heights), np.log(means.to_numpy()), 1)[0])
    lo, hi = heights[0], heights[-1]
    pair_alpha = lambda u: np.log(u[hi].mean() / u[lo].mean()) / np.log(hi / lo)  # noqa: E731
    sub = frame[m]
    by_hour = sub.groupby(sub.index.hour).apply(pair_alpha)
    res = {"heights": heights, "means": means, "alpha_fit": alpha_fit,
           "alpha_top_bottom": float(pair_alpha(sub)), "n": int(m.sum()), "by_hour": by_hour,
           "lo": lo, "hi": hi, "left_out": sorted(left_out)}
    if len(heights) > 2:  # the two highest heights: the closest analogue to carrying the wind above the mast
        h2 = heights[-2]
        res["top_pair"] = (h2, hi)
        res["alpha_top_pair"] = float(np.log(means[hi] / means[h2]) / np.log(hi / h2))
    if wd is not None:
        sec = sector(wd[m], 12)
        res["by_sector"] = sub.groupby(sec).apply(pair_alpha).reindex(range(12))
        res["by_sector"].index = [i * 30 for i in range(12)]
    return res


def shear_test(hs: dict, min_ws=3.0, train_share=0.8, min_n=100) -> pd.DataFrame:
    """Out-of-sample test of the power law, the model used to carry the top-height wind upward.

    For each lower height: records where it and the top height are both valid and at least
    min_ws, in time order. The exponent between the two heights comes from the first 80 %
    (ratio of means, as in the shear table); it then predicts the top-height speed from the
    lower one over the last 20 %, which it has not seen. The baseline carries the lower speed
    up unchanged (no shear). mean_error_% is the error in the mean over the test period, the
    error that matters for the resource.
    """
    heights = sorted(hs)
    if len(heights) < 2:
        return pd.DataFrame()
    hi, rows = heights[-1], []
    # the exponent fitted across all heights on the first 80 % of records where all are valid,
    # tested the same way: shows whether one law for the whole profile carries the top pair
    allh = pd.DataFrame({h: hs[h]["ws"] for h in heights}).dropna()
    allh = allh[(allh >= min_ws).all(axis=1)].sort_index()
    allh = allh.iloc[:int(len(allh) * train_share)]
    a_all = (float(np.polyfit(np.log(heights), np.log(allh.mean().to_numpy()), 1)[0])
             if len(heights) > 2 and len(allh) >= min_n else np.nan)
    for lo in heights[:-1]:
        pair = pd.DataFrame({"lo": hs[lo]["ws"], "hi": hs[hi]["ws"]}).dropna()
        pair = pair[(pair >= min_ws).all(axis=1)].sort_index()
        cut = int(len(pair) * train_share)
        train, test = pair.iloc[:cut], pair.iloc[cut:]
        if min(len(train), len(test)) < min_n:
            continue
        ln = np.log(hi / lo)
        a_train = float(np.log(train["hi"].mean() / train["lo"].mean()) / ln)
        a_test = float(np.log(test["hi"].mean() / test["lo"].mean()) / ln)
        pred = test["lo"] * (hi / lo) ** a_train
        err, base = pred - test["hi"], test["lo"] - test["hi"]
        obs = float(test["hi"].mean())
        rows.append({"from_m": lo, "to_m": hi, "train_records": len(train), "trained_from": train.index[0],
                     "trained_to": train.index[-1], "test_records": len(test), "tested_from": test.index[0],
                     "tested_to": test.index[-1], "alpha_train": a_train,
                     "alpha_test": a_test, "MAE_mps": float(err.abs().mean()),
                     "RMSE_mps": float(np.sqrt((err ** 2).mean())), "bias_mps": float(err.mean()),
                     "mean_error_%": 100.0 * float(pred.mean()) / obs - 100.0,
                     "baseline_MAE_mps": float(base.abs().mean()),
                     "baseline_mean_error_%": 100.0 * float(test["lo"].mean()) / obs - 100.0,
                     "alpha_all_heights": a_all,
                     "all_heights_mean_error_%": (100.0 * float(test["lo"].mean()) * (hi / lo) ** a_all / obs - 100.0
                                                  if np.isfinite(a_all) else np.nan)})
    return pd.DataFrame(rows)


def ti_bins(ws: pd.Series, sd: pd.Series) -> pd.DataFrame:
    m = ws.notna() & sd.notna() & (ws >= 2.0)
    v, ti = ws[m], (sd / ws)[m]
    b = np.floor(v + 0.5).astype(int)
    g = ti.groupby(b)
    tab = pd.DataFrame({"count": g.size(), "mean_TI": g.mean(), "sd_TI": g.std(), "p90_TI": g.quantile(0.9)})
    tab["rep_TI"] = tab["mean_TI"] + 1.28 * tab["sd_TI"]
    tab = tab[(tab.index >= 2) & (tab.index <= 25)]
    for cls, iref in IEC_IREF.items():
        tab[f"IEC_{cls}"] = iref * (0.75 + 5.6 / tab.index.to_numpy())
    tab.index.name = "ws_bin_mps"
    return tab


def rose(ws: pd.Series, wd: pd.Series, n=16) -> pd.DataFrame:
    m = ws.notna() & wd.notna()
    sec = sector(wd[m], n)
    tab = pd.DataFrame({"freq_%": sec.value_counts(normalize=True).sort_index() * 100.0,
                        "mean_ws": ws[m].groupby(sec).mean()}).reindex(range(n)).fillna(0.0)
    tab.index = [i * 360.0 / n for i in range(n)]
    tab.index.name = "sector_centre_deg"
    return tab


def _pick(sensors, kind, valid, highest=True, min_valid=144):
    cands = [s for s in sensors if s.kind == kind and s.label in valid and int(valid[s.label].sum()) >= min_valid]
    if not cands:
        return None
    return sorted(cands, key=lambda s: ((s.height or 0) * (1 if highest else -1), int(valid[s.label].sum())))[-1]


def air_density(ds: Dataset, qc: dict, sensors: list[Sensor]) -> dict:
    data, valid = ds.data, qc["valid"]
    t_s, p_s, rh_s = (_pick(sensors, k, valid) for k in ("temperature", "pressure", "humidity"))
    if t_s is None:
        return {"note": "No temperature sensor with valid data: air density not computed."}
    t = data[t_s.avg_col].where(valid[t_s.label])
    note = f"Temperature from {t_s.label}"
    p0 = site_pressure(ds.site)
    p = pd.Series(np.nan, index=data.index)
    if p_s is not None:
        p = data[p_s.avg_col].where(valid[p_s.label])
        if p_s.units.lower() == "kpa":
            p = p * 10.0
    measured = float(p.notna().mean() * 100.0)
    p_own = float(p.mean()) if p.notna().any() else None          # the barometer's own mean, before any filling
    if p0 is not None:
        p = p.fillna(p0)
    elif p.notna().sum() == 0:
        return {"note": "No valid pressure data and no site elevation: air density not computed."}
    if p_s is not None and measured >= 99.5:
        note += f"; pressure from {p_s.label}"
    elif p_s is not None:
        note += (f"; pressure from {p_s.label} where valid ({measured:.0f}% of records), otherwise "
                 + (f"the standard atmosphere at the site elevation ({p0:.0f} hPa)" if p0 is not None else
                    "none: the site elevation is not known, so the density is the mean of the records with a valid "
                    "pressure only. Enter the elevation to fill the rest from the standard atmosphere"))
    else:
        note += (f"; no pressure sensor with valid data, so pressure is the standard atmosphere at the "
                 f"site elevation ({p0:.0f} hPa)")
    estimated = measured < 50.0
    tk = t + 273.15
    if rh_s is not None:
        rh = data[rh_s.avg_col].where(valid[rh_s.label]).clip(0, 100)
        e = rh / 100.0 * 6.1078 * 10 ** (7.5 * t / (t + 237.3))
        rho = (p - e) * 100.0 / (R_DRY * tk) + e * 100.0 / (R_VAP * tk)
        rho = rho.fillna((p * 100.0) / (R_DRY * tk))
        note += f"; humidity from {rh_s.label}"
    else:
        rho = (p * 100.0) / (R_DRY * tk)
    return {"rho": rho, "mean": float(rho.mean()), "monthly": rho.groupby(rho.index.to_period("M")).mean(),
            "t_monthly": t.groupby(t.index.to_period("M")).agg(["mean", "min", "max"]),
            "estimated": estimated, "note": note, "sensor_height": t_s.height,
            "pressure_mean": p_own, "pressure_pct": measured if p_s is not None else 0.0,
            "pressure_height": p_s.height if p_s is not None else None, "temperature_mean": float(t.mean())}


def sensor_table(ds: Dataset, qc: dict, sensors: list[Sensor]) -> pd.DataFrame:
    rows, exp = [], max(qc["time"]["expected"], 1)
    for s in sensors:
        if s.label not in qc["valid"]:
            continue
        x = ds.data[s.avg_col].where(qc["valid"][s.label])
        if s.kind == "wind_direction":  # vector mean; min and max mean nothing for a direction
            r = np.radians(x.dropna())
            mean = float(np.degrees(np.arctan2(np.sin(r).mean(), np.cos(r).mean())) % 360) if len(r) else np.nan
            lo = hi = np.nan
        else:
            mean = float(x.mean()) if x.notna().any() else np.nan
            lo = float(x.min()) if x.notna().any() else np.nan
            hi = float(x.max()) if x.notna().any() else np.nan
        rows.append({"sensor": s.label, "kind": s.kind, "height_m": s.height, "boom": s.bearing,
                     "description": s.meta.get("Description", ""), "serial": ", ".join(s.serials) or s.meta.get("Serial Number", ""),
                     "channels": ", ".join(f"Ch{c}" for c in s.channels) or f"Ch{s.channel}",
                     "configurations": len(s.segments) or 1,
                     "scale_factor": s.meta.get("Scale Factor", ""), "offset": s.meta.get("Offset", ""),
                     "units": s.units, "valid_records": int(qc["valid"][s.label].sum()),
                     "recovery_%": 100.0 * qc["valid"][s.label].sum() / exp,
                     "mean": mean, "min": lo, "max": hi})
    return pd.DataFrame(rows)


def analyse(ds: Dataset, qc: dict, sensors: list[Sensor]) -> dict:
    hs, pairs = height_series(ds, qc, sensors)
    out = {"heights": hs, "pairs": pairs, "sensor_table": sensor_table(ds, qc, sensors)}
    if not hs:
        return out
    top = max(hs)
    ws_top, sd_top = hs[top]["ws"], hs[top]["sd"]
    wd = hs[top]["wd"]
    exp = max(qc["time"]["expected"], 1)
    stats = []
    for h, d in sorted(hs.items(), reverse=True):
        ws = d["ws"]
        A, k = weibull_fit(ws.dropna())
        mm, ncal = momm(ws.dropna())
        stats.append({"height_m": h, "source": d["source"], "valid_records": int(ws.notna().sum()),
                      "recovery_%": 100.0 * ws.notna().sum() / exp, "mean_ws": float(ws.mean()),
                      "momm_ws": mm, "calendar_months": ncal, "weibull_A": A, "weibull_k": k})
    out["height_stats"] = pd.DataFrame(stats)
    out["top_height"] = top
    desc = sorted(hs.items(), reverse=True)  # top height first, so legends read in the order of the lines
    out["monthly_ws"] = pd.DataFrame({h: d["ws"].groupby(d["ws"].index.to_period("M")).mean() for h, d in desc})
    out["daily_ws"] = pd.DataFrame({h: d["ws"].resample("D").mean() for h, d in desc})
    out["diurnal_ws"] = pd.DataFrame({h: d["ws"].groupby(d["ws"].index.hour).mean() for h, d in desc})
    out["shear"] = shear(hs, wd=wd)
    out["shear_test"] = shear_test(hs)
    out["ti"] = ti_bins(ws_top, sd_top)
    if wd is not None:
        out["rose"] = rose(ws_top, wd)
        out["rose_vane"] = f"{hs[top]['vane']}, gaps filled from other vanes"
    out["weibull_sample"] = ws_top.dropna()
    dens = air_density(ds, qc, sensors)
    out["density"] = dens
    if np.isfinite(dens.get("mean", np.nan)):
        out["wpd_top"] = float(0.5 * dens["mean"] * np.nanmean(ws_top.to_numpy() ** 3))
    return out
