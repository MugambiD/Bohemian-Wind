"""0.3.3-dev: the expected case of shadow flicker and its budget, quiet operating modes, cost of energy, the
provenance record, the connector for AI assistants and the validation set. Invented data only."""
import io
import json

import numpy as np
import pytest

from bohemian_wind import demo, mcp, provenance, terrain, validation
from bohemian_wind.energy import cost
from bohemian_wind.environment import flicker, modes, noise
from bohemian_wind.project import STEPS, Project, ProjectError


def test_every_validation_case_passes_and_the_published_results_are_this_versions():
    from pathlib import Path  # noqa: PLC0415
    import bohemian_wind  # noqa: PLC0415
    rows = validation.cases()
    assert len(rows) >= 11 and all(r["passed"] for r in rows), [r for r in rows if not r["passed"]]
    kept = Path(__file__).resolve().parents[1] / "docs" / "validation" / f"{bohemian_wind.__version__}.json"
    assert kept.is_file(), "run: bohemian-wind validate --write docs/validation"
    on_file = {(c["case"], c["quantity"]): c for c in json.loads(kept.read_text(encoding="utf-8"))["cases"]}
    for r in rows:                                                # the same cases, the same figures within their tolerance
        c = on_file[(r["case"], r["quantity"])]
        assert c["passed"] and c["got"] == pytest.approx(r["got"], abs=max(r["tolerance"], 1e-4))


def test_the_expected_case_follows_the_wind_rose_the_sun_and_the_running_time():
    g = terrain.from_function(lambda x, y: 0.0 * x, 0.0, 30.0, half_km=3, res=100)
    turb = [{"id": "T1", "x": 0.0, "y": 0.0}]
    homes = [{"name": "west", "x": -600.0, "y": 0.0}, {"name": "east", "x": 600.0, "y": 0.0}]
    cen = np.arange(0.0, 360.0, 30.0)

    def run(freq, **more):
        return flicker.at_receptors(g, turb, homes, 100.0, 120.0, 2027, step_min=5, expected={"centres": cen, "freq": freq, **more.pop("ex", {})}, **more)

    plain = flicker.at_receptors(g, turb, homes, 100.0, 120.0, 2027, step_min=5)
    assert "expected" not in plain and "expected_hours_per_year" not in plain["receptors"][0]       # asked for, or not there
    facing, edge, even = run(np.eye(12)[3]), run(np.eye(12)[0]), run(np.ones(12))
    worst = facing["receptors"][0]["hours_per_year"]
    assert worst == plain["receptors"][0]["hours_per_year"]                                          # the worst case is untouched
    f = facing["receptors"][0]["expected_hours_per_year"]
    assert 0.9 * worst < f <= worst and edge["receptors"][0]["expected_hours_per_year"] == 0.0
    assert 0.3 * f < even["receptors"][0]["expected_hours_per_year"] < 0.9 * f
    assert not facing["expected"]["sunshine_entered"] and "sun taken to shine all day" in facing["expected"]["method"]
    part = run(np.eye(12)[3], ex={"sunshine": [0.5] * 12, "run": 0.8})
    assert part["receptors"][0]["expected_hours_per_year"] == pytest.approx(0.4 * f, abs=0.15) and part["expected"]["sunshine_entered"]
    assert part["expected"]["stop_whenever_shading_pct"] < facing["expected"]["stop_whenever_shading_pct"] <= plain["shutdown_upper_bound_pct"]
    # a budget: both homes are brought down to it, and the turbine stops for what they have over it
    b = run(np.eye(12)[3], budget_hours=10.0)
    assert b["budget"]["homes_over"] == 2 and all(r["over_budget"] for r in b["receptors"])
    assert b["budget"]["per_turbine_stop_hours"]["T1"] == pytest.approx(2 * f * (1 - 10.0 / f), abs=0.3)
    assert b["budget"]["shutdown_loss_pct"] == pytest.approx(100 * 2 * (f - 10.0) / 8760.0, abs=0.01)
    roomy = run(np.eye(12)[3], budget_hours=500.0)
    assert roomy["budget"]["homes_over"] == 0 and roomy["budget"]["shutdown_loss_pct"] == 0.0


