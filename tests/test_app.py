"""The whole chain on the invented demo project, the daily intake, the permitting pack and the application's
programming interface. Run: python -m pytest tests/test_app.py -q"""
import csv
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
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
    # the map stands on the long-term mean itself, carried to the map height with the measured shear
    d = project.result("data")
    assert m["annual_mean_ws"] == pytest.approx(lt["long_term"])
    assert m["mast_mean_ws"] == pytest.approx(lt["long_term"] * (m["height_m"] / d["top_height_m"]) ** d["shear_alpha"], rel=1e-6)


def test_a_long_term_correction_older_than_the_data_is_not_used(tmp_path):
    p = demo.create(tmp_path / "Demo Ridge", today=TODAY)
    p.run("data", log=quiet)
    p.run("longterm", log=quiet)
    assert p.speed_scale()["mean_ws"] == pytest.approx(p.result("longterm")["long_term"])
    assert p.warnings(p.climate()) == []
    p.s["results"]["longterm"]["cautions"] = ["Only 5 of the 12 calendar months were measured."]
    assert p.warnings(p.climate()) == ["Only 5 of the 12 calendar months were measured."]      # carried to the map and the energy
    p.s["results"]["data"]["at"] = "2099-01-01 00:00:00"            # the mast data were checked again afterwards
    assert p.status("longterm") == "stale"
    with pytest.raises(ProjectError, match="Run step 2"):
        p.speed_scale()


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
    lt = project.result("longterm")
    assert f"Long-term mean {lt['long_term']:.2f} m/s at 80 m" in page and f"times a long-term factor of {lt['factor']:.3f}" in page
    assert "Check before relying on these figures" not in page          # the demo gives no cause for one
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
    assert p.status("layout") == "stale" and p.status("environment") == "stale"     # they rest on a map that is out of date
    with pytest.raises(ProjectError, match=r"'Wind map' \(step 3\) is out of date. 'Mast data' was run again"):   # nothing new is built on it
        p.run("layout", log=quiet)
    with pytest.raises(ProjectError, match="Run step 2"):          # the old long-term mean belongs to the old data
        p.run("map", log=quiet)
    p.run("longterm", log=quiet)
    assert p.status("layout") == "stale"         # the layout rests on the long-term mean too
    p.run("map", log=quiet)
    assert p.status("map") == "done" and p.status("layout") == "stale" and p.status("pack") == "stale"
    state = p.state()
    assert [s["key"] for s in state["steps"]] == list(STEPS) and "results" not in state


def test_a_layout_of_your_own_is_taken_as_given(project, tmp_path):
    import shutil
    copy = tmp_path / "copy"
    shutil.copytree(project.folder, copy)
    p = Project.open(copy)
    designed = p.result("layout")
    p.attach("positions", copy / "layout" / "turbines.csv")          # the application's own table is a valid positions file
    p.update("layout", {"n_turbines": 3})                            # not used: the file decides
    res = p.run("layout", log=quiet)
    assert res["given_layout"] and res["n_placed"] == res["n_requested"] == designed["n_placed"]
    assert [q["id"] for q in res["positions"]] == [q["id"] for q in designed["positions"]]
    assert res["p50_gwh"] == pytest.approx(designed["p50_gwh"], rel=1e-3) and res["outside_buildable"] == []
    assert "were given, not designed" in res["notes"][0]
    # a turbine on ground the constraints rule out is worked out all the same, and named
    home = p.features("receptors")[0].coords[0]
    good = designed["positions"][0]
    own = tmp_path / "own.csv"
    own.write_text(f"name,lat,lon\nA1,{good['lat']:.6f},{good['lon']:.6f}\nAt a home,{home[0]:.6f},{home[1]:.6f}\n", encoding="utf-8")
    p.attach("positions", own)
    res = p.run("layout", log=quiet)
    assert res["n_placed"] == 2 and res["outside_buildable"] == ["At a home"]
    assert any("outside the buildable area" in w and "At a home" in w for w in res["warnings"])
    page = (p.folder / p.run("pack", log=quiet)["index"]).read_text(encoding="utf-8")
    assert "Check before relying on these figures" in page and "At a home" in page      # the pack carries the warning
    # a position off the map stops the run with a message that says what to do
    own.write_text(f"name,lat,lon\nA1,{good['lat']:.6f},{good['lon']:.6f}\nFar away,{good['lat'] + 1:.6f},{good['lon']:.6f}\n", encoding="utf-8")
    p.attach("positions", own)
    with pytest.raises(ProjectError, match="1 of the 2 turbine positions given lies outside the mapped area"):
        p.run("layout", log=quiet)
    # removing the file brings the designed layout back
    p.update("layout", {"positions_file": "", "n_turbines": designed["n_requested"]})
    res = p.run("layout", log=quiet)
    assert not res["given_layout"] and res["n_placed"] == designed["n_placed"]
    with pytest.raises(ProjectError, match="holds no points"):
        p.attach("positions", copy / p.s["layout"]["boundary_file"])


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
    assert p.s["map"]["res_m"] == 50.0                                 # the default cell follows narrow ridges


