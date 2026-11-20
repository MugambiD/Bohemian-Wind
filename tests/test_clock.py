"""Logger clock corrections, and copies of one record in files whose headers name different programs.

A logger reprogrammed at a site visit came back with its clock an hour behind (set to UTC+1 instead of UTC+2): its
records were written an hour early, the first hour of them on top of the last hour of the old program, and the same
records reached the office in several files, one of them an export whose header named the old program. All data
here is invented.

Run: python -m pytest tests/test_clock.py -q
"""
import json
import os
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bohemian_wind import demo  # noqa: E402
from bohemian_wind.flow import cfd  # noqa: E402
from bohemian_wind.loggers import toa5_mast  # noqa: E402
from bohemian_wind.project import FEEDS, Project, ProjectError, _clock  # noqa: E402
from bohemian_wind.sympro import merge_duplicates  # noqa: E402

quiet = lambda m: None  # noqa: E731
HEAD = ('"TOA5","MAST_X","CR1000X","1234","CR1000X.Std.07.02","CPU:{prog}","1111","DATA_10MIN"\n'
        '"TIMESTAMP","RECORD","ProgName","NumSamplesInterval","V1_124m_Avg","V1_124m_Std","V1_124m_Min","V1_124m_Max"\n'
        '"TS","RN","","","m/s","m/s","m/s","m/s"\n'
        '"","","","","Avg","Std","Min","Max"\n')
OLD, NEW = "prog_v4.CR1X", "prog_v5.CR1X"


def labels(start, n):
    return [f"{t:%Y-%m-%d %H:%M:%S}" for t in pd.date_range(start, periods=n, freq="10min")]


def toa5(path: Path, header_prog: str, rows: list) -> None:
    """rows: (time as written, program on the record, wind speed)"""
    body = [f'"{t}",{i},"{p}",600,{v:.3f},0.5,{v - 1:.3f},{v + 1:.3f}' for i, (t, p, v) in enumerate(rows)]
    path.write_text(HEAD.format(prog=header_prog) + "\n".join(body) + "\n", encoding="utf-8")


@pytest.fixture
def mast(tmp_path):
    """The old program writes 10:00 to 11:30 with the right clock. The new one starts at 11:40 with its clock an hour
    behind, so its 15 records are written 10:40 to 13:00. Three files carry them, as the real archives did."""
    old = [(t, OLD, 5.0 + 0.01 * i) for i, t in enumerate(labels("2025-03-10 10:00", 10))]
    new = [(t, NEW, 8.0 + 0.01 * i) for i, t in enumerate(labels("2025-03-10 10:40", 15))]
    d = tmp_path / "data"
    (d / "may").mkdir(parents=True)
    (d / "wrong time").mkdir()
    toa5(d / "may" / "DATA_10MIN0.dat", NEW, old + new[:4])                  # the visit's day: both programs in one file
    toa5(d / "wrong time" / "DATA_10MIN0.dat", NEW, new)                     # the new program's own file
    toa5(d / "wrong time" / "combined_export.dat", OLD, new)                 # an export whose header names the old program
    return d


def test_copies_in_files_whose_headers_differ_are_one_record(mast):
    """The program a file's header names is bookkeeping: copies of a record do not conflict over it. Only the hour
    the wrong clock wrote twice conflicts."""
    L = toa5_mast.load([mast], log=quiet)
    assert len(L["conflict_times"]) == 6                                      # 10:40 to 11:30, written by both programs
    assert L["removed"] > 0 and L["clock"] == []
    d = pd.DatetimeIndex(["2025-01-01 00:10"] * 2 + ["2025-01-01 00:20"])
    frame = pd.DataFrame({"ws": [5.0, 5.0, 6.0], "_src": [0, 1, 1], "_prog": [0.0, 1.0, 1.0]}, index=d)
    out, removed, conflict_times, _, _ = merge_duplicates(frame, ["a.dat", "b.dat"])
    assert removed == 1 and len(conflict_times) == 0 and len(out) == 2
    # one export writes 865.5312, another 865.5313: the same record rounded differently, not two records
    frame = pd.DataFrame({"pa": [865.5312, 865.5313, 865.6], "ws": [7.199552, 7.199553, 7.3], "_src": [0, 1, 1]},
                         index=pd.DatetimeIndex(["2025-01-01 00:10"] * 2 + ["2025-01-01 00:20"]))
    assert len(merge_duplicates(frame, ["a.dat", "b.dat"])[2]) == 0
    frame.iloc[1, frame.columns.get_loc("ws")] = 7.21                        # a different measurement: a conflict
    assert len(merge_duplicates(frame, ["a.dat", "b.dat"])[2]) == 1