def test_quiet_modes_bring_the_homes_within_the_limit_at_the_least_energy():
    g = terrain.from_function(lambda x, y: 0.0 * x, 0.0, 30.0, half_km=3, res=100)
    T = [{"id": "T1", "x": 0.0, "y": 0.0}, {"id": "T2", "x": 600.0, "y": 0.0}, {"id": "T3", "x": 1200.0, "y": 0.0}]
    H = [{"name": "near T1", "x": -400.0, "y": 0.0}, {"name": "far", "x": 2500.0, "y": 0.0}]
    prop = noise.propagation(g, T, H, 100.0)
    whole = noise.at_receptors(g, T, H, 100.0, 106.0)
    by = {r["name"]: r["level_dba"] for r in whole["receptors"]}
    assert np.round(10 * np.log10((10 ** ((prop + 106.0) / 10)).sum(axis=1)), 1).tolist() == [by["near T1"], by["far"]]
    table = modes.read_modes([{"name": "M2", "sound_power_dba": 101, "energy_pct": 91}, {"name": "M1", "sound_power_dba": 104, "energy_pct": 97},
                              {"name": "M3", "sound_power_dba": 98, "energy_pct": 80}], 106.0)
    assert [m["name"] for m in table] == ["full", "M1", "M2", "M3"]
    limit = by["near T1"] - 2.0
    q = modes.strategy(prop, limit, table)
    assert q["within_limit"] and q["homes_over_before"] == 1 and q["homes_over_after"] == 0
    assert q["modes"][0] != "full" and q["modes"][2] == "full"                        # the turbine beside the home, not the far one
    again = noise.at_receptors(g, T, H, 100.0, np.array(q["sound_power_dba"]), limit=limit)
    assert again["over_limit"] == 0 and again["highest_dba"] == q["highest_dba_after"]
    louder = list(q["sound_power_dba"])                                               # nothing was turned down more than needed
    louder[0] = table[[m["name"] for m in table].index(q["modes"][0]) - 1]["sound_power_dba"]
    assert noise.at_receptors(g, T, H, 100.0, np.array(louder), limit=limit)["over_limit"] == 1
    hopeless = modes.strategy(prop, by["near T1"] - 30.0, table)
    assert not hopeless["within_limit"] and hopeless["homes_over_after"] >= 1
    assert modes.strategy(prop, by["near T1"] + 1.0, table)["energy_loss_pct"] == 0.0
    for bad in ([{"name": "x", "sound_power_dba": 107, "energy_pct": 90}], [{"name": "x", "sound_power_dba": 100, "energy_pct": 120}], [{"name": "x"}]):
        with pytest.raises(modes.ModesError):
            modes.read_modes(bad, 106.0)


def test_cost_of_energy_from_the_costs_entered():
    assert cost.read({}) is None and cost.read({"capex_per_mw": ""}) is None
    c = cost.read({"capex_per_mw": 1_400_000, "opex_per_mw_year": 40_000, "link_per_m": 400})
    assert (c.years, c.discount_pct, c.currency) == (25.0, 8.0, "USD")
    assert cost.Costs(1.0, years=20, discount_pct=0).recovery == pytest.approx(0.05)
    line = ([0.0, 500.0, 1500.0], [0.0, 0.0, 0.0])
    assert cost.tree_length(*line) == 1500.0 and cost.tree_length(*line, root=(0.0, -200.0)) == 1700.0
    near, far = cost.of_layout(c, 13.5, *line, 40_000.0), cost.of_layout(c, 13.5, [0.0, 500.0, 5000.0], [0.0] * 3, 40_000.0)
    assert far["cost_of_energy_per_mwh"] > near["cost_of_energy_per_mwh"] and far["link_m"] == 5000.0
    with pytest.raises(cost.CostError):
        cost.read({"capex_per_mw": "many"})


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    p = demo.open_or_create(tmp_path_factory.mktemp("p033") / "demo")
    for step in STEPS[:5]:
        p.run(step, log=lambda m: None)
    return p