def test_a_map_too_big_to_hold_is_refused_with_a_cell_size_that_fits(project, tmp_path):
    import shutil
    copy = tmp_path / "copy"
    shutil.copytree(project.folder, copy)
    p = Project.open(copy)
    p.update("map", {"half_km": 30.0, "res_m": 50.0})
    with pytest.raises(ProjectError, match="2.2 million cells.*Use cells of 60 m or more"):
        p.run("map", log=quiet)


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
    assert st["history"][0]["new_findings"] == [] and "0 new findings" in st["message"]     # the summary line is not news
    late = watch.check_once(p, log=quiet, now=datetime(2026, 11, 9, 6, 0))
    assert late["state"] == "stale" and "8.3 days old" in late["message"]
    assert json.loads((copy / "intake" / "status.json").read_text())["state"] == "stale"
    p.update("data", {"folder": str(tmp_path / "gone")})
    assert watch.check_once(p, log=quiet)["state"] == "error"


def test_intake_news_is_what_changed_not_what_was_recounted():
    from bohemian_wind import DISCLAIMER
    before = ["Data: 8,352 ten-minute records from 01 Sep 2026 00:10 to 29 Oct 2026 00:00; 0 of 8,352 expected timestamps missing.",
              "RH 9 m (Ch17): output at its zero value in 2,390 records (16 Jul 2025 to 14 Feb 2026).",
              "No data from 16 Nov 2025 to 20 Nov 2025 (4.1 days).", DISCLAIMER]
    after = ["Data: 8,496 ten-minute records from 01 Sep 2026 00:10 to 30 Oct 2026 00:00; 0 of 8,496 expected timestamps missing.",
             "RH 9 m (Ch17): output at its zero value in 2,534 records (16 Jul 2025 to 15 Feb 2026).",
             "No data from 16 Nov 2025 to 20 Nov 2025 (4.1 days).", "No data from 02 Mar 2026 to 03 Mar 2026 (1.2 days).",
             "V4 WS 40 m: dead or fixed for 20 hours.", DISCLAIMER]
    assert watch.news(before, after) == ["No data from 02 Mar 2026 to 03 Mar 2026 (1.2 days).", "V4 WS 40 m: dead or fixed for 20 hours."]
    assert watch.news([], after) == after[1:5]                          # a first run: everything but the summary and the disclaimer
    assert watch.news(after, after) == [] and watch.news(after, before) == []


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
        code, body, _ = _call(url + "api/settings", token=app.token,
                              body={"section": "layout", "values": {"losses": [{"item": "Availability", "pct": 150}]}})
        assert code == 400 and "The loss for 'Availability' is 150 %" in json.loads(body)["error"]      # refused, with the reason
        st = json.loads(_call(url + "api/state", token=app.token)[1])["project"]
        assert len(st["layout"]["losses"]) == 7 and st["buildings_half_km"] == 9.0
        lay = next(s for s in st["steps"] if s["key"] == "layout")                 # the changed number put the layout out of date
        assert lay["status"] == "stale" and lay["why"].startswith("A setting or a file") and lay["redo"] == []
        assert next(s for s in st["steps"] if s["key"] == "pack")["redo"] == ["layout", "environment"]
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
        assert env["status"] == "stale" and env["why"] == "'Layout and energy' was run again after it."      # the layout changed under it
        pack = next(s for s in st["project"]["steps"] if s["key"] == "pack")       # built without the screening, and knows it
        assert pack["status"] == "done" and pack["redo"] == ["environment"] and pack["result"]["environment_out_of_date"]
        assert json.loads(_call(url + "api/project", token=app.token, body={"action": "close"})[1])["project"] is None
    finally:
        server.shutdown()
        server.server_close()


