"""The whole chain on the invented demo project, the daily intake, the permitting pack and the application's
programming interface. Run: python -m pytest tests/test_app.py -q"""
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytest.importorskip("py_wake")

from bohemian_wind import demo, intake  # noqa: E402
from bohemian_wind.app import server as srv  # noqa: E402
from bohemian_wind.intake import watch  # noqa: E402
from bohemian_wind.project import STEPS, Project, ProjectError  # noqa: E402

TODAY = date(2026, 11, 18)
quiet = lambda m: None  # noqa: E731


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    p = demo.create(tmp_path_factory.mktemp("demo") / "Demo Ridge", today=TODAY)
    for step in STEPS:
        p.run(step, log=quiet)
    return p


def test_demo_data_step_finds_every_planted_fault(project):
    r = project.result("data")
    text = " | ".join(r["findings"])
    assert "No data from 11 Nov 2025" in text and "(3.0 days)" in text                    # the logger gap
    assert "V4 WS 40 m: dead or fixed" in text and "D2 WD 58 m: dead or fixed" in text      # dead cup, fixed vane
    assert "2 records outside the physical limits" in text and "V1 WS 80 m (1)" in text and "V3 WS 60 m (1)" in text
    assert r["format"] == "Campbell Scientific TOA5" and r["top_height_m"] == 80.0 and r["months_usable"] == 14
    assert 7.0 < r["mean_ws"] < 8.0 and r["recovery_pct"] > 99 and 0.10 < r["shear_alpha"] < 0.20
    assert len(r["rose"]) == 12 and abs(sum(s["freq"] for s in r["rose"]) - 1) < 1e-6
    assert max(r["rose"], key=lambda s: s["freq"])["centre"] == 90.0                      # the demo wind is easterly
    assert (project.folder / r["report"]).is_file() and (project.folder / r["excel"]).is_file()
    page = (project.folder / r["report"]).read_text(encoding="utf-8")
    assert "[COMPLIANCE]" in page and "not an accredited or bankable assessment" in page
    assert len((project.folder / r["run_dir"] / "inputs.csv").read_text().splitlines()) == 15   # 14 files fingerprinted


def test_demo_long_term_and_map(project):
    lt = project.result("longterm")
    assert 0.93 < lt["factor"] < 0.99 and lt["r"] > 0.9 and lt["months"] == 14 and not lt["indicative"]
    assert lt["long_term"] == pytest.approx(lt["measured"] * lt["factor"])
    m = project.result("map")
    assert (project.folder / m["map"]).is_file() and (project.folder / m["map_print"]).is_file()
    assert m["engine"] == "linear" and m["min_ws"] < m["mast_mean_ws"] <= m["max_ws"] + 1e-9
    assert m["lt_factor"] == pytest.approx(lt["factor"])


def test_demo_layout_respects_every_constraint(project):
    from bohemian_wind.environment import constraints
    lay = project.result("layout")
    assert lay["n_placed"] == 10 and lay["capacity_mw"] == 45.0
    grid = project.wind_field(lay["turbine"]["hub_m"]).grid
    mask = np.load(project.folder / "layout" / "buildable.npz")["mask"]
    xs = np.array([p["x"] for p in lay["positions"]]); ys = np.array([p["y"] for p in lay["positions"]])
    assert mask[grid.index(xs, ys)].all()
    homes = constraints.points_of(project.features("receptors"), grid)
    d = np.hypot(xs[:, None] - np.array([h["x"] for h in homes])[None, :], ys[:, None] - np.array([h["y"] for h in homes])[None, :])
    assert d.min() >= 500.0 - grid.res / 2                              # setback from homes (to the cell centre)
    boundary = constraints.polygon_mask(grid, project.features("boundary")[0])
    assert boundary[grid.index(xs, ys)].all()
    for f in project.features("exclusions"):
        if f.kind == "polygon":
            assert not constraints.polygon_mask(grid, f)[grid.index(xs, ys)].any()
    u = lay["uncertainty"]
    assert lay["gross_gwh"] > lay["after_wake_gwh"] > lay["p50_gwh"] > u["p75_10_year"] > u["p90_10_year"] > u["p90_1_year"] > 0
    assert 0.25 < lay["capacity_factor"] < 0.60
    assert all(p["tip_amsl_m"] == pytest.approx(p["ground_elev_m"] + 201.5, abs=0.06) for p in lay["positions"])
    assert "not an accredited or bankable energy yield assessment" in lay["estimate_note"]
    rows = (project.folder / "layout" / "turbines.csv").read_text().splitlines()
    assert len(rows) == 11 and rows[0].startswith("id,latitude,longitude,utm_zone")


