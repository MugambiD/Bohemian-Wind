"""Alternative layouts of one project, kept side by side and compared.

    bohemian-wind layout save FOLDER NAME               keep the layout the project holds now under a name
    bohemian-wind layout list FOLDER                    the layouts kept
    bohemian-wind layout duplicate FOLDER NAME NEW      a copy of a kept layout's positions, to change
    bohemian-wind layout move FOLDER NAME T07 --east 300 --north -150     move a turbine of that copy, in metres
    bohemian-wind layout use FOLDER NAME                make those positions the layout step 4 takes as given
    bohemian-wind layout compare FOLDER A B             set two kept layouts against each other

A kept layout is two files in the project's layouts/ folder: NAME.geojson, the turbine positions (one point per
turbine; any GIS program opens and edits it), and NAME.json, what steps 4 and 5 gave for them and what they rested
on. The way to try a change: save the layout as A, duplicate it as B, move turbines of B (here or in a GIS program),
use B, run steps 4 and 5, save as B, compare A and B.

A comparison gives the change in turbines, capacity, gross energy, wake loss, other losses, net energy, cost of
energy and homes affected, as amounts and as percentages; which turbines moved and how far; and what each turbine
yields in the two. It warns when the two layouts did not rest on the same wind, turbine, wake model or losses,
because a difference between them is then not the difference the positions make.

[COMPLIANCE] Developer estimates. A comparison ranks alternatives; it is not an energy yield assessment.
"""
from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from pathlib import Path

import numpy as np

from . import APP_NOTICE, __version__

FOLDER = "layouts"
MOVED_M = 1.0              # a turbine counts as moved beyond this


class LayoutError(ValueError):
    pass


def _slug(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_-]+", "-", str(name).strip()).strip("-")
    if not s:
        raise LayoutError("Give the layout a name of letters and digits.")
    return s[:60]


def _path(project, name: str, ext: str) -> Path:
    return project.path(FOLDER, f"{_slug(name)}.{ext}")


def _geojson(positions: list) -> dict:
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"name": q["id"]}, "geometry": {"type": "Point", "coordinates": [q["lon"], q["lat"]]}}
        for q in positions]}


def save(project, name: str) -> dict:
    """Keep the project's present layout, with the screening when it belongs to this layout."""
    lay = project.result("layout")
    if not lay or project.status("layout") != "done":
        raise LayoutError("There is no up-to-date layout to keep: run step 4 first.")
    env = project.result("environment") if project.status("environment") == "done" else None
    kept = {"name": str(name).strip(), "saved": f"{datetime.now():%Y-%m-%d %H:%M:%S}", "program_version": __version__,
            "figures": {k: lay.get(k) for k in ("n_placed", "capacity_mw", "gross_gwh", "wake_loss_pct", "other_loss_pct", "p50_gwh",
                                                 "capacity_factor")},
            "cost": ({k: lay["cost"][k] for k in ("cost_of_energy_per_mwh", "currency", "link_m")} if lay.get("cost") else None),
            "p90_10_year_gwh": (lay.get("uncertainty") or {}).get("p90_10_year"),
            "basis": {"layout_ran_at": lay.get("at"), "wind": lay.get("based_on"), "turbine": lay.get("turbine"),
                      "wake_model": lay.get("wake_model"), "losses": lay.get("losses"), "engine": lay.get("engine"),
                      "mast_mean_ws": lay.get("mast_mean_ws"), "given": bool(project.s["layout"].get("positions_file"))},
            "positions": [{k: q.get(k) for k in ("id", "lat", "lon", "x", "y", "free_ws", "gross_mwh", "wake_loss_pct", "p50_mwh")}
                          for q in lay["positions"]],
            "screening": None}
    if env:
        nz, fl = env["noise"], env["flicker"]
        kept["screening"] = {
            "noise_limit_dba": nz.get("limit_dba"), "noise_homes_over": nz.get("over_limit"), "noise_highest_dba": nz.get("highest_dba"),
            "flicker_homes_over": fl.get("over_limit"), "flicker_worst_case_hours": fl.get("highest_hours"),
            "flicker_expected_hours": (fl.get("expected") or {}).get("highest_hours"),
            "flicker_budget_loss_pct": (fl.get("budget") or {}).get("shutdown_loss_pct"),
            "quiet_modes_loss_pct": (nz.get("quiet_modes") or {}).get("energy_loss_pct"),
            "homes_with_any_flicker": fl.get("with_any_flicker"), "homes_assessed": nz.get("n_receptors")}
    _path(project, name, "json").write_text(json.dumps(kept, indent=1), encoding="utf-8")
    _path(project, name, "geojson").write_text(json.dumps(_geojson(lay["positions"]), indent=1), encoding="utf-8")
    return kept


