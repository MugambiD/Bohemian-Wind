"""What keeps the results of a project in step with its settings: a result goes out of date when a setting or a
file it was made from changes, nothing new is built on an out-of-date result, every run and every pack gets a
folder of its own, and homes the screening cannot reach are counted and listed instead of dropped.
The site is the invented demo project. Run: python -m pytest tests/test_out_of_date.py -q"""
import csv
import json
import shutil
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytest.importorskip("py_wake")

from bohemian_wind import demo  # noqa: E402
from bohemian_wind.energy import EnergyError, budget  # noqa: E402
from bohemian_wind.environment import flicker, noise  # noqa: E402
from bohemian_wind.folders import fresh_folder  # noqa: E402
from bohemian_wind.pipeline import new_run_folder  # noqa: E402
from bohemian_wind.project import STEPS, Project, ProjectError  # noqa: E402

TODAY = date(2026, 11, 18)
quiet = lambda m: None  # noqa: E731
CHANGED = "A setting or a file it works from was changed after it ran."


@pytest.fixture(scope="module")
def finished(tmp_path_factory):
    p = demo.create(tmp_path_factory.mktemp("demo") / "Demo Ridge", today=TODAY)
    for step in STEPS:
        p.run(step, log=quiet)
    return p


@pytest.fixture
def project(finished, tmp_path):
    """A copy of the finished demo project that a test may change."""
    shutil.copytree(finished.folder, tmp_path / "copy")
    return Project.open(tmp_path / "copy")


def statuses(p) -> dict:
    return {s: p.status(s) for s in STEPS}


def all_done(p) -> bool:
    return set(statuses(p).values()) == {"done"}


# ---------- out of date ----------

def test_a_changed_setting_puts_its_step_and_the_later_ones_out_of_date(project):
    p = project
    name, elevation = p.s["name"], p.s["site"]["elevation_m"]
    assert all_done(p) and all(s["why"] is None and s["redo"] == [] for s in p.state()["steps"])
    # the noise level to screen against: the screening no longer belongs to it, and neither does the pack built from it
    p.update("environment", {"noise_limit_dba": 35})
    assert statuses(p) == {"data": "done", "longterm": "done", "map": "done", "layout": "done", "environment": "stale", "pack": "stale"}
    assert p.stale_reason("environment") == CHANGED
    assert p.stale_reason("pack") == "The noise and shadow flicker screening changed after the pack was built."
    assert p.redo("pack") == ["environment"] and p.redo("environment") == []
    assert statuses(Project.open(p.folder)) == statuses(p)                  # read from the project file, not kept in memory
    # set back, the results belong to the settings again: typed as text, 45 is still 45
    p.update("environment", {"noise_limit_dba": "45"})
    assert all_done(p)
    # a setting no result is made from
    p.update("data", {"watch_minutes": 15, "stale_hours": 12})
    assert all_done(p)
    # a setting a later version might bring, empty or switched off: as good as not there, until it is switched on
    for off in (None, "", False, [], {}):
        p.s["layout"]["an_option_yet_to_come"] = off
        p.s["layout"]["rules"]["a_limit_yet_to_come"] = off
        assert all_done(p)
    p.s["layout"]["an_option_yet_to_come"] = True
    assert p.status("layout") == "stale"
    p.s["layout"]["an_option_yet_to_come"], p.s["layout"]["rules"]["a_limit_yet_to_come"] = False, 0
    assert p.status("layout") == "stale"                                    # a limit of nought is a limit
    del p.s["layout"]["rules"]["a_limit_yet_to_come"]
    assert all_done(p)
    # the shear exponent carries the wind to hub height from the wind map on
    p.update("data", {"shear_alpha": 0.2})
    assert statuses(p) == {"data": "done", "longterm": "done", "map": "stale", "layout": "stale", "environment": "stale", "pack": "stale"}
    assert p.stale_reason("map") == CHANGED and p.stale_reason("layout") == "It rests on 'Wind map', which is out of date."
    assert p.redo("pack") == ["map", "layout", "environment"] and p.redo("map") == []
    state = {s["key"]: s for s in p.state()["steps"]}
    assert state["layout"]["why"] == "It rests on 'Wind map', which is out of date." and state["environment"]["redo"] == ["map", "layout"]
    # nothing new is built on an out-of-date result, whichever way the step is started
    with pytest.raises(ProjectError, match=r"'Wind map' \(step 3\) is out of date. A setting or a file .* Run it again, then this step"):
        p.run("layout", log=quiet)
    with pytest.raises(ProjectError, match=r"'Layout and energy' \(step 4\) is out of date"):
        p.run_pack(log=quiet)
    with pytest.raises(ProjectError, match=r"'Layout and energy' \(step 4\) is out of date"):
        p.run("environment", log=quiet)
    p.update("data", {"shear_alpha": None})
    assert all_done(p)
    # the mast's elevation sets the air density of step 1, and everything rests on step 1
    p.update("site", {"elevation_m": 2100})
    assert set(statuses(p).values()) == {"stale"} and p.stale_reason("data") == CHANGED
    assert p.stale_reason("longterm") == "It rests on 'Mast data', which is out of date."
    with pytest.raises(ProjectError, match=r"'Mast data' \(step 1\) is out of date"):
        p.run("longterm", log=quiet)
    p.update("site", {"elevation_m": elevation})
    assert all_done(p)
    # the project's name is printed on the pack
    p.update("name", {"name": "Demo Ridge, east"})
    assert statuses(p)["pack"] == "stale" and statuses(p)["environment"] == "done" and p.stale_reason("pack") == CHANGED
    p.update("name", {"name": name})
    assert all_done(p)