def test_demo_environment_and_pack(project):
    env = project.result("environment")
    assert env["noise"]["n_receptors"] == 40 and env["noise"]["over_limit"] == 0 and 38 < env["noise"]["highest_dba"] < 45
    assert env["flicker"]["over_limit"] >= 1 and env["flicker"]["with_any_flicker"] >= env["flicker"]["over_limit"]
    assert all((project.folder / env[k]).is_file() for k in ("noise_map", "flicker_map", "noise_map_print", "flicker_map_print"))
    pack = project.result("pack")
    folder = project.folder / pack["folder"]
    names = {p.name for p in folder.iterdir()}
    assert {"index.html", "turbine_coordinates.csv", "layout.geojson", "layout.kml", "constraints_applied.csv",
            "map_layout.png", "noise_at_homes.csv", "shadow_flicker_at_homes.csv"} <= names
    page = (folder / "index.html").read_text(encoding="utf-8")
    assert "[COMPLIANCE]" in page and "does not replace" in page and "makes no statement about what" in page
    gj = json.loads((folder / "layout.geojson").read_text())
    kinds = [f["properties"]["kind"] for f in gj["features"]]
    assert kinds.count("turbine") == 10 and kinds.count("met mast") == 1 and kinds.count("home") == 40
    assert (folder / "layout.kml").read_text().count("<Placemark>") == 51
    assert all(project.status(s) == "done" for s in STEPS)


def test_a_new_data_run_marks_later_steps_out_of_date(project, tmp_path):
    import shutil
    copy = tmp_path / "copy"
    shutil.copytree(project.folder, copy)
    p = Project.open(copy)
    p.run("data", log=quiet)
    assert p.status("data") == "done" and p.status("longterm") == "stale" and p.status("map") == "stale"
    assert p.status("layout") == "done"          # still based on the same map run; the map itself is what is stale
    p.run("map", log=quiet)
    assert p.status("layout") == "stale" and p.status("pack") == "done"
    state = p.state()
    assert [s["key"] for s in state["steps"]] == list(STEPS) and "results" not in state


