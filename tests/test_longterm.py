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
    bad = ref.copy()
    bad[pd.Period("2025-06", "M")] *= 0.6                        # the reference's last month breaks
    res = lt.monthly_index(mast, bad, lt_years=(2005, 2024))
    assert [d["month"] for d in res["reference_months_left_out"]] == ["2025-06"]
    assert res["last_month"] == "2025-05"
    clean = lt.monthly_index(mast, ref, lt_years=(2005, 2024))
    assert not clean["reference_months_left_out"]
    assert res["factor"] == pytest.approx(clean["factor"], rel=0.02)


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