def test_a_reference_file_or_a_homes_file_that_changes_counts_as_a_changed_setting(project):
    p = project
    homes = p.folder / p.s["layout"]["receptors_file"]
    original = homes.read_bytes()
    j = json.loads(original)
    j["features"] = j["features"][:-1]                                    # one home fewer, written straight into the project
    homes.write_text(json.dumps(j), encoding="utf-8")
    assert statuses(p) == {"data": "done", "longterm": "done", "map": "done", "layout": "stale", "environment": "stale", "pack": "stale"}
    assert p.stale_reason("layout") == CHANGED and p.stale_reason("environment") == "It rests on 'Layout and energy', which is out of date."
    homes.write_bytes(original)                                           # the same homes again: nothing has changed
    assert all_done(p)
    keep = p.folder.parent / "homes.geojson"
    keep.write_bytes(original)
    p.attach("receptors", keep)                                           # choosing the same file again changes nothing either
    assert all_done(p)
    keep.write_text(json.dumps(j), encoding="utf-8")
    p.attach("receptors", keep)
    assert p.status("layout") == "stale"
    keep.write_bytes(original)
    p.attach("receptors", keep)
    assert all_done(p)
    # the long-term reference read from a file
    ref = p.folder / p.s["longterm"]["monthly_file"]
    kept = ref.read_bytes()
    r = json.loads(kept)
    months = r["properties"]["parameter"]["WS50M"]
    first = next(iter(months))
    months[first] = months[first] + 0.5
    ref.write_text(json.dumps(r) + "\n", encoding="utf-8")
    assert statuses(p) == {"data": "done", "longterm": "stale", "map": "stale", "layout": "stale", "environment": "stale", "pack": "stale"}
    with pytest.raises(ProjectError, match="The long-term correction is out of date. A setting or a file .* Run step 2"):
        p.run("map", log=quiet)                                           # the map needs step 1 only, but uses step 2 when it is there
    ref.write_bytes(kept)
    assert all_done(p)
    # a file that is named but not used does not count: with the reference downloaded, the files are not read
    p.s["longterm"]["source"] = "power"
    marked = p.inputs("longterm")
    ref.write_text(json.dumps(r), encoding="utf-8")
    assert p.inputs("longterm") == marked
    p.s["map"]["gwa_dir"] = "atlas files"                                 # nor does the atlas folder, with the own flow model
    assert p.inputs("map") == p.result("map")["inputs"]


def test_a_file_rewritten_in_the_same_instant_with_the_same_size_is_still_seen_to_change(tmp_path):
    from bohemian_wind.project import _file_mark
    f = tmp_path / "homes.csv"
    seen = set()
    for text in ("name,lat,lon\nA,1.0,2.0\n", "name,lat,lon\nB,1.0,2.0\n", "name,lat,lon\nC,1.0,2.0\n"):
        f.write_text(text, encoding="utf-8")          # three versions of one size, as fast as the disk will take them
        seen.add(_file_mark(f))
    assert len(seen) == 3 and _file_mark(f) == _file_mark(f) and _file_mark(tmp_path / "none.csv") == "" and _file_mark(None) == ""


def test_results_made_before_the_marks_were_kept_stay_as_they_were(project):
    p = project
    for step in STEPS:
        p.result(step).pop("inputs")
    p.result("pack")["based_on"].pop("environment")
    p.save()
    p = Project.open(p.folder)
    p.update("environment", {"noise_limit_dba": 35})
    p.update("layout", {"n_turbines": 4})
    assert all_done(p)                                                    # nothing to compare with: they are run again when the user says
    p.run("pack", log=quiet)
    assert p.result("pack")["inputs"] and "environment" in p.result("pack")["based_on"]