def test_project_guards(tmp_path):
    p = Project.create(tmp_path / "p", "Example Hill")
    assert p.status("data") == "ready" and p.status("layout") == "blocked"
    with pytest.raises(ProjectError, match="Choose the folder"):
        p.run("data", log=quiet)
    with pytest.raises(ProjectError, match="Check the mast data first"):
        p.run("map", log=quiet)
    with pytest.raises(ProjectError, match="already a project"):
        Project.create(tmp_path / "p", "Again")
    with pytest.raises(ProjectError, match="No project found"):
        Project.open(tmp_path / "nothing")
    with pytest.raises(ProjectError):
        Project.create(tmp_path / "q", "Bad", lat=123, lon=0)
    (tmp_path / "homes.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {}, "geometry": {"type": "LineString", "coordinates": [[35, -5], [35.1, -5]]}}]}))
    with pytest.raises(ProjectError, match="holds no points"):
        p.attach("receptors", tmp_path / "homes.geojson")
    assert p.attach("exclusions", tmp_path / "homes.geojson") == "constraints/exclusions.geojson"
    empty = tmp_path / "empty"; empty.mkdir()
    p.update("data", {"folder": str(empty)})
    with pytest.raises(ProjectError, match="No logger files were recognised"):
        p.run("data", log=quiet)


def test_intake_detects_formats(tmp_path, project):
    d = intake.detect(project.folder / "data")
    assert d["format"] == "toa5" and d["counts"]["toa5"] == 14
    (tmp_path / "notes.txt").write_text("nothing to see")
    assert intake.detect(tmp_path)["format"] is None
    (tmp_path / "x.rld").write_bytes(b"\x00\x01")
    assert intake.detect(tmp_path)["format"] == "sympro"
    sig = intake.folder_signature(tmp_path)
    (tmp_path / "y.rld").write_bytes(b"\x00")
    assert intake.folder_signature(tmp_path) != sig
    with pytest.raises(intake.IntakeError):
        intake.detect(tmp_path / "missing")


def test_daily_intake_runs_only_on_new_files_and_alerts_when_silent(project, tmp_path):
    import shutil
    copy = tmp_path / "copy"
    shutil.copytree(project.folder, copy)
    p = Project.open(copy)
    p.run("data", log=quiet)                                                   # signature of the copied folder
    now = datetime(2026, 11, 1, 6, 0)
    st = watch.check_once(p, log=quiet, now=now)
    assert st["ran"] is False and st["state"] == "ok" and "No new files" in st["message"]
    newest = sorted((copy / "data").glob("*.dat"))[-1]
    lines = newest.read_text().splitlines()
    newest.write_text("\n".join(lines[:-144]) + "\n")                          # the logger's last day "arrives" later
    st = watch.check_once(p, log=quiet, now=now)
    assert st["ran"] is True and st["history"][0]["new_records"] == -144
    newest.write_text("\n".join(lines) + "\n")
    st = watch.check_once(p, log=quiet, now=now)
    assert st["ran"] is True and st["history"][0]["new_records"] == 144 and st["state"] == "ok"
    assert "144 new records" in st["message"] and len(st["history"]) == 2
    late = watch.check_once(p, log=quiet, now=datetime(2026, 11, 9, 6, 0))
    assert late["state"] == "stale" and "8.3 days old" in late["message"]
    assert json.loads((copy / "intake" / "status.json").read_text())["state"] == "stale"
    p.update("data", {"folder": str(tmp_path / "gone")})
    assert watch.check_once(p, log=quiet)["state"] == "error"


def _call(url, token=None, body=None, host=None):
    req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"} if body is not None else {})
    if token:
        req.add_header("X-BW-Token", token)
    if host:
        req.add_header("Host", host)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def test_application_interface_and_its_locks(project, tmp_path, monkeypatch):
    monkeypatch.setattr(srv, "CONFIG", tmp_path / "config.json")
    monkeypatch.setattr(srv, "projects_home", lambda: tmp_path / "projects")
    server, app, url = srv.serve(8930, open_browser=False, quiet=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        code, page, headers = _call(url)
        assert code == 200 and app.token.encode() in page and b"{{TOKEN}}" not in page
        assert "script-src 'self'" in headers["Content-Security-Policy"] and b"<script>" not in page
        assert _call(url + "static/app.js")[0] == 200 and _call(url + "static/img/hero.jpg")[0] == 200
        assert _call(url + "static/../server.py")[0] == 404                       # nothing outside the static folder
        assert _call(url + "api/state")[0] == 403                                  # no token
        assert _call(url + "api/state", token="wrong")[0] == 403
        assert _call(url + "api/state", token=app.token, host="evil.example:8930")[0] == 403   # another site's name
        assert _call(url + "api/run", body={"step": "data"})[0] == 403             # no token on a change
        code, body, _ = _call(url + "api/state", token=app.token)
        st = json.loads(body)
        assert code == 200 and st["project"] is None and len(st["app"]["turbines"]) == 5
        assert all(t["name"].startswith("Generic") for t in st["app"]["turbines"])
        assert _call(url + "api/run", token=app.token, body={"step": "data"})[0] == 400      # no project yet
        code, body, _ = _call(url + "api/project", token=app.token, body={"action": "open", "folder": str(project.folder)})
        st = json.loads(body)
        assert code == 200 and st["project"]["name"] == demo.NAME and st["recent"][0]["folder"] == str(project.folder)
        rel = project.result("layout")["map"]
        assert _call(f"{url}files/{app.token}/{rel}")[0] == 200
        assert _call(f"{url}files/wrong/{rel}")[0] == 403
        assert _call(f"{url}files/{app.token}/../../../etc/passwd")[0] == 404      # nothing outside the project
        code, body, _ = _call(url + "api/settings", token=app.token, body={"section": "layout", "values": {"n_turbines": 6}})
        assert code == 200 and json.loads(body)["project"]["layout"]["n_turbines"] == 6
        assert _call(url + "api/settings", token=app.token, body={"section": "nonsense", "values": {}})[0] == 400
        code, body, _ = _call(url + "api/run", token=app.token, body={"steps": ["layout", "pack"]})
        job = json.loads(body)
        assert code == 200 and job["status"] == "running"
        assert _call(url + "api/run", token=app.token, body={"step": "data"})[0] == 400       # one run at a time
        assert _call(url + "api/settings", token=app.token, body={"section": "layout", "values": {"n_turbines": 7}})[0] == 400
        for _ in range(600):
            j = json.loads(_call(f"{url}api/job?id={job['id']}", token=app.token)[1])
            if j["status"] != "running":
                break
            time.sleep(0.2)
        assert j["status"] == "done" and j["done"] == ["layout", "pack"] and any("6 turbines" in line for line in j["log"])
        st = json.loads(_call(url + "api/state", token=app.token)[1])
        lay = next(s for s in st["project"]["steps"] if s["key"] == "layout")
        assert lay["result"]["n_placed"] == 6 and lay["status"] == "done"
        env = next(s for s in st["project"]["steps"] if s["key"] == "environment")
        assert env["status"] == "stale"                                           # the layout changed under it
        assert json.loads(_call(url + "api/project", token=app.token, body={"action": "close"})[1])["project"] is None
    finally:
        server.shutdown()
        server.server_close()