def test_a_project_made_before_the_new_settings_is_not_out_of_date_and_the_new_figures_come_when_asked(project):
    p = project
    assert all(p.status(s) == "done" for s in STEPS[:5])
    lay, env = p.result("layout"), p.result("environment")
    assert "cost" not in lay and lay["objective"] == "energy" and "quiet_modes" not in env["noise"] and "budget" not in env["flicker"]
    ex = env["flicker"]["expected"]                                   # the expected case needs nothing entered
    assert 0 < ex["highest_hours"] < env["flicker"]["highest_hours"] and not ex["sunshine_entered"]
    p.update("layout", {"objective": "cost", "costs": {"capex_per_mw": 1_400_000, "opex_per_mw_year": 40_000, "link_per_m": 400}})
    p.update("environment", {"sunshine_share": 0.6, "flicker_budget_hours": 8, "noise_limit_dba": env["noise"]["highest_dba"] - 2.0,
                             "noise_modes": [{"name": "Mode 1", "sound_power_dba": 102, "energy_pct": 96}, {"name": "Mode 2", "sound_power_dba": 99, "energy_pct": 88}]})
    assert p.status("layout") != "done" and p.status("environment") != "done"
    said = []
    p.run("layout", log=said.append)
    p.run("environment", log=said.append)
    lay, env = p.result("layout"), p.result("environment")
    assert lay["refinement"]["objective"] == "cost" and lay["cost"]["cost_of_energy_per_mwh"] > 0 and lay["cost"]["link_m"] > 0
    assert any("Cost of energy" in m and "not a financial model" in m for m in said)
    fl, qm = env["flicker"], env["noise"]["quiet_modes"]
    assert fl["expected"]["sunshine_entered"] and fl["expected"]["highest_hours"] < ex["highest_hours"] * 0.7
    assert fl["budget"]["hours"] == 8 and 0 <= fl["budget"]["shutdown_loss_pct"] < fl["shutdown_upper_bound_pct"]
    assert qm["homes_over_before"] >= 1 and qm["turbines_in_quiet_mode"] >= 1 and set(qm["by_turbine"]) <= {q["id"] for q in lay["positions"]}
    head = (p.folder / "environment" / "flicker_at_homes.csv").read_text(encoding="utf-8").splitlines()[0]
    assert head.endswith("hours_per_year_expected,over_budget")
    p.update("layout", {"objective": "cost", "costs": {"capex_per_mw": None}})
    with pytest.raises(ProjectError, match="enter the costs first"):
        p.run("layout", log=lambda m: None)
    p.update("layout", {"objective": "energy"})