def test_vane_correction_turns_the_wind_and_sensor_settings_reach_the_reader(project, tmp_path):
    import shutil
    copy = tmp_path / "copy"
    shutil.copytree(project.folder, copy)
    p = Project.open(copy)
    before = p.result("layout")["prevailing_deg"]
    p.update("data", {"direction_offset_deg": -40})
    assert p.vane_offset()[0] == -40.0
    p.run("map", log=quiet)
    lay = p.run("layout", log=quiet)
    assert lay["prevailing_deg"] == pytest.approx((before - 40.0) % 360.0, abs=0.5)
    assert any("turned by -40 degrees" in n for n in lay["notes"]) and any("turned by -40" in n for n in p.result("map")["notes"])
    # sensor settings by port: cleaned, replaced as a whole, and written into the file the reader is given
    p.update("data", {"channels": {"a4": {"height_m": 78, "direction_offset_deg": -28}, "A6": {"height_m": None}, "": {"height_m": 3}}})
    assert p.s["data"]["channels"] == {"A4": {"height_m": 78.0, "direction_offset_deg": -28.0}}
    p.run("data", log=quiet)
    sent = json.loads((copy / "runs" / "reader_settings.json").read_text())
    assert sent["channels"] == {"A4": {"height_m": 78.0, "direction_offset_deg": -28.0}} and sent["latitude"] == -5.0
    p.update("data", {"channels": {}})
    assert p.s["data"]["channels"] == {}
    with pytest.raises(ProjectError):
        p.update("data", {"channels": {"A4": {"height_m": 999}}})


def test_the_vane_is_checked_against_the_reanalysis_when_the_reference_is_downloaded(tmp_path, monkeypatch):
    from bohemian_wind import longterm
    p = demo.create(tmp_path / "Demo Ridge", today=TODAY)
    p.run("data", log=quiet)
    clim = p.climate()
    dd = clim["direction_days"]
    days = [date.fromisoformat(dd["first"]) + timedelta(days=i) for i in range(len(dd["deg"]))]
    # an invented reanalysis that blows 170 degrees away from what the mast's vane reads
    measured = tmp_path / "measured.json"
    measured.write_text(json.dumps({"properties": {"parameter": {
        "WS50M": {f"{d:%Y%m%d}": 6.0 for d in days},
        "WD50M": {f"{d:%Y%m%d}": float((v - 170) % 360) if v is not None else -999.0 for d, v in zip(days, dd["deg"])}}}}))
    asked = {}

    def fake_fetch(lat, lon, dest, period=None, **kw):
        asked["period"] = period
        return {"monthly": str(p.folder / "longterm" / "demo_reference_monthly.json"),
                "daily": str(p.folder / "longterm" / "demo_reference_daily.json"), "measured": str(measured)}

    monkeypatch.setattr(longterm, "fetch_power", fake_fetch)
    p.update("longterm", {"source": "power"})
    lines = []
    res = p.run("longterm", log=lines.append)
    assert asked["period"] == (dd["first"], f"{days[-1]:%Y-%m-%d}")
    v = res["vane_check"]
    assert abs(abs(v["median_deg"]) - 170) < 3 and v["days"] >= 20
    assert any("north mark read the wrong way round" in c for c in res["cautions"])
    assert any("north mark read the wrong way round" in w for w in p.warnings(clim))      # it reaches the map and the layout
    assert any(ln.startswith("Vane north check: the direction at") for ln in lines)
    # the vane correction entered in the project is part of what is checked; until step 2 runs again, the old
    # verdict is not passed off as a verdict on the new correction
    p.update("data", {"direction_offset_deg": -170})
    stale = p.warnings(clim)
    assert not any("wrong way round" in w for w in stale) and any("run step 2 again" in w for w in stale)
    res = p.run("longterm", log=quiet)
    assert p.warnings(clim) == []
    assert abs(res["vane_check"]["median_deg"]) < 3 and not res["vane_check"]["caution"] and not res["cautions"]
    # reference files of one's own: no directions to compare, no check
    p.update("longterm", {"source": "file"})
    assert p.run("longterm", log=quiet)["vane_check"] is None