def test_two_runs_of_a_step_never_share_a_stamp(tmp_path):
    p = Project.create(tmp_path / "p", "Example Hill")
    first = p._store("data", {"n": 1})["at"]
    p._store("longterm", {"n": 1})
    second = p._store("data", {"n": 2})["at"]                             # within the same second: it waits for the next
    assert second > first and p.status("longterm") == "stale" and p.stale_reason("longterm") == "'Mast data' was run again after it."


# ---------- a folder of its own ----------

def test_every_result_folder_is_new(tmp_path):
    a, b, c = (fresh_folder(tmp_path / "permitting", "pack_2026-11-19") for _ in range(3))
    assert [x.name for x in (a, b, c)] == ["pack_2026-11-19", "pack_2026-11-19_2", "pack_2026-11-19_3"] and all(x.is_dir() for x in (a, b, c))
    (tmp_path / "permitting" / "pack_2026-11-19_4").write_text("a file in the way")
    assert fresh_folder(tmp_path / "permitting", "pack_2026-11-19").name == "pack_2026-11-19_5"
    runs = [new_run_folder(tmp_path, "Example Hill / mast 1") for _ in range(3)]      # three runs within a second
    assert len({r for r in runs}) == 3 and all(r.is_dir() and r.parent == tmp_path / "runs" for r in runs)
    assert all(r.name.startswith("Example_Hill___mast_1_20") for r in runs)


def test_a_second_pack_on_the_same_day_does_not_write_into_the_first(project):
    p = project
    first = p.folder / p.result("pack")["folder"]
    before = {f.name: f.read_bytes() for f in first.iterdir()}
    assert "map_noise.png" in before and p.result("pack")["with_environment"]
    p.update("environment", {"noise_limit_dba": 35})                      # the screening in the first pack is now out of date
    lines = []
    out = p.run("pack", log=lines.append)
    second = p.folder / out["folder"]
    assert second != first and second.name == first.name + "_2"
    assert not out["with_environment"] and out["environment_out_of_date"]
    assert any("is out of date and is left out of the pack" in ln for ln in lines)
    names = {f.name for f in second.iterdir()}
    assert not names & {"map_noise.png", "map_shadow_flicker.png", "noise_at_homes.csv", "shadow_flicker_at_homes.csv"}
    page = (second / "index.html").read_text(encoding="utf-8")
    assert "Not included: the screening of step 5 is out of date." in page and "Run step 5 again, then build the pack again." in page
    assert {f.name: f.read_bytes() for f in first.iterdir()} == before      # the pack that may have gone out is as it was
    assert p.status("pack") == "done" and p.status("environment") == "stale"      # built without the screening, and says so
    p.run("environment", log=quiet)
    assert p.status("pack") == "stale" and p.redo("pack") == []
    out = p.run("pack", log=quiet)
    third = p.folder / out["folder"]
    assert third.name == first.name + "_3" and out["with_environment"] and not out["environment_out_of_date"]
    assert "above the 35 dB(A) level used here" in " ".join((third / "index.html").read_text(encoding="utf-8").split())
    assert all_done(p)


# ---------- homes the screening cannot reach ----------

def ll(site, x, y):
    from bohemian_wind.terrain import metres_per_degree
    m_lon, m_lat = metres_per_degree(site["lat"])
    return site["lat"] + y / m_lat, site["lon"] + x / m_lon


def points(path, site, rows):
    path.write_text("name,lat,lon\n" + "".join("{},{:.7f},{:.7f}\n".format(n, *ll(site, x, y)) for n, x, y in rows), encoding="utf-8")
    return path