def kept(project, name: str) -> dict:
    f = _path(project, name, "json")
    if not f.is_file():
        have = ", ".join(x["name"] for x in listing(project)) or "none yet"
        raise LayoutError(f"No layout is kept under the name '{name}' (kept: {have}).")
    return json.loads(f.read_text(encoding="utf-8"))


def listing(project) -> list:
    out = []
    for f in sorted((project.folder / FOLDER).glob("*.json")) if (project.folder / FOLDER).is_dir() else []:
        try:
            k = json.loads(f.read_text(encoding="utf-8"))
            out.append({"name": k["name"], "saved": k["saved"], "turbines": k["figures"]["n_placed"], "p50_gwh": k["figures"]["p50_gwh"],
                        "screened": k["screening"] is not None})
        except (ValueError, KeyError):
            continue
    return out


def sets(project) -> list:
    """Every set of positions in layouts/: those kept with results and the copies still being changed."""
    folder = project.folder / FOLDER
    names = {_slug(x["name"]): x["name"] for x in listing(project)}
    used = str(project.s["layout"].get("positions_file") or "")
    out = []
    for f in sorted(folder.glob("*.geojson")) if folder.is_dir() else []:
        try:
            ids = [str(ft["properties"].get("name")) for ft in json.loads(f.read_text(encoding="utf-8"))["features"]]
        except (ValueError, KeyError, TypeError):
            continue
        out.append({"key": f.stem, "name": names.get(f.stem, f.stem), "kept": f.stem in names, "turbines": ids,
                    "in_use": used == f"{FOLDER}/{f.name}"})
    return out


def _points(project, name: str) -> tuple:
    f = _path(project, name, "geojson")
    if not f.is_file():
        raise LayoutError(f"There are no positions under the name '{name}': save or duplicate a layout first.")
    return f, json.loads(f.read_text(encoding="utf-8"))


def duplicate(project, name: str, new: str) -> str:
    """Copy a layout's positions under a new name, to be changed. Returns the file, relative to the project."""
    _, gj = _points(project, name)
    target = _path(project, new, "geojson")
    if target.exists():
        raise LayoutError(f"Positions named '{new}' exist already: choose another name.")
    target.write_text(json.dumps(gj, indent=1), encoding="utf-8")
    return project.rel(target)


def move(project, name: str, turbine: str, east_m: float = 0.0, north_m: float = 0.0) -> dict:
    """Move one turbine of a set of positions by metres to the east and to the north."""
    f, gj = _points(project, name)
    for ft in gj["features"]:
        if str(ft["properties"].get("name")) == str(turbine):
            lon, lat = ft["geometry"]["coordinates"][:2]
            lat2 = lat + float(north_m) / 111_320.0
            lon2 = lon + float(east_m) / (111_320.0 * np.cos(np.radians(lat)))
            ft["geometry"]["coordinates"] = [float(lon2), float(lat2)]
            f.write_text(json.dumps(gj, indent=1), encoding="utf-8")
            return {"turbine": turbine, "from": [lat, lon], "to": [float(lat2), float(lon2)], "east_m": float(east_m), "north_m": float(north_m)}
    raise LayoutError(f"'{name}' has no turbine {turbine} (it has: {', '.join(str(ft['properties'].get('name')) for ft in gj['features'])}).")


