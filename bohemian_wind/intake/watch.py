"""Daily intake: watch a project's data folder and run the checks again whenever logger files arrive.

What it does: looks at the folder every few minutes (or once, from a scheduled task); when a file has been added
or changed it runs step 1 again, records what is new, and raises an alert when the newest record is older than
expected (a silent logger or a broken upload).

What it does not do: fetch files from the logger, its email or its cloud service. Point the logger's own software,
or a synced folder (OneDrive, Dropbox, a mail rule that saves attachments), at the data folder.
"""
from __future__ import annotations

import json
import re
import threading
from collections import Counter
from datetime import datetime
from pathlib import Path

from .. import DISCLAIMER
from . import folder_signature

STATUS_FILE = "intake/status.json"
HISTORY_KEPT = 60


def load_status(project) -> dict:
    f = project.folder / STATUS_FILE
    if f.is_file():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"history": []}


_MONTHS = re.compile(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b")
_NUMBERS = re.compile(r"\d[\d,.:]*")


def _kind(finding: str) -> str:
    """A finding with its numbers and dates taken out: what kind of finding it is."""
    return _NUMBERS.sub("#", _MONTHS.sub("Mon", finding))


def news(before: list, after: list) -> list:
    """Findings of the latest run that are news. A finding that was there before and only moved with the record
    count ('... in 2,390 records' becoming '... in 2,534 records', the summary line, the disclaimer) is not; a
    second finding of a kind that was there once before is. On real daily files, every day's run otherwise
    reported its own summary line as a new finding."""
    was = set(before)
    left = Counter(_kind(f) for f in before)
    for f in after:
        if f in was:
            left[_kind(f)] -= 1
    out = []
    for f in after:
        if f in was or f == DISCLAIMER or f.startswith("Data: "):
            continue
        k = _kind(f)
        if left[k] > 0:
            left[k] -= 1
            continue
        out.append(f)
    return out


def _hours_since(stamp: str, now: datetime) -> float | None:
    try:
        return (now - datetime.fromisoformat(str(stamp)[:19])).total_seconds() / 3600.0
    except ValueError:
        return None


def check_once(project, log=print, force: bool = False, now: datetime | None = None) -> dict:
    """Run step 1 if the data folder changed since the last run (or if force). Returns the intake status, which is
    also written to <project>/intake/status.json."""
    now = now or datetime.now()
    status = load_status(project)
    folder = project.s["data"].get("folder")
    src = Path(folder) if folder and Path(folder).is_absolute() else project.folder / (folder or "")
    status.update(last_check=f"{now:%Y-%m-%d %H:%M:%S}", folder=str(src), state="ok", message="", ran=False)
    if not folder or not src.exists():
        status.update(state="error", message="The data folder is not set or no longer exists.")
    else:
        prev = project.result("data")
        sig = folder_signature(src)
        if force or not prev or prev.get("signature") != sig:
            log(f"Data folder changed: checking {src} ...")
            try:
                res = project.run_data(log=log)
            except Exception as e:  # noqa: BLE001  (the watcher must survive a bad file and say what happened)
                status.update(state="error", message=f"The checks could not run: {e}")
            else:
                new_findings = news((prev or {}).get("findings") or [], res.get("findings", []))[:20]
                entry = {"at": status["last_check"], "records": res.get("records"),
                         "new_records": (res.get("records") or 0) - ((prev or {}).get("records") or 0),
                         "last_record": str(res.get("period", ["", ""])[1]), "recovery_pct": res.get("recovery_pct"),
                         "mean_ws": res.get("mean_ws"), "new_findings": new_findings}
                status["history"] = ([entry] + status.get("history", []))[:HISTORY_KEPT]
                status.update(ran=True, last_run=status["last_check"])
        cur = project.result("data")
        if cur and status["state"] != "error":
            last_record = str(cur.get("period", ["", ""])[1])
            age = _hours_since(last_record, now)
            limit = float(project.s["data"].get("stale_hours") or 36)
            status.update(last_record=last_record, data_age_hours=None if age is None else round(age, 1))
            if age is not None and age > limit:
                status.update(state="stale", message=(
                    f"The newest record is about {age / 24:.1f} days old (limit {limit:g} hours). Check that the logger is "
                    "sending and that its files reach the data folder."))
            elif status.get("ran"):
                h = status["history"][0]
                status["message"] = (f"{h['new_records']:,} new records; {len(h['new_findings'])} new finding"
                                     f"{'s' if len(h['new_findings']) != 1 else ''}.")
            else:
                status["message"] = "No new files since the last check."
    f = project.path(*STATUS_FILE.split("/"))
    f.write_text(json.dumps(status, indent=1), encoding="utf-8")
    return status


def watch(project, minutes: float = 60.0, stop: threading.Event | None = None, log=print, lock=None) -> None:
    """Check now, then every `minutes`, until stop is set. lock: held while a check runs (shared with other work on
    the same project)."""
    stop = stop or threading.Event()
    while not stop.is_set():
        if lock is None:
            st = check_once(project, log=log)
        else:
            with lock:
                st = check_once(project, log=log)
        log(f"[{st['last_check']}] {st['state']}: {st['message']}")
        stop.wait(max(60.0, minutes * 60.0))