def test_a_clock_correction_puts_the_records_back_in_their_place(mast):
    rule = {"from": "2025-03-10 10:40", "to": "2025-03-10 13:00", "minutes": 60, "program": NEW, "note": "clock on UTC+1"}
    L = toa5_mast.load([mast], log=quiet, clock=[rule])
    data = L["data"].dropna(how="all")
    assert len(L["conflict_times"]) == 0 and len(data) == 25                  # 10 old and 15 new records, none lost
    assert data.index[0] == pd.Timestamp("2025-03-10 10:00") and data.index[-1] == pd.Timestamp("2025-03-10 14:00")
    assert (data.index.to_series().diff().dropna() == pd.Timedelta("10min")).all()      # one record every 10 minutes
    assert data.loc["2025-03-10 11:30", "V1_124m_Avg"] == pytest.approx(5.09)   # the old program's last record stays put
    assert data.loc["2025-03-10 11:40", "V1_124m_Avg"] == pytest.approx(8.00)   # the new program's first, an hour later
    assert L["clock"][0]["records"] == 15 and L["clock"][0]["minutes"] == 60
    assert any("15 records" in n and "+60 minutes" in n and "clock on UTC+1" in n for n in L["notes"])


def test_without_the_program_the_old_records_would_move_too(mast):
    """The program tells apart two records the wrong clock gave one time: without it the old program's last hour
    moves with the new records and lands on them."""
    L = toa5_mast.load([mast], log=quiet, clock=[{"from": "2025-03-10 10:40", "to": "2025-03-10 13:00", "minutes": 60}])
    assert len(L["conflict_times"]) == 6
    missed = toa5_mast.load([mast], log=quiet, clock=[{"from": "2024-01-01 00:00", "to": "2024-01-02 00:00", "minutes": 60}])
    assert missed["clock"][0]["records"] == 0 and any("matched no record" in n for n in missed["notes"])


def test_clock_settings_are_checked_and_written_one_way():
    rule = _clock([{"from": "2025-03-10T14:40", "to": "2025-04-02 09:10", "minutes": "60", "program": " v5.CR1X ",
                    "note": "UTC+1"}, {"from": "", "to": "", "minutes": ""}])
    assert rule == [{"from": "2025-03-10 14:40", "to": "2025-04-02 09:10", "minutes": 60, "program": "v5.CR1X", "note": "UTC+1"}]
    assert _clock(None) == [] and _clock([]) == []
    for bad in ({"from": "2025-04-02 09:10", "to": "2025-03-10 14:40", "minutes": 60},     # ends before it starts
                {"from": "2025-03-10 14:40", "to": "2025-04-02 09:10", "minutes": 0},
                {"from": "2025-03-10 14:40", "to": "2025-04-02 09:10", "minutes": 1.5},
                {"from": "10/03/2025", "to": "2025-04-02 09:10", "minutes": 60},
                {"from": "2025-03-10 14:40", "to": "2025-04-02 09:10"}):
        with pytest.raises(ProjectError):
            _clock([bad])
    with pytest.raises(ProjectError):
        _clock({"from": "2025-03-10 14:40"})


def test_the_correction_reaches_the_reader_and_puts_step_1_out_of_date(tmp_path):
    p = demo.create(tmp_path / "Demo Ridge", today=date(2026, 11, 18))
    before = FEEDS["data"](p.s)
    rule = {"from": "1990-01-01 00:00", "to": "1990-01-02 00:00", "minutes": 60, "note": "a period before the data"}
    p.update("data", {"clock_corrections": [rule]})
    assert Project.open(p.folder).s["data"]["clock_corrections"] == [rule]
    assert FEEDS["data"](p.s) != before                                      # a changed correction: step 1 is out of date
    out = p.run("data", log=quiet)
    sent = json.loads((p.folder / "runs" / "reader_settings.json").read_text(encoding="utf-8"))
    assert sent["clock_corrections"] == [rule]
    findings = (p.folder / out["run_dir"] / "findings.txt").read_text(encoding="utf-8")
    assert "The clock correction for 01 Jan 1990 00:00 to 02 Jan 1990 00:00 matched no record" in findings
    p.update("data", {"clock_corrections": []})
    assert p.s["data"]["clock_corrections"] == []


def test_windows_is_asked_to_stay_awake_during_a_cfd_run_and_nothing_happens_elsewhere():
    calls = []

    class Kernel32:
        def SetThreadExecutionState(self, flags):  # noqa: N802  (the Windows name)
            calls.append(flags)
            return 1

    assert cfd.keep_awake(True, Kernel32()) and cfd.keep_awake(False, Kernel32())
    assert calls == [cfd.ES_CONTINUOUS | cfd.ES_SYSTEM_REQUIRED, cfd.ES_CONTINUOUS]
    if os.name != "nt":
        assert cfd.keep_awake(True) is False