def use(project, name: str) -> str:
    """Make a set of positions the layout that step 4 takes as given. Run step 4 (and 5) afterwards."""
    f, _ = _points(project, name)
    project.update("layout", {"positions_file": project.rel(f)})
    project.save()
    return project.rel(f)


def design_again(project) -> None:
    """Let step 4 place the turbines itself again."""
    project.update("layout", {"positions_file": ""})
    project.save()


DEMO_BASE, DEMO_TRY = "Baseline", "Try a change"


def demo_alternatives(project) -> bool:
    """In the demo project, once steps 4 and 5 have run: keep the designed layout as "Baseline" and put a copy
    beside it, "Try a change", with three turbines moved, so that a first-time user can run it and compare the two
    without preparing anything. Done once; never in a project of the user's own."""
    if not project.s.get("demo") or _path(project, DEMO_BASE, "json").is_file():
        return False
    if project.status("layout") != "done" or project.status("environment") != "done" or project.s["layout"].get("positions_file"):
        return False
    base = save(project, DEMO_BASE)
    ids = [q["id"] for q in base["positions"]]
    if len(ids) >= 7 and not _path(project, DEMO_TRY, "geojson").exists():
        duplicate(project, DEMO_BASE, DEMO_TRY)
        for tid, east, north in ((ids[1], 0.0, 400.0), (ids[4], 300.0, -200.0), (ids[6], -250.0, 0.0)):
            move(project, DEMO_TRY, tid, east, north)
    return True


def _change(a, b) -> dict:
    if a is None or b is None:
        return {"a": a, "b": b, "change": None, "change_pct": None}
    return {"a": a, "b": b, "change": b - a, "change_pct": (100.0 * (b - a) / a) if a else None}


def compare(project, a: str, b: str) -> dict:
    A, B = kept(project, a), kept(project, b)
    fa, fb = A["figures"], B["figures"]
    rows = {"Turbines": _change(fa["n_placed"], fb["n_placed"]), "Capacity, MW": _change(fa["capacity_mw"], fb["capacity_mw"]),
            "Gross energy, GWh a year": _change(fa["gross_gwh"], fb["gross_gwh"]),
            "Wake loss, %": _change(fa["wake_loss_pct"], fb["wake_loss_pct"]),
            "Other losses, %": _change(fa["other_loss_pct"], fb["other_loss_pct"]),
            "Net energy P50, GWh a year": _change(fa["p50_gwh"], fb["p50_gwh"]),
            "P90 over 10 years, GWh a year": _change(A.get("p90_10_year_gwh"), B.get("p90_10_year_gwh"))}
    if A.get("cost") and B.get("cost"):
        rows[f"Cost of energy, {A['cost']['currency']} per MWh"] = _change(A["cost"]["cost_of_energy_per_mwh"], B["cost"]["cost_of_energy_per_mwh"])
        rows["Cables and roads, km"] = _change(A["cost"]["link_m"] / 1000.0, B["cost"]["link_m"] / 1000.0)
    sa, sb = A.get("screening"), B.get("screening")
    if sa and sb:
        for label, key in (("Homes over the noise level", "noise_homes_over"), ("Highest noise level, dB(A)", "noise_highest_dba"),
                           ("Homes over a flicker limit (worst case)", "flicker_homes_over"),
                           ("Most flicker, worst case, hours a year", "flicker_worst_case_hours"),
                           ("Most flicker, expected, hours a year", "flicker_expected_hours")):
            rows[label] = _change(sa.get(key), sb.get(key))
    warnings = []
    ba, bb = A["basis"], B["basis"]
    for what, key in (("wind (mast data, long-term correction or wind map)", "wind"), ("turbine", "turbine"), ("wake model", "wake_model"),
                      ("loss assumptions", "losses"), ("wind map method", "engine")):
        if ba.get(key) != bb.get(key):
            warnings.append(f"The two layouts did not rest on the same {what}: part of the difference between them comes from that, "
                            "not from where the turbines stand.")
    if (sa is None) != (sb is None):
        warnings.append(f"Only '{(A if sa else B)['name']}' was kept with its noise and flicker screening: run step 5 for the other "
                        "before keeping it, to compare the homes affected.")
    elif sa is None:
        warnings.append("Neither layout was kept with its noise and flicker screening, so the homes affected are not compared.")
    pa, pb = {q["id"]: q for q in A["positions"]}, {q["id"]: q for q in B["positions"]}
    turbines = []
    for tid in sorted(set(pa) | set(pb)):
        qa, qb = pa.get(tid), pb.get(tid)
        row = {"id": tid, "in": "both" if qa and qb else ("A only" if qa else "B only")}
        if qa and qb:
            d = float(np.hypot(qb["x"] - qa["x"], qb["y"] - qa["y"]))
            row.update(moved_m=round(d, 0), moved=d > MOVED_M, east_m=round(qb["x"] - qa["x"], 0), north_m=round(qb["y"] - qa["y"], 0))
        for k in ("free_ws", "wake_loss_pct", "p50_mwh"):
            row[k] = _change((qa or {}).get(k), (qb or {}).get(k))
        turbines.append(row)
    moved = [t for t in turbines if t.get("moved")]
    net = rows["Net energy P50, GWh a year"]
    head = (f"'{B['name']}' against '{A['name']}': net energy {net['change']:+.2f} GWh a year ({net['change_pct']:+.2f} %), wake loss "
            f"{fa['wake_loss_pct']:.2f} % to {fb['wake_loss_pct']:.2f} %; {len(moved)} turbine{'s' if len(moved) != 1 else ''} moved"
            + (f" ({', '.join(t['id'] for t in moved[:8])}{' ...' if len(moved) > 8 else ''})" if moved else "") + ".")
    return {"a": A["name"], "b": B["name"], "headline": head, "rows": rows, "turbines": turbines, "warnings": warnings,
            "notice": APP_NOTICE, "program_versions": [A["program_version"], B["program_version"]]}