def test_air_density_of_a_mast_without_thermometer_comes_from_the_reference(tmp_path):
    from bohemian_wind.energy import turbine
    p = demo.create(tmp_path / "Demo Ridge", today=TODAY)
    p.run("data", log=quiet)
    hand_off = p.folder / p.result("data")["climate"]
    c = json.loads(hand_off.read_text("utf-8"))
    measured = c["density"]["mean"]
    c["density"] = None                                    # as for a mast with no thermometer and no barometer
    hand_off.write_text(json.dumps(c), encoding="utf-8")
    ref = p.folder / "longterm" / "demo_reference_monthly.json"
    j = json.loads(ref.read_text("utf-8"))
    months = [k for k in j["properties"]["parameter"]["WS50M"] if k[4:] != "13"]
    j["properties"]["parameter"]["T2M"] = {k: 21.0 for k in months}      # a warm cell 240 m below the site (invented)
    j["properties"]["parameter"]["PS"] = {k: 86.0 for k in months}
    j["geometry"]["coordinates"][2] = 1400.0
    ref.write_text(json.dumps(j), encoding="utf-8")
    air = p.run("longterm", log=quiet)["reference_air"]
    assert air["t2m_c"] == 21.0 and air["ps_kpa"] == 86.0 and air["elevation_m"] == 1400.0 and air["months"] == 240
    p.run("map", log=quiet)
    lay = p.run("layout", log=quiet)
    hub = lay["turbine"]["hub_m"]
    note = next(n for n in lay["notes"] if n.startswith("Air density at hub height"))
    assert "not measured: long-term temperature and pressure of the reanalysis (NASA POWER, MERRA-2)" in note
    assert lay["rho_hub"] == pytest.approx(turbine.reference_density(p.site()["elevation_m"], hub, air), rel=1e-9)
    isa = turbine.air_density(p.site()["elevation_m"], hub)[0]
    assert 0.9 < lay["rho_hub"] < isa and abs(lay["rho_hub"] / measured - 1) < 0.06
    # a reference with wind speeds only: the standard atmosphere, and the note says what is wrong with it
    for name in ("T2M", "PS"):
        del j["properties"]["parameter"][name]
    ref.write_text(json.dumps(j), encoding="utf-8")
    assert p.run("longterm", log=quiet)["reference_air"] is None
    p.run("map", log=quiet)                                                        # the map rests on the long-term run too
    again = p.run("layout", log=quiet)
    assert again["rho_hub"] == pytest.approx(isa) and "too dense for a warm climate" in " ".join(again["notes"])
    assert again["gross_gwh"] > lay["gross_gwh"]                                   # denser air, more energy


def test_a_survey_of_many_homes_stays_out_of_the_project_file(tmp_path, monkeypatch):
    from bohemian_wind import maps, project
    monkeypatch.setattr(project, "LISTED_HOMES", 40)
    p = demo.create(tmp_path / "Demo Ridge", today=TODAY)
    for step in ("data", "longterm", "map"):
        p.run(step, log=quiet)
    site = p.site()
    rng = np.random.default_rng(8)
    homes = tmp_path / "survey.csv"                           # 130 buildings scattered over 7 km (invented)
    homes.write_text("name,lat,lon\n" + "".join(
        f"B{i:03d},{site['lat'] + dy:.6f},{site['lon'] + dx:.6f}\n" for i, (dx, dy) in enumerate(rng.uniform(-0.032, 0.032, (130, 2)))),
        encoding="utf-8")
    p.attach("receptors", homes)
    p.run("layout", log=quiet)
    lines = []
    env = p.run("environment", log=lines.append)
    nz, fl = env["noise"], env["flicker"]
    assert nz["n_receptors"] == fl["n_receptors"] == 130 and len(nz["receptors"]) == len(fl["receptors"]) == 40
    assert nz["receptors_listed"] == 40 and nz["receptors"][0]["level_dba"] == nz["highest_dba"]
    assert [r["level_dba"] for r in nz["receptors"]] == sorted((r["level_dba"] for r in nz["receptors"]), reverse=True)
    assert any("keeps the 40 most affected of the 130 homes" in ln for ln in lines)
    for name in ("noise_at_homes.csv", "flicker_at_homes.csv"):                    # every home is in the tables
        assert len((p.folder / "environment" / name).read_text("utf-8").strip().splitlines()) == 131
    listed = {r["name"] for r in nz["receptors"]}
    rows = list(csv.DictReader(open(p.folder / "environment" / "noise_at_homes.csv", encoding="utf-8")))
    assert min(float(r["level_dba"]) for r in rows if r["home"] in listed) >= max(float(r["level_dba"]) for r in rows if r["home"] not in listed)
    assert nz["over_limit"] == sum(r["over_limit"] == "yes" for r in rows)          # the counts cover all 130
    assert nz["nearest_home_m"] == min(float(r["distance_m"]) for r in rows)
    pack = p.run("pack", log=quiet)
    folder = p.folder / pack["folder"]
    gis = json.loads((folder / "layout.geojson").read_text("utf-8"))
    assert sum(f["properties"]["kind"] == "home" for f in gis["features"]) == 40
    page = next(folder.glob("*.html")).read_text("utf-8")
    assert "130 homes assessed" in page and "The 25 highest of 130 homes are shown" in page
    assert "the 40 homes with the highest noise levels (of 130 assessed)" in (page + (folder / "README.txt").read_text("utf-8") if (folder / "README.txt").is_file() else page)
    # markers: full size for a few homes, dots for a survey
    assert maps._marker(120, 5.5, 1.1) == (5.5, 1.0, 1.0)
    small = maps._marker(40000, 5.5, 1.1)
    assert small[0] == 1.1 and small[1] == 0.0 and small[2] < 1.0 and 1.1 < maps._marker(1200, 5.5, 1.1)[0] < 5.5