def test_the_provenance_record_and_the_assistant_connector_tell_the_same_story(project):
    p = Project.open(project.folder)
    on_disk = json.loads((p.folder / provenance.NAME).read_text(encoding="utf-8"))
    assert on_disk["format"] == provenance.FORMAT and on_disk["program"]["version"] and "bankable" in on_disk["notice"]
    rec = provenance.record(p)
    assert set(rec["steps"]) == set(STEPS) and rec["steps"]["data"]["status"] == "done"
    assert rec["steps"]["layout"]["input_files"]["boundary_file"]["mark"] and rec["steps"]["longterm"]["inputs_mark"]
    assert any("ISO 9613-2" in m for m in rec["steps"]["environment"]["methods"].values())

    def ask(*messages):
        out = io.StringIO()
        import sys  # noqa: PLC0415
        keep = sys.stdout
        try:
            mcp.main(io.StringIO("".join(json.dumps(m) + "\n" for m in messages)), out)
        finally:
            sys.stdout = keep
        return [json.loads(line) for line in out.getvalue().splitlines()]

    def tool(i, name, **args):
        return {"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": name, "arguments": args}}

    a = ask({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"}, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            tool(3, "project_status", folder=str(p.folder)), tool(4, "get_results", folder=str(p.folder), step="layout"),
            tool(5, "get_results", folder=str(p.folder), step="nope"), tool(6, "get_provenance", folder=str(p.folder)),
            {"jsonrpc": "2.0", "id": 7, "method": "no/such"}, "not a message")
    assert [m.get("id") for m in a] == [1, 2, 3, 4, 5, 6, 7, None]                      # the notification gets no answer
    assert a[0]["result"]["protocolVersion"] == "2025-06-18" and "bankable" in a[0]["result"]["instructions"]
    assert {t["name"] for t in a[1]["result"]["tools"]} == {"project_status", "run_steps", "get_results", "get_provenance", "create_demo", "list_layouts", "save_layout", "compare_layouts"}
    status = json.loads(a[2]["result"]["content"][0]["text"])
    assert [s["step"] for s in status["steps"]] == list(STEPS) and "bankable" in status["notice"]
    layout = json.loads(a[3]["result"]["content"][0]["text"])
    assert layout["result"]["p50_gwh"] == pytest.approx(p.result("layout")["p50_gwh"], abs=1e-3)      # the engine's figure, as stored
    assert a[4]["result"]["isError"] and "Unknown step" in a[4]["result"]["content"][0]["text"]
    assert json.loads(a[5]["result"]["content"][0]["text"])["format"] == provenance.FORMAT
    assert a[6]["error"]["code"] == -32601 and a[7]["error"]["code"] == -32600


def test_two_layouts_are_kept_changed_and_compared(project):
    """0.3.5-dev: keep layout A, copy it, move three turbines, run, keep as B, compare."""
    from bohemian_wind import layouts  # noqa: PLC0415
    p = Project.open(project.folder)
    layouts.design_again(p)
    for step in ("layout", "environment"):
        p.run(step, log=lambda m: None)
    a = layouts.save(p, "Layout A")
    assert a["screening"] and a["figures"]["n_placed"] == len(a["positions"]) and not a["basis"]["given"]
    with pytest.raises(layouts.LayoutError, match="No layout is kept"):
        layouts.compare(p, "Layout A", "Layout B")
    rel = layouts.duplicate(p, "Layout A", "Layout B")
    assert rel == "layouts/Layout-B.geojson"
    with pytest.raises(layouts.LayoutError, match="exist already"):
        layouts.duplicate(p, "Layout A", "Layout B")
    ids = [q["id"] for q in a["positions"]]
    for tid, east, north in ((ids[1], 0.0, 400.0), (ids[4], 300.0, -200.0), (ids[6], -250.0, 0.0)):
        layouts.move(p, "Layout B", tid, east, north)
    with pytest.raises(layouts.LayoutError, match="has no turbine"):
        layouts.move(p, "Layout B", "T99", 10.0)
    layouts.use(p, "Layout B")
    assert p.status("layout") != "done"                                       # the positions changed: step 4 is to run again
    p.run("layout", log=lambda m: None)
    only_layout = layouts.save(p, "Layout B")
    assert only_layout["screening"] is None and only_layout["basis"]["given"]  # step 5 not run for it yet
    assert any("Only 'Layout A' was kept with its noise and flicker screening" in w for w in layouts.compare(p, "Layout A", "Layout B")["warnings"])
    p.run("environment", log=lambda m: None)
    layouts.save(p, "Layout B")
    c = layouts.write_comparison(p, "Layout A", "Layout B")
    assert not c["warnings"], c["warnings"]                                    # the same wind, turbine, wake model and losses
    moved = {t["id"]: t for t in c["turbines"] if t.get("moved")}
    assert set(moved) == {ids[1], ids[4], ids[6]}
    assert moved[ids[1]]["north_m"] == pytest.approx(400, abs=6) and moved[ids[6]]["east_m"] == pytest.approx(-250, abs=6)
    net = c["rows"]["Net energy P50, GWh a year"]
    b = layouts.kept(p, "Layout B")
    assert net["a"] == a["figures"]["p50_gwh"] and net["b"] == b["figures"]["p50_gwh"]
    assert net["change"] == pytest.approx(net["b"] - net["a"]) and net["change_pct"] == pytest.approx(100 * (net["b"] / net["a"] - 1))
    assert sum(t["p50_mwh"]["change"] for t in c["turbines"]) / 1000.0 == pytest.approx(net["change"], abs=1e-6)   # the turbines add up
    assert "Homes over the noise level" in c["rows"] and c["rows"]["Turbines"]["change"] == 0
    text = (p.folder / c["files"][0]).read_text(encoding="utf-8")
    assert "[COMPLIANCE]" in text and "bankable" in text and ids[4] in text and "Change, %" in text
    assert (p.folder / c["files"][1]).is_file() and [x["name"] for x in layouts.listing(p)] == ["Layout A", "Layout B"]
    # another wake model for one of them: the comparison says the two no longer stand on the same footing
    p.update("layout", {"wake": "jensen075"})
    p.run("layout", log=lambda m: None)
    layouts.save(p, "Layout B jensen")
    assert any("wake model" in w for w in layouts.compare(p, "Layout A", "Layout B jensen")["warnings"])
    p.update("layout", {"wake": "niayifar"})
    layouts.design_again(p)


def test_the_wake_benchmark_is_published_with_every_turbine_and_model():
    from pathlib import Path  # noqa: PLC0415
    import bohemian_wind  # noqa: PLC0415
    b = validation.benchmark()
    assert len(b["cases"]) == 4 * 2 * 3 and {c["wake_model"] for c in b["cases"]} == {"niayifar", "jensen075", "turbopark"}
    line = next(c for c in b["cases"] if c["layout"].startswith("two in line") and c["wind"].startswith("wind from the west") and c["wake_model"] == "jensen075")
    assert line["wake_loss_pct"][0] == 0.0 and 5.0 < line["wake_loss_pct"][1] < 30.0
    kept = Path(__file__).resolve().parents[1] / "docs" / "validation" / f"wake-benchmark-{bohemian_wind.__version__}.json"
    assert kept.is_file(), "run: bohemian-wind validate --write docs/validation"
    on_file = json.loads(kept.read_text(encoding="utf-8"))["cases"]
    for mine, theirs in zip(b["cases"], on_file):
        assert mine["layout"] == theirs["layout"] and mine["wake_loss_pct"] == pytest.approx(theirs["wake_loss_pct"], abs=0.01)