def _num(v, digits=2) -> str:
    return "–" if v is None else (f"{v:,.0f}" if float(v).is_integer() and digits == 0 else f"{v:,.{digits}f}")


def write_comparison(project, a: str, b: str) -> dict:
    """Write the comparison as a one-page text (Markdown) and a table (CSV) in layouts/. Returns the comparison with
    the two file names."""
    c = compare(project, a, b)
    base = f"compare_{_slug(a)}_vs_{_slug(b)}"
    lines = [f"# Layout comparison: {c['a']} and {c['b']}", "", f"{project.s.get('name') or ''} · {datetime.now():%d %B %Y} · Bohemian Wind "
             f"{__version__}", "", f"> **[COMPLIANCE]** {APP_NOTICE}", "", c["headline"], ""]
    lines += [f"> **Caution.** {w}" for w in c["warnings"]] + ([""] if c["warnings"] else [])
    lines += [f"| | {c['a']} | {c['b']} | Change | Change, % |", "|---|---|---|---|---|"]
    for label, r in c["rows"].items():
        whole = label in ("Turbines", "Homes over the noise level", "Homes over a flicker limit (worst case)")
        lines.append(f"| {label} | {_num(r['a'], 0 if whole else 2)} | {_num(r['b'], 0 if whole else 2)} | "
                     f"{'–' if r['change'] is None else format(r['change'], '+,.0f' if whole else '+,.2f')} | "
                     f"{'–' if r['change_pct'] is None else format(r['change_pct'], '+.2f')} |")
    lines += ["", "## Turbine by turbine", "", f"| Turbine | Moved, m (east, north) | Free wind, m/s: {c['a']} → {c['b']} | "
              f"Wake loss, %: {c['a']} → {c['b']} | Net P50, MWh: {c['a']} → {c['b']} | Change, MWh |", "|---|---|---|---|---|---|"]
    for t in c["turbines"]:
        mv = (f"{t['moved_m']:.0f} ({t['east_m']:+.0f}, {t['north_m']:+.0f})" if t.get("moved") else "–") if t["in"] == "both" else t["in"]
        w, p, f = t["wake_loss_pct"], t["p50_mwh"], t["free_ws"]
        lines.append(f"| {t['id']} | {mv} | {_num(f['a'])} → {_num(f['b'])} | {_num(w['a'])} → {_num(w['b'])} | "
                     f"{_num(p['a'], 0)} → {_num(p['b'], 0)} | {'–' if p['change'] is None else format(p['change'], '+,.0f')} |")
    lines += ["", "A turbine that did not move can still change: its neighbours' wakes reach it differently.", ""]
    project.path(FOLDER, base + ".md").write_text("\n".join(lines), encoding="utf-8")
    with open(project.path(FOLDER, base + ".csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["quantity", c["a"], c["b"], "change", "change_pct"])
        for label, r in c["rows"].items():
            w.writerow([label, r["a"], r["b"], r["change"], r["change_pct"]])
        w.writerow([])
        w.writerow(["turbine", "in", "moved_m", "east_m", "north_m", "p50_mwh_a", "p50_mwh_b", "wake_loss_pct_a", "wake_loss_pct_b"])
        for t in c["turbines"]:
            w.writerow([t["id"], t["in"], t.get("moved_m"), t.get("east_m"), t.get("north_m"), t["p50_mwh"]["a"], t["p50_mwh"]["b"],
                        t["wake_loss_pct"]["a"], t["wake_loss_pct"]["b"]])
    c["files"] = [f"{FOLDER}/{base}.md", f"{FOLDER}/{base}.csv"]
    return c


def main(argv, say=print) -> int:
    from .project import Project, ProjectError  # noqa: PLC0415
    import argparse  # noqa: PLC0415
    ap = argparse.ArgumentParser(prog="bohemian-wind layout", description="Keep alternative layouts of a project and compare them.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for cmd, extra in (("save", ["name"]), ("list", []), ("duplicate", ["name", "new"]), ("use", ["name"]), ("compare", ["a", "b"]),
                       ("design", [])):
        p = sub.add_parser(cmd)
        p.add_argument("folder")
        for e in extra:
            p.add_argument(e)
    m = sub.add_parser("move")
    for e in ("folder", "name", "turbine"):
        m.add_argument(e)
    m.add_argument("--east", type=float, default=0.0)
    m.add_argument("--north", type=float, default=0.0)
    args = ap.parse_args(argv)
    try:
        project = Project.open(args.folder)
        if args.cmd == "save":
            k = save(project, args.name)
            say(f"Kept '{k['name']}': {k['figures']['n_placed']} turbines, net P50 {k['figures']['p50_gwh']:.1f} GWh a year"
                + ("" if k["screening"] else "; without the noise and flicker screening (step 5 is not up to date)") + ".")
        elif args.cmd == "list":
            for x in listing(project):
                say(f"{x['name']}: {x['turbines']} turbines, net P50 {x['p50_gwh']:.1f} GWh a year, kept {x['saved']}"
                    + ("" if x["screened"] else " (no screening)"))
        elif args.cmd == "duplicate":
            say(f"Positions copied to {duplicate(project, args.name, args.new)}. Move turbines, then: layout use, run steps 4 and 5, layout save.")
        elif args.cmd == "move":
            r = move(project, args.name, args.turbine, args.east, args.north)
            say(f"{r['turbine']} of '{args.name}' moved {r['east_m']:+.0f} m east, {r['north_m']:+.0f} m north.")
        elif args.cmd == "use":
            say(f"Step 4 now takes the positions of {use(project, args.name)} as given. Run steps 4 and 5.")
        elif args.cmd == "design":
            design_again(project)
            say("Step 4 places the turbines itself again. Run steps 4 and 5.")
        else:
            c = write_comparison(project, args.a, args.b)
            say(c["headline"])
            for w in c["warnings"]:
                say(f"Caution: {w}")
            say(f"Written: {', '.join(c['files'])}")
            say(APP_NOTICE)
        return 0
    except (LayoutError, ProjectError) as e:
        say(f"ERROR: {e}")
        return 2