def test_homes_outside_the_terrain_are_counted_listed_and_weighed(project, tmp_path):
    p = project
    site, terrain = p.site(), p.grid()
    mapped = 1000.0 * p.s["map"]["half_km"]                               # the wind map ends 8 km from the mast,
    edge = float(terrain.x[-1])                                           # the terrain prepared for it 10 km
    assert (mapped, edge) == (8000.0, 10000.0)
    homes = [(f.name, *[float(v) for v in terrain.to_xy(*f.coords[0])]) for f in p.features("receptors")]
    assert len(homes) == 40 and all(abs(x) < mapped and abs(y) < mapped for _, x, y in homes)
    # three turbines of the user's own near the eastern edge of the wind map; a home between that edge and the edge of
    # the terrain, one 250 m beyond the terrain, one far away
    p.update("layout", {"rules": {"radius_km": 8.0}})                     # turbines up to 7.9 km out: within the search radius
    p.attach("positions", points(tmp_path / "turbines.csv", site, [("E1", 7800.0, 0.0), ("E2", 7800.0, 900.0), ("E3", 6900.0, 450.0)]))
    extra = [("Fence farm", mapped + 300.0, 0.0), ("Edge farm", edge + 250.0, 0.0), ("Far town", 40000.0, 5000.0)]
    p.attach("receptors", points(tmp_path / "homes.csv", site, homes + extra))
    p.update("environment", {"noise_limit_dba": 25})                      # a level strict enough to be reached 2.5 km away
    lines = []
    lay = p.run("layout", log=lines.append)
    assert lay["receptors_in_area"] == lay["receptor_origin"]["inside"] == 40 and lay["receptor_origin"]["outside"] == 3
    assert "40 homes are on the wind map and 3 beyond its edge." in lines
    rule = next(s["rule"] for s in lay["constraints"]["steps"] if "homes and other receptors" in s["rule"])
    assert rule.endswith("(41)")                                          # the setback counts for the home just beyond the map too
    lines = []
    env = p.run("environment", log=lines.append)
    reach = env["reach_m"]
    assert reach == pytest.approx(noise.reach_m(105.0, 25.0 - noise.UNCERTAINTY_DB, 3), abs=0.5) and reach > flicker.MAX_DISTANCE_ROTORS * 163.0
    assert env["noise"]["n_receptors"] == env["flicker"]["n_receptors"] == 41      # the terrain holds Fence farm: it is assessed
    assert any(r["name"] == "Fence farm" and r["distance_m"] == 500.0 for r in env["noise"]["receptors"])
    na = env["not_assessed"]
    assert (na["n"], na["of"], na["within_reach"], na["csv"]) == (2, 43, 1, "environment/homes_not_assessed.csv")
    assert na["nearest_turbine_m"] == pytest.approx(2450.0, abs=2.0) and 2450.0 < reach
    with open(p.folder / na["csv"], newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert [(r["home"], r["within_reach_of_noise_or_flicker"]) for r in rows] == [("Edge farm", "yes"), ("Far town", "no")]
    assert rows[0]["why_not_assessed"] == "outside the terrain of the wind map" and float(rows[0]["nearest_turbine_m"]) == pytest.approx(2450.0, abs=2.0)
    assert len(env["cautions"]) == 1 and env["cautions"][0].startswith(f"1 home was not assessed, though within {reach / 1000:.1f} km of a turbine")
    assert "Draw the wind map over a wider area (step 3)" in env["cautions"][0]
    assert any("2 of the 43 homes lie outside the terrain of the wind map and were not assessed" in ln for ln in lines)
    out = p.run("pack", log=quiet)
    folder = p.folder / out["folder"]
    page = " ".join((folder / "index.html").read_text(encoding="utf-8").split())
    assert "Not assessed: 2 of the 43 homes lie outside the terrain of the wind map" in page and "The screening is incomplete" in page
    assert "1 of them is near enough for noise over the level used or for shadow flicker." in page
    assert (folder / "homes_not_assessed.csv").read_bytes() == (p.folder / na["csv"]).read_bytes()
    assert any(f["file"].endswith("homes_not_assessed.csv") for f in out["files"])
    # with the far home alone, nothing is within reach: said, with the distance beyond which nothing can happen
    p.attach("receptors", points(tmp_path / "homes2.csv", site, homes + [("Far town", 40000.0, 5000.0)]))
    p.run("layout", log=quiet)
    env = p.run("environment", log=quiet)
    assert env["not_assessed"]["within_reach"] == 0 and env["cautions"] == []
    page = " ".join((p.folder / p.run("pack", log=quiet)["index"]).read_text(encoding="utf-8").split())
    assert f"All of them are more than {reach / 1000:.1f} km from every turbine" in page and "The screening is incomplete" not in page
    # every home on the terrain again: the list of the earlier run does not stay behind
    p.attach("receptors", points(tmp_path / "homes3.csv", site, homes))
    p.run("layout", log=quiet)
    env = p.run("environment", log=quiet)
    assert "not_assessed" not in env and not (p.folder / "environment" / "homes_not_assessed.csv").exists()
    # and when no home is on the terrain, the step says why it has nothing to assess
    p.attach("receptors", points(tmp_path / "homes4.csv", site, extra[1:]))
    p.run("layout", log=quiet)
    with pytest.raises(ProjectError, match="All 2 homes lie outside the terrain of the wind map"):
        p.run("environment", log=quiet)


def test_beyond_the_reach_no_arrangement_of_turbines_reaches_the_level():
    rng = np.random.default_rng(3)
    for lw, limit, n in ((105.0, 43.0, 10), (108.0, 33.0, 40), (100.0, 45.0, 1)):
        d = noise.reach_m(lw, limit, n)
        hub = rng.uniform(60.0, 170.0, 400)
        dz = rng.uniform(-300.0, 300.0, 400)                              # turbine above or below the home
        far = d * rng.uniform(1.0, 3.0, 400)
        hm = rng.uniform(0.0, 400.0, 400)                                 # from a path hugging the ground to one high across a valley
        worst = noise.level(lw, np.hypot(far, hub + dz - noise.RECEIVER_HEIGHT), far, hub, noise.RECEIVER_HEIGHT, hm) + 10 * np.log10(n)
        assert worst.max() <= limit + 1e-6
        # and the distance is not padded: a little inside it, level with the turbines across a valley, the level is reached
        near = noise.level(lw, 0.97 * d, 0.97 * d, noise.RECEIVER_HEIGHT, noise.RECEIVER_HEIGHT, 1e6) + 10 * np.log10(n)
        assert limit < near < limit + 1.0
    assert noise.reach_m(90.0, 95.0, 1) == pytest.approx(1.0, abs=0.01)      # a level no turbine reaches: no distance to speak of


# ---------- losses ----------

def test_losses_are_checked_when_they_are_entered(tmp_path):
    assert budget.loss_factor([]) == 1.0 and budget.losses_table([]) == []      # an empty table is a table with no losses
    assert budget.loss_factor(None) == budget.loss_factor() < 1.0               # nothing given: the defaults
    for bad in (-5, 100, 120, float("nan"), float("inf"), "a lot", None):
        with pytest.raises(EnergyError, match="Availability"):
            budget.loss_factor([{"item": "Availability", "pct": bad}])
    with pytest.raises(EnergyError, match="has no name"):
        budget.loss_factor([{"pct": 3}])
    with pytest.raises(EnergyError, match="has no name"):
        budget.loss_factor(["Availability 3 %"])
    with pytest.raises(EnergyError, match="given as a table"):
        budget.loss_factor({"item": "Availability", "pct": 3})
    assert budget.loss_factor([{"item": "Availability", "pct": "3"}, {"item": "Electrical", "pct": 0}]) == pytest.approx(0.97)
    p = Project.create(tmp_path / "p", "Example Hill")
    table = p.s["layout"]["losses"]
    assert len(table) == 7
    with pytest.raises(ProjectError, match="is -3 %. Give a figure from 0 to less than 100"):
        p.update("layout", {"losses": [{**table[0], "pct": -3}] + table[1:]})
    assert Project.open(p.folder).s["layout"]["losses"] == table          # the refused table was not kept
    p.update("layout", {"losses": [{**table[0], "pct": 4.5}] + table[1:]})
    assert Project.open(p.folder).s["layout"]["losses"][0]["pct"] == 4.5
    p.update("layout", {"losses": []})
    assert Project.open(p.folder).s["layout"]["losses"] == []
    p.update("layout", {"losses": None})
    assert Project.open(p.folder).s["layout"]["losses"] == table          # back to the defaults


def test_a_layout_with_no_losses_keeps_its_gross_energy_less_the_wakes(project):
    p = project
    base = p.result("layout")
    assert base["other_loss_pct"] > 5
    p.update("layout", {"losses": [], "refine": False})
    lay = p.run("layout", log=quiet)
    assert lay["other_loss_pct"] == pytest.approx(0.0, abs=1e-9) and lay["losses"] == []
    assert lay["p50_gwh"] == pytest.approx(lay["gross_gwh"] * (1 - lay["wake_loss_pct"] / 100.0), rel=1e-3)


def test_the_interface_shows_why_and_runs_what_is_out_of_date_first():
    js = (Path(__file__).resolve().parents[1] / "bohemian_wind" / "app" / "static" / "app.js").read_text("utf-8")
    assert "st.why" in js and "st.redo" in js and "data-first" in js           # the reason, and the steps the button runs first
    assert "r.not_assessed" in js and "r.cautions" in js and "environment_out_of_date" in js
    assert "function saveSettings" in js and "refreshStatus()" in js           # a refused value goes back; the status follows a change
