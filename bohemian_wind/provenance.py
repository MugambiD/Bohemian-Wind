"""The provenance record of a project: one machine-readable file, provenance.json, written after every step.

It says what any figure of the project rests on, so that the figure can be traced and the step run again: the
version of the program, when each step ran, a mark of the settings and input files it worked from, the settings
themselves, the method texts the results carry, and the main figures. Marks of files are taken from their content;
the files themselves are not copied and no measurement is in the record.

The record describes the project as it stands. A step that is out of date is named as such.
"""
from __future__ import annotations

import json
import platform
import sys
from datetime import datetime
from importlib import metadata

from . import APP_NOTICE, __version__

NAME = "provenance.json"
FORMAT = "bohemian-wind-provenance/1"
PACKAGES = ("numpy", "pandas", "scipy", "xarray", "py_wake", "python-docx", "matplotlib")
KEY_FIGURES = {
    "data": ("mean_ws", "height_m", "recovery_pct", "records", "first", "last"),
    "longterm": ("long_term", "factor", "r", "months", "lt_years", "reference"),
    "map": ("engine", "height_m", "res_m", "half_km", "mean_ws_min", "mean_ws_max"),
    "layout": ("n_placed", "capacity_mw", "gross_gwh", "wake_loss_pct", "other_loss_pct", "p50_gwh", "capacity_factor",
               "wake_model", "objective"),
    "environment": (),
    "pack": (),
}


def _methods(obj, path="", out=None) -> dict:
    """Every text a result carries under the name "method", by where it stands."""
    out = {} if out is None else out
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "method" and isinstance(v, str):
                out[path or "result"] = v
            elif isinstance(v, dict):
                _methods(v, f"{path}.{k}" if path else k, out)
    return out


def _versions() -> dict:
    found = {}
    for p in PACKAGES:
        try:
            found[p] = metadata.version(p)
        except metadata.PackageNotFoundError:
            pass
    return found


def record(project) -> dict:
    from .project import FEEDS, STEPS, _canon, _file_mark  # noqa: PLC0415
    memo: dict = {}
    steps = {}
    for step in STEPS:
        res = project.result(step)
        feed = FEEDS[step](project.s) if step in FEEDS else {}
        files = {k: {"name": str(v), "mark": _file_mark(project.resolve(v))} for k, v in feed.items()
                 if k.endswith("_file") and v}
        entry = {"status": project.status(step, memo), "settings": _canon({k: v for k, v in feed.items() if k != "losses"}),
                 "input_files": files}
        if res:
            entry.update({"ran_at": res.get("at"), "inputs_mark": res.get("inputs"), "based_on": res.get("based_on"),
                          "figures": {k: res[k] for k in KEY_FIGURES.get(step, ()) if k in res and not isinstance(res[k], (dict, list))},
                          "methods": _methods(res)})
            if step == "layout":
                entry["losses"] = res.get("losses")
                entry["uncertainty_pct"] = (res.get("uncertainty") or {}).get("sigma_pct")
                if res.get("cost"):
                    entry["cost"] = {k: res["cost"][k] for k in ("cost_of_energy_per_mwh", "currency", "link_m", "inputs", "method")}
            if step == "environment":
                nz, fl = res.get("noise") or {}, res.get("flicker") or {}
                entry["figures"] = {"noise_highest_dba": nz.get("highest_dba"), "noise_homes_over": nz.get("over_limit"),
                                    "flicker_worst_case_hours": fl.get("highest_hours"), "flicker_homes_over": fl.get("over_limit"),
                                    "flicker_expected_hours": (fl.get("expected") or {}).get("highest_hours"),
                                    "flicker_budget_loss_pct": (fl.get("budget") or {}).get("shutdown_loss_pct"),
                                    "quiet_modes_loss_pct": (nz.get("quiet_modes") or {}).get("energy_loss_pct")}
        steps[step] = entry
    return {"format": FORMAT, "written": f"{datetime.now():%Y-%m-%d %H:%M:%S}", "notice": APP_NOTICE,
            "program": {"name": "Bohemian Wind", "version": __version__, "python": sys.version.split()[0],
                        "system": platform.system(), "packages": _versions()},
            "project": {"name": project.s.get("name"), "created": project.s.get("created"),
                        "made_with_version": project.s.get("version"), "demo": bool(project.s.get("demo"))},
            "how_to_run_again": "bohemian-wind run <project folder> [step ...]; a step whose inputs_mark is unchanged gives the same figures",
            "steps": steps}


def write(project) -> str | None:
    """Write provenance.json into the project folder. A record that cannot be written never stops a step."""
    try:
        path = project.folder / NAME
        path.write_text(json.dumps(record(project), indent=1, default=str), encoding="utf-8")
        return NAME
    except Exception:  # noqa: BLE001
        return None
