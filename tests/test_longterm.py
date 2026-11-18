"""Long-term index: known answers on series written here. Run: python -m pytest tests/test_longterm.py -q"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bohemian_wind import longterm as lt  # noqa: E402


def reference(start=2001, end=2025, seed=1):
    """Seasonal reference with year-to-year variation; 2024 is 10 % windier than the long-term mean."""
    rng = np.random.default_rng(seed)
    idx = pd.period_range(f"{start}-01", f"{end}-12", freq="M")
    season = 5.0 + 1.2 * np.sin(2 * np.pi * (idx.month.to_numpy() - 3) / 12)
    year = {y: 1.0 for y in range(start, end + 1)}
    year[2024] = 1.10
    return pd.Series(season * np.array([year[p.year] for p in idx]) * (1 + 0.01 * rng.standard_normal(len(idx))), index=idx)


def test_full_year_recovers_the_known_factor():
    ref = reference()
    mast = ref["2024-01":"2024-12"] * 1.30                      # the mast reads 1.3 x the reference
    res = lt.monthly_index(mast, ref, lt_years=(2005, 2023))
    assert res["calendar_months"] == 12 and not res["indicative"]
    assert res["factor"] == pytest.approx(1 / 1.10, rel=0.01)   # a windy year is scaled down
    assert res["long_term"] == pytest.approx(1.30 * res["reference_long_term"], rel=0.002)
    assert res["r"] > 0.99 and res["months"] == 12


def test_part_year_is_marked_indicative():
    ref = reference()
    mast = ref["2023-11":"2024-03"] * 1.2
    res = lt.monthly_index(mast, ref, lt_years=(2005, 2023))
    assert res["indicative"] and res["calendar_months"] == 5 and "assumes" in res["method"]
    assert res["long_term"] == pytest.approx(res["measured"] * res["reference_long_term"] / res["reference_concurrent"])


def test_broken_last_reference_month_is_left_out():
    ref = reference()
    mast = ref["2024-01":"2025-06"] * 1.3
    bad = ref[:"2025-06"].copy()
    bad[pd.Period("2025-06", "M")] *= 0.6                        # the reference's last month breaks
    res = lt.monthly_index(mast, bad, lt_years=(2005, 2024))
    assert [d["month"] for d in res["reference_months_left_out"]] == ["2025-06"]
    assert res["last_month"] == "2025-05"
    clean = lt.monthly_index(mast, ref, lt_years=(2005, 2024))
    assert not clean["reference_months_left_out"]
    assert res["factor"] == pytest.approx(clean["factor"], rel=0.02)
    # the same break in the middle of the reference is not its tail: an older campaign is never trimmed
    middle = ref.copy()
    middle[pd.Period("2025-06", "M")] *= 0.6
    assert not lt.monthly_index(mast, middle, lt_years=(2005, 2024))["reference_months_left_out"]


def test_a_seasonal_mast_to_reference_ratio_does_not_trim_an_old_campaign():
    ref = reference()
    months = pd.period_range("2015-03", "2016-02", freq="M")
    ratio = np.array([1.3 + 0.4 * np.cos(2 * np.pi * (p.month - 7) / 12) for p in months])     # 0.9 to 1.7 over the year
    mast = pd.Series(ref.reindex(months).to_numpy() * ratio, index=months)
    res = lt.monthly_index(mast, ref, lt_years=(2005, 2024))
    assert res["months"] == 12 and res["calendar_months"] == 12 and not res["reference_months_left_out"]


def test_too_little_data_and_default_years():
    ref = reference()
    with pytest.raises(ValueError):
        lt.monthly_index(ref["2024-01":"2024-02"], ref)
    assert lt.default_years(ref) == (2006, 2025)
    assert lt.default_years(ref[:"2025-06"]) == (2005, 2024)


def test_reads_power_files(tmp_path):
    monthly = {f"{y}{m:02d}": 5.0 + 0.1 * m for y in (2023, 2024) for m in range(1, 13)}
    monthly["202313"] = 5.65
    monthly["202412"] = -999.0
    (tmp_path / "m.json").write_text(json.dumps({"properties": {"parameter": {"WS50M": monthly}}}))
    days = {f"2025{m:02d}{d:02d}": 6.0 for m in (1, 2) for d in range(1, 29)}   # January incomplete, February complete
    (tmp_path / "d.json").write_text(json.dumps({"properties": {"parameter": {"WS50M": days}}}))
    s = lt.read_power(tmp_path / "m.json", tmp_path / "d.json")
    assert pd.Period("2024-12", "M") not in s.index and pd.Period("2023-06", "M") in s.index and len(s) == 24
    assert s[pd.Period("2025-02", "M")] == 6.0 and pd.Period("2025-01", "M") not in s.index
    assert "WS50M" in lt.power_url(-1.5, 36.9, "monthly", "2001", "2025") and "latitude=-1.5000" in lt.power_url(-1.5, 36.9, "monthly", "2001", "2025")


def test_a_thin_month_stands_in_for_a_calendar_month_that_is_otherwise_missing():
    """One calendar month has 55 % valid data and no other year covers it: using it gives twelve calendar months and the
    mean-of-monthly-means method; leaving it out would fall back on a single ratio, which is biased when the
    mast-to-reference ratio changes with the season."""
    months = pd.period_range("2021-05", "2022-04", freq="M")
    season = {m: 4.0 + 2.0 * np.cos(2 * np.pi * (m - 1) / 12) for m in range(1, 13)}             # reference
    ratio = {m: 1.3 + 0.4 * np.cos(2 * np.pi * (m - 7) / 12) for m in range(1, 13)}              # mast / reference
    ref = pd.Series({p: season[p.month] for p in pd.period_range("2001-01", "2025-12", freq="M")}, dtype=float)
    monthly = {str(p): {"mean": season[p.month] * ratio[p.month], "share": 0.55 if p.month == 9 else 1.0} for p in months}
    monthly["2021-04"] = {"mean": 3.0, "share": 0.30}                                              # too thin: never used
    clim = {"height_m": 80.0, "monthly": monthly}
    truth = float(np.mean([season[m] * ratio[m] for m in range(1, 13)]))
    res = lt.correct(clim, ref)
    assert res["calendar_months"] == 12 and not res["indicative"] and res["months"] == 12
    assert res["partial_months_used"] == [{"month": "2021-09", "valid_share": 0.55}]
    assert res["long_term"] == pytest.approx(truth, rel=1e-9) and "September 2021" in res["cautions"][0]
    series, used = lt.measured_months(clim, ref)
    assert "2021-04" not in [str(p) for p in series.index]
    # without the thin month: eleven calendar months, flagged, and biased by the seasonal ratio
    monthly["2021-09"]["share"] = 0.20
    res2 = lt.correct(clim, ref)
    assert res2["calendar_months"] == 11 and res2["indicative"] and res2["partial_months_used"] == []
    assert "Only 11 of the 12 calendar months" in res2["cautions"][0]
    assert abs(res2["long_term"] / truth - 1) > abs(res["long_term"] / truth - 1)
    # a thin month is not used when a good month already covers that calendar month
    monthly["2021-09"]["share"] = 1.0
    monthly["2022-09"] = {"mean": 9.9, "share": 0.5}
    assert lt.correct(clim, ref)["partial_months_used"] == []
    # a campaign that stays short of a full year gains nothing from a thin month at full weight (seen on a real
    # part-year record, where the thin month moved the result away from an independent daily-ratio calculation)
    short = {"height_m": 80.0, "monthly": {k: v for k, v in monthly.items() if k in ("2021-11", "2021-12", "2022-01", "2022-02")}}
    short["monthly"]["2021-10"] = {"mean": 5.0, "share": 0.45}
    res3 = lt.correct(short, ref)
    assert res3["partial_months_used"] == [] and res3["calendar_months"] == 4 and res3["indicative"]
    assert len(res3["cautions"]) == 1 and "Only 4 of the 12 calendar months" in res3["cautions"][0]
