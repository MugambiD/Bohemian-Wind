"""A project: one folder that holds a site's settings and the results of every step.

    <project>/project.json      settings and the latest result of each step
    <project>/runs/             step 1: one folder per data check (report, tables, wind climate)
    <project>/longterm/         step 2: reference data and the long-term factor
    <project>/terrain/          step 3: terrain tiles and the terrain grid
    <project>/maps/             step 3: wind field and wind map
    <project>/constraints/      step 4: exclusion zones, homes and boundary files
    <project>/layout/           step 4: turbine positions and the energy estimate
    <project>/environment/      step 5: noise and shadow flicker at homes
    <project>/permitting/       step 6: the pack for authorities and consultants

Steps run in order; each uses the latest result of the steps before it. A step whose inputs have been run again
since is marked out of date.

[COMPLIANCE] Results are developer estimates. Nothing here is an accredited or bankable assessment.
"""
from __future__ import annotations

import csv
import json
import shutil
from datetime import date, datetime
from pathlib import Path

import numpy as np

from . import APP_NOTICE, DISCLAIMER, __version__
from . import climate as _climate

FILE = "project.json"
STEPS = ("data", "longterm", "map", "layout", "environment", "pack")
STEP_NAMES = {"data": "Mast data", "longterm": "Long-term correction", "map": "Wind map", "layout": "Layout and energy",
              "environment": "Noise and shadow flicker", "pack": "Permitting pack"}
NEEDS = {"data": (), "longterm": ("data",), "map": ("data",), "layout": ("map",), "environment": ("layout",),
         "pack": ("layout",)}
ESTIMATE_NOTE = ("Developer estimate prepared by Bohemian Renewables with open-source tools. It is not an accredited or "
                 "bankable energy yield assessment and must not be used for financing decisions.")
DEFAULT_ALPHA = 0.14


class ProjectError(RuntimeError):
    """A problem the user can fix; the message says how."""


def defaults() -> dict:
    from .energy import budget  # noqa: PLC0415
    return {
        "name": "", "created": f"{datetime.now():%Y-%m-%d %H:%M}", "demo": False, "version": __version__,
        "site": {"lat": None, "lon": None, "elevation_m": None},
        "data": {"folder": "", "watch": False, "watch_minutes": 60, "stale_hours": 36},
        "longterm": {"source": "power", "monthly_file": "", "daily_file": ""},
        "map": {"engine": "linear", "height_m": 120.0, "half_km": 8.0, "res_m": 100.0, "z0_m": 0.05, "gwa_dir": ""},
        "layout": {"turbine": "4.5 MW, 163 m rotor", "rated_kw": 4500.0, "rotor_m": 163.0, "hub_m": 120.0,
                   "n_turbines": 10, "cross_d": 3.0, "along_d": 7.0, "link_m": 2500.0, "wake": "niayifar", "refine": True,
                   "exclusions_file": "", "receptors_file": "", "boundary_file": "",
                   "rules": {"radius_km": 8.0, "slope_max_pct": 15.0, "exclusion_buffer_m": 100.0, "line_buffer_m": 250.0,
                             "receptor_setback_m": 500.0, "min_mean_ws": None, "elevation_max_m": None},
                   "losses": budget.losses_table()},
        "environment": {"sound_power_dba": 105.0, "noise_limit_dba": 45.0, "flicker_hours_limit": 30.0,
                        "flicker_minutes_limit": 30.0, "year": date.today().year + 1},
        "results": {},
    }


def _merge(base: dict, extra: dict) -> dict:
    out = dict(base)
    for k, v in (extra or {}).items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) and k != "results" else v
    return out


def _num(v, name: str, lo: float | None = None, hi: float | None = None, allow_none: bool = False):
    if v in (None, ""):
        if allow_none:
            return None
        raise ProjectError(f"{name} is missing.")
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ProjectError(f"{name} must be a number.") from None
    if not np.isfinite(f) or (lo is not None and f < lo) or (hi is not None and f > hi):
        raise ProjectError(f"{name} must be between {lo:g} and {hi:g}." if lo is not None and hi is not None
                           else f"{name} is out of range.")
    return f


class Project:
    def __init__(self, folder, settings: dict):
        self.folder = Path(folder)
        self.s = settings

    # ---------- open, create, save ----------

    @classmethod
    def create(cls, folder, name: str, lat=None, lon=None, elevation_m=None) -> "Project":
        folder = Path(folder)
        if (folder / FILE).is_file():
            raise ProjectError(f"There is already a project in {folder}. Open it instead.")
        folder.mkdir(parents=True, exist_ok=True)
        s = defaults()
        s["name"] = (name or folder.name).strip()
        s["site"] = {"lat": _num(lat, "Latitude", -90, 90, True), "lon": _num(lon, "Longitude", -180, 180, True),
                     "elevation_m": _num(elevation_m, "Elevation", -500, 6000, True)}
        p = cls(folder, s)
        p.save()
        return p

    @classmethod
    def open(cls, folder) -> "Project":
        folder = Path(folder)
        f = folder / FILE
        if not f.is_file():
            raise ProjectError(f"No project found in {folder} (there is no {FILE} there). Choose the project's folder, "
                               "or create a new project.")
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ProjectError(f"{f} cannot be read ({e.msg}, line {e.lineno}).") from None
        return cls(folder, _merge(defaults(), raw))

    def save(self) -> None:
        tmp = self.folder / (FILE + ".tmp")
        tmp.write_text(json.dumps(self.s, indent=1, default=str), encoding="utf-8")
        tmp.replace(self.folder / FILE)

    def path(self, *parts) -> Path:
        p = self.folder.joinpath(*parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def rel(self, p) -> str:
        return Path(p).resolve().relative_to(self.folder.resolve()).as_posix()

    # ---------- settings ----------

    def update(self, section: str, values: dict) -> None:
        if section == "name":
            self.s["name"] = str(values.get("name") or self.s["name"]).strip()
        elif section in ("site", "data", "longterm", "map", "layout", "environment"):
            self.s[section] = _merge(self.s[section], values)
        else:
            raise ProjectError(f"Unknown settings section '{section}'.")
        self.save()

    def attach(self, kind: str, source) -> str:
        """Copy a constraints file (exclusions, receptors or boundary) into the project. Returns the stored path."""
        if kind not in ("exclusions", "receptors", "boundary"):
            raise ProjectError(f"Unknown file kind '{kind}'.")
        src = Path(source)
        if not src.is_file():
            raise ProjectError(f"File not found: {src}")
        from .environment import constraints  # noqa: PLC0415
        try:
            feats = constraints.read_features(src)
        except constraints.ConstraintError as e:
            raise ProjectError(str(e)) from e
        if kind == "receptors" and not any(f.kind == "point" for f in feats):
            raise ProjectError("The homes file holds no points. Give a GeoJSON, KML or CSV file with one point per home.")
        if kind == "boundary" and not any(f.kind == "polygon" for f in feats):
            raise ProjectError("The boundary file holds no polygon.")
        dest = self.path("constraints", f"{kind}{src.suffix.lower()}")
        if src.resolve() != dest.resolve():
            shutil.copyfile(src, dest)
        self.s["layout"][f"{kind}_file"] = self.rel(dest)
        self.save()
        return self.rel(dest)

    # ---------- results and state ----------

    def result(self, step: str) -> dict | None:
        return self.s["results"].get(step)

    def _store(self, step: str, summary: dict) -> dict:
        summary = dict(summary)
        summary["at"] = f"{datetime.now():%Y-%m-%d %H:%M:%S}"
        summary["based_on"] = {n: (self.result(n) or {}).get("at") for n in NEEDS[step]}
        if step == "layout":       # the long-term factor used, so a new long-term run marks the layout out of date
            summary["based_on"]["longterm"] = (self.result("longterm") or {}).get("at")
        self.s["results"][step] = summary
        self.save()
        return summary

    def status(self, step: str) -> str:
        """'done', 'stale' (an earlier step was run again since), 'ready' or 'blocked'."""
        r = self.result(step)
        if r:
            for n, at in (r.get("based_on") or {}).items():
                if (self.result(n) or {}).get("at") != at:
                    return "stale"
            return "done"
        return "ready" if all(self.result(n) for n in NEEDS[step]) else "blocked"

    def state(self) -> dict:
        out = {k: v for k, v in self.s.items() if k != "results"}
        out["folder"] = str(self.folder)
        out["steps"] = [{"key": k, "name": STEP_NAMES[k], "status": self.status(k), "result": self.result(k),
                         "needs": [STEP_NAMES[n] for n in NEEDS[k] if not self.result(n)]} for k in STEPS]
        return out

    # ---------- shared pieces ----------

    def climate(self) -> dict:
        r = self.result("data")
        if not r or not r.get("climate") or not self.path(r["climate"]).is_file():
            raise ProjectError("Check the mast data first (step 1): the later steps work from its wind climate.")
        return _climate.load(self.path(r["climate"]))

    def site(self, need_position: bool = True) -> dict:
        c = self.climate()["site"] if self.result("data") else {}
        s = self.s["site"]
        lat = s.get("lat") if s.get("lat") is not None else c.get("lat")
        lon = s.get("lon") if s.get("lon") is not None else c.get("lon")
        elev = s.get("elevation_m") if s.get("elevation_m") is not None else c.get("elevation_m")
        if need_position and (lat is None or lon is None):
            raise ProjectError("The mast position is not known: the logger files give none. Enter the latitude and "
                               "longitude under 'Mast data', then run this step again.")
        return {"lat": lat, "lon": lon, "elevation_m": elev}

    def lt_factor(self) -> tuple:
        r = self.result("longterm")
        if r and r.get("factor"):
            return float(r["factor"]), f"long-term factor {r['factor']:.3f} applied"
        return 1.0, "no long-term correction applied: the measured period is taken as it is"

    def alpha(self, clim: dict) -> tuple:
        a = (clim.get("shear") or {}).get("alpha")
        if a is None:
            return DEFAULT_ALPHA, (f"no shear measured (one height): an assumed power-law exponent of {DEFAULT_ALPHA:g} "
                                   "carries the wind to hub height")
        return float(a), f"measured shear exponent {a:.3f} ({clim['shear']['basis']}) carries the wind to hub height"

    def turbine(self):
        from .energy.turbine import PRESETS, Turbine  # noqa: PLC0415
        L = self.s["layout"]
        hub = _num(L.get("hub_m"), "Hub height", 40, 250)
        if L.get("turbine") in PRESETS:
            p = PRESETS[L["turbine"]]
            return Turbine(p.name, p.rated_kw, p.rotor_m, hub)
        rated, rotor = _num(L.get("rated_kw"), "Rated power", 500, 20000), _num(L.get("rotor_m"), "Rotor diameter", 40, 300)
        if hub < rotor / 2 + 15:
            raise ProjectError("The hub height leaves less than 15 m between the blade tip and the ground.")
        return Turbine(f"Generic {rated / 1000:g} MW / {rotor:g} m", rated, rotor, hub)

    def grid(self):
        from .terrain import Grid  # noqa: PLC0415
        f = self.folder / "terrain" / "grid.npz"
        if not f.is_file():
            raise ProjectError("Draw the wind map first (step 3): it prepares the terrain.")
        return Grid.load(f)

    def features(self, kind: str) -> list:
        from .environment import constraints  # noqa: PLC0415
        rel = self.s["layout"].get(f"{kind}_file")
        if not rel:
            return []
        f = self.folder / rel
        if not f.is_file():
            raise ProjectError(f"The {kind} file is missing from the project: {rel}. Choose it again.")
        try:
            return constraints.read_features(f)
        except constraints.ConstraintError as e:
            raise ProjectError(str(e)) from e

    def wind_field(self, height_m: float, log=lambda m: None):
        """Wind field at height_m: the stored one if it matches, else computed from the stored terrain."""
        from .flow import fields  # noqa: PLC0415
        m = self.s["map"]
        clim = self.climate()
        factor, _ = self.lt_factor()
        alpha, _ = self.alpha(clim)
        key = {"height_m": float(height_m), "engine": m["engine"], "z0_m": float(m["z0_m"]), "factor": round(factor, 5),
               "alpha": round(alpha, 5), "climate": (self.result("data") or {}).get("at"), "half_km": float(m["half_km"])}
        store, meta = self.folder / "maps" / f"windfield_{height_m:g}m.npz", self.folder / "maps" / f"windfield_{height_m:g}m.json"
        if store.is_file() and meta.is_file() and json.loads(meta.read_text(encoding="utf-8")) == key:
            return fields.WindField.load(store)
        grid = self.grid()
        log(f"Computing the wind field at {height_m:g} m ({'own linear flow model' if m['engine'] == 'linear' else 'atlas pattern'}) ...")
        gwa = (m.get("gwa_dir") or None) if m["engine"] == "atlas" else None
        if m["engine"] == "atlas" and not gwa:
            raise ProjectError("The atlas engine needs the folder with the Global Wind Atlas GeoTIFF files for this area "
                               "(download them from globalwindatlas.info), or choose the own flow model.")
        try:
            wf = fields.wind_field(grid, clim, height_m, m["engine"], z0_m=float(m["z0_m"]), lt_factor=factor, alpha=alpha,
                                   gwa_dir=gwa, keep_half_m=float(m["half_km"]) * 1000.0)
        except fields.FlowError as e:
            raise ProjectError(str(e)) from e
        self.path("maps", "x")
        wf.save(store)
        meta.write_text(json.dumps(key), encoding="utf-8")
        return wf

    # ---------- step 1: mast data ----------

    def run_data(self, log=print) -> dict:
        from . import intake  # noqa: PLC0415
        folder = self.s["data"].get("folder")
        if not folder:
            raise ProjectError("Choose the folder that holds the mast's logger files.")
        src = Path(folder)
        if not src.is_absolute():
            src = self.folder / src
        if not src.exists():
            raise ProjectError(f"The data folder was not found: {src}")
        settings_path = None
        s = self.s["site"]
        vals = {"site_name": self.s["name"]}
        if s.get("elevation_m") is not None:
            vals.update(elevation_m=float(s["elevation_m"]), source="entered in the project")
        if s.get("lat") is not None and s.get("lon") is not None:
            vals.update(latitude=float(s["lat"]), longitude=float(s["lon"]))
        if len(vals) > 1 or self.s["name"]:
            settings_path = self.path("runs", "site_settings.json")
            settings_path.write_text(json.dumps(vals, indent=1), encoding="utf-8")
        try:
            out = intake.run_checks(src, self.folder, site_name=self.s["name"] or None, settings_path=settings_path, log=log)
        except intake.IntakeError as e:
            raise ProjectError(str(e)) from e
        if not out.get("climate"):
            raise ProjectError("The checks ran, but there were too few valid wind records to describe the wind. "
                               "Open the report in the run folder for the reasons.")
        for k in ("run_dir", "report", "excel", "climate"):
            if out.get(k):
                out[k] = self.rel(out[k])
        out["figures"] = [self.rel(f) for f in out.get("figures", [])]
        clim = self.climate_from(out["climate"])
        out["rose"] = [{"centre": x["centre"], "freq": x["freq"], "mean": x["mean"]} for x in clim["sectors"]]
        out["monthly"] = [{"month": k, "mean": v["mean"], "share": v["share"]} for k, v in sorted(clim["monthly"].items())]
        out["signature"] = intake.folder_signature(src)
        out["data_folder"] = str(src)
        return self._store("data", out)

    def climate_from(self, rel: str) -> dict:
        return _climate.load(self.folder / rel)

    # ---------- step 2: long-term ----------

    def run_longterm(self, log=print) -> dict:
        from . import longterm  # noqa: PLC0415
        clim = self.climate()
        cfg = self.s["longterm"]
        if cfg.get("source") == "file":
            monthly = self.folder / cfg["monthly_file"] if cfg.get("monthly_file") and not Path(cfg["monthly_file"]).is_absolute() \
                else Path(cfg.get("monthly_file") or "")
            daily = None
            if cfg.get("daily_file"):
                daily = Path(cfg["daily_file"]) if Path(cfg["daily_file"]).is_absolute() else self.folder / cfg["daily_file"]
            if not monthly.is_file():
                raise ProjectError("Choose the NASA POWER monthly file (JSON, wind speed at 50 m), or switch to 'Download'.")
            files = {"monthly": str(monthly), "daily": str(daily) if daily else None}
            log(f"Reference data from {monthly.name}.")
        else:
            site = self.site()
            log(f"Downloading the reference from NASA POWER for {site['lat']:.4f}, {site['lon']:.4f} ...")
            try:
                files = longterm.fetch_power(site["lat"], site["lon"], self.path("longterm", "x").parent)
            except longterm.ReferenceError_ as e:
                raise ProjectError(str(e)) from e
        try:
            ref = longterm.read_power(files["monthly"], files.get("daily"))
            res = longterm.correct(clim, ref)
        except (ValueError, KeyError) as e:
            raise ProjectError(f"The long-term correction could not be made: {e}") from e
        res["reference_first"], res["reference_last"] = str(ref.index[0]), str(ref.index[-1])
        res["measured_height_m"] = clim["height_m"]
        self.path("longterm", "longterm.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
        log(f"Long-term factor {res['factor']:.3f}: {res['measured']:.2f} m/s measured becomes {res['long_term']:.2f} m/s "
            f"at {clim['height_m']:g} m (monthly correlation r = {res['r']:.2f}, {res['months']} months).")
        if res["indicative"]:
            log("  Fewer than 12 calendar months measured: the factor is indicative only.")
        return self._store("longterm", res)

    # ---------- step 3: terrain and wind map ----------

    def run_map(self, log=print) -> dict:
        from . import maps, terrain  # noqa: PLC0415
        from .flow import fields  # noqa: PLC0415
        m = self.s["map"]
        height = _num(m.get("height_m"), "Map height", 10, 300)
        half = _num(m.get("half_km"), "Area half-width", 2, 30)
        res_m = _num(m.get("res_m"), "Grid cell size", 30, 500)
        _num(m.get("z0_m"), "Roughness length", 0.0002, 2.0)
        clim = self.climate()
        site = self.site()
        gfile = self.folder / "terrain" / "grid.npz"
        need_half = half * 1.25
        grid = None
        if gfile.is_file():
            g = terrain.Grid.load(gfile)
            same_place = abs(g.lat0 - site["lat"]) < 1e-6 and abs(g.lon0 - site["lon"]) < 1e-6
            wide = min(-g.x[0], g.x[-1], -g.y[0], g.y[-1]) >= need_half * 1000 - g.res
            if same_place and wide and (abs(g.res - res_m) < 1e-6 or self.s.get("demo")):
                grid = g
                log(f"Terrain: using the stored grid ({g.source or 'no source recorded'}).")
        if grid is None:
            tiles = terrain.needed_tiles(site["lat"], site["lon"], need_half)
            tdir = self.path("terrain", "tiles", "x").parent
            missing = [t for t in tiles if not (tdir / f"{t}.hgt").is_file()]
            if missing:
                log(f"Downloading terrain tiles: {', '.join(missing)} ...")
            try:
                terrain.fetch_tiles(tiles, tdir, log=log)
                grid = terrain.from_srtm(tdir, site["lat"], site["lon"], half_km=need_half, res=res_m)
            except terrain.TerrainError as e:
                raise ProjectError(str(e)) from e
            grid.save(gfile)
            log(f"Terrain grid: {grid.shape[1]} x {grid.shape[0]} cells of {grid.res:g} m, "
                f"{float(np.nanmin(grid.elev)):.0f} to {float(np.nanmax(grid.elev)):.0f} m.")
        wf = self.wind_field(height, log)
        factor, f_note = self.lt_factor()
        _, a_note = self.alpha(clim)
        sub = f"{self.s['name']}: {'own linear flow model' if wf.engine == 'linear' else 'Global Wind Atlas pattern scaled to the mast'}; {f_note}"
        dark = maps.wind_map(wf.grid, wf.mean_ws, self.path("maps", "wind_map.png"), height_m=height, theme="dark",
                             title=f"Mean wind speed at {height:g} m", subtitle=sub)
        light = maps.wind_map(wf.grid, wf.mean_ws, self.path("maps", "wind_map_print.png"), height_m=height, theme="light",
                              title=f"Mean wind speed at {height:g} m", subtitle=sub)
        credits = [terrain.TERRAIN_CREDIT] if "SRTM" in (wf.grid.source or "") else [f"Terrain: {wf.grid.source}."] if wf.grid.source else []
        if wf.engine == "atlas":
            credits.append(fields.GWA_CREDIT)
        out = {"height_m": height, "engine": wf.engine, "map": self.rel(dark), "map_print": self.rel(light),
               "mast_mean_ws": wf.mast_mean_ws, "min_ws": float(np.nanmin(wf.mean_ws)), "max_ws": float(np.nanmax(wf.mean_ws)),
               "p95_ws": float(np.nanpercentile(wf.mean_ws, 95)), "steep_share_pct": 100.0 * wf.steep_share,
               "area_km2": float(wf.grid.shape[0] * wf.grid.shape[1] * wf.grid.res ** 2 / 1e6), "cell_m": wf.grid.res,
               "elev_min": float(np.nanmin(wf.grid.elev)), "elev_max": float(np.nanmax(wf.grid.elev)),
               "mast_elev": float(wf.grid.sample(0.0, 0.0)), "notes": wf.notes + [f_note.capitalize() + ".", a_note.capitalize() + "."],
               "credits": credits, "lt_factor": factor}
        log(f"Wind map at {height:g} m: {out['min_ws']:.1f} to {out['max_ws']:.1f} m/s; {wf.mast_mean_ws:.2f} m/s at the mast.")
        return self._store("map", out)

    # ---------- step 4: constraints, layout, energy ----------

    def run_layout(self, log=print) -> dict:
        from . import maps  # noqa: PLC0415
        from .energy import EnergyError, assess  # noqa: PLC0415
        from .energy.turbine import air_density  # noqa: PLC0415
        from .environment import constraints  # noqa: PLC0415
        L = self.s["layout"]
        turbine = self.turbine()
        n = int(_num(L.get("n_turbines"), "Number of turbines", 1, 150))
        clim = self.climate()
        site = self.site()
        wf = self.wind_field(turbine.hub_m, log)
        grid = wf.grid
        R = L["rules"]
        rules = constraints.Rules(
            radius_km=min(_num(R.get("radius_km"), "Search radius", 1, 30), float(self.s["map"]["half_km"])),
            slope_max_pct=_num(R.get("slope_max_pct"), "Maximum slope", 1, 60),
            elevation_max_m=_num(R.get("elevation_max_m"), "Maximum elevation", 0, 6000, True),
            min_mean_ws=_num(R.get("min_mean_ws"), "Minimum wind speed", 0, 20, True),
            exclusion_buffer_m=_num(R.get("exclusion_buffer_m"), "Buffer round exclusion zones", 0, 5000),
            line_buffer_m=_num(R.get("line_buffer_m"), "Buffer along roads and lines", 0, 5000),
            receptor_setback_m=_num(R.get("receptor_setback_m"), "Setback from homes", 0, 5000))
        exclusions, boundary = self.features("exclusions"), self.features("boundary")
        receptors = constraints.points_of(self.features("receptors"), grid)
        receptors = [r for r in receptors if grid.inside(r["x"], r["y"])]
        mask, rep = constraints.buildable(grid, rules, exclusions=exclusions, receptors=receptors, boundary=boundary,
                                          mean_ws=wf.mean_ws)
        log(f"Buildable area: {rep['buildable_km2']:.1f} km2 after {len(rep['steps']) - 1} rules.")
        elev = site.get("elevation_m") if site.get("elevation_m") is not None else float(grid.sample(0.0, 0.0))
        d = clim.get("density") or {}
        rho, rho_src = air_density(elev, turbine.hub_m, d.get("mean"), d.get("sensor_height_m"))
        factor, f_note = self.lt_factor()
        alpha, a_note = self.alpha(clim)
        try:
            res = assess.design(wf, clim, mask, turbine, n, lt_factor=factor, alpha=alpha, rho_hub=rho,
                                cross_d=_num(L.get("cross_d"), "Spacing across the wind", 1.5, 15),
                                along_d=_num(L.get("along_d"), "Spacing along the wind", 1.5, 20),
                                link_m=_num(L.get("link_m"), "Link distance", 0, 20000, True) or None,
                                wake=L.get("wake") or "niayifar", refine=bool(L.get("refine")), refine_seconds=45.0,
                                losses=L.get("losses") or None, log=log)
        except EnergyError as e:
            raise ProjectError(str(e)) from e
        res["notes"] = res["notes"] + [f_note.capitalize() + ".", a_note.capitalize() + ".",
                                       f"Air density at hub height {rho:.3f} kg/m3 ({rho_src})."]
        res["constraints"] = rep
        res["rules"] = {k: getattr(rules, k) for k in ("radius_km", "slope_max_pct", "elevation_max_m", "min_mean_ws",
                                                       "exclusion_buffer_m", "line_buffer_m", "receptor_setback_m")}
        res["receptors_in_area"] = len(receptors)
        res["estimate_note"] = ESTIMATE_NOTE
        from .terrain import utm  # noqa: PLC0415
        for p in res["positions"]:
            u = utm(p["lat"], p["lon"])
            p.update(utm_zone=f"{u['zone']}{u['hemisphere']}", easting=round(u["easting"], 1), northing=round(u["northing"], 1),
                     tip_height_m=turbine.tip_height, tip_amsl_m=round(p["ground_elev_m"] + turbine.tip_height, 1))
        with open(self.path("layout", "turbines.csv"), "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["id", "latitude", "longitude", "utm_zone", "easting_m", "northing_m", "ground_elevation_m",
                        "hub_height_m", "tip_height_m", "tip_elevation_amsl_m", "free_wind_speed_ms", "gross_mwh",
                        "wake_loss_pct", "net_p50_mwh", "capacity_factor"])
            for p in res["positions"]:
                w.writerow([p["id"], f"{p['lat']:.6f}", f"{p['lon']:.6f}", p["utm_zone"], p["easting"], p["northing"],
                            f"{p['ground_elev_m']:.1f}", turbine.hub_m, p["tip_height_m"], p["tip_amsl_m"],
                            f"{p['free_ws']:.2f}", f"{p['gross_mwh']:.0f}", f"{p['wake_loss_pct']:.2f}",
                            f"{p['p50_mwh']:.0f}", f"{p['capacity_factor']:.3f}"])
        np.savez_compressed(self.path("layout", "buildable.npz"), mask=mask)
        title = f"Layout: {res['n_placed']} x {turbine.name.replace('Generic ', '')}"
        sub = (f"{self.s['name']}: {res['capacity_mw']:.1f} MW, P50 {res['p50_gwh']:.0f} GWh a year "
               f"(developer estimate); bright area is buildable")
        kw = dict(height_m=turbine.hub_m, title=title, subtitle=sub, turbines=res["positions"], receptors=receptors,
                  mask=mask, exclusions=exclusions, boundary=boundary)
        res["map"] = self.rel(maps.wind_map(grid, wf.mean_ws, self.path("layout", "layout_map.png"), theme="dark", **kw))
        res["map_print"] = self.rel(maps.wind_map(grid, wf.mean_ws, self.path("layout", "layout_map_print.png"), theme="light", **kw))
        res["csv"] = "layout/turbines.csv"
        self.path("layout", "layout.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
        u = res["uncertainty"]
        log(f"{res['n_placed']} turbines, {res['capacity_mw']:.1f} MW: gross {res['gross_gwh']:.0f} GWh, wake loss "
            f"{res['wake_loss_pct']:.1f} %, other losses {res['other_loss_pct']:.1f} %, P50 {res['p50_gwh']:.0f} GWh a year "
            f"(capacity factor {100 * res['capacity_factor']:.1f} %), P90 over 10 years {u['p90_10_year']:.0f} GWh.")
        return self._store("layout", res)

    # ---------- step 5: noise and shadow flicker ----------

    def run_environment(self, log=print) -> dict:
        from . import maps  # noqa: PLC0415
        from .environment import constraints, flicker, noise  # noqa: PLC0415
        lay = self.result("layout")
        if not lay:
            raise ProjectError("Place the turbines first (step 4).")
        E = self.s["environment"]
        lw = _num(E.get("sound_power_dba"), "Sound power level", 85, 120)
        limit = _num(E.get("noise_limit_dba"), "Noise limit", 20, 80)
        h_lim = _num(E.get("flicker_hours_limit"), "Flicker limit, hours a year", 0, 500)
        m_lim = _num(E.get("flicker_minutes_limit"), "Flicker limit, minutes a day", 0, 600)
        year = int(_num(E.get("year"), "Year", 1950, 2099))
        t = lay["turbine"]
        hub, rotor = float(t["hub_m"]), float(t["rotor_m"])
        wf = self.wind_field(hub, log)
        grid = wf.grid
        receptors = constraints.points_of(self.features("receptors"), grid)
        receptors = [r for r in receptors if grid.inside(r["x"], r["y"])]
        if not receptors:
            raise ProjectError("There are no homes in the project yet. Under 'Layout and energy', choose a file with one "
                               "point per home (GeoJSON, KML or CSV), run the layout again, then come back.")
        turbines = lay["positions"]
        log(f"Noise at {len(receptors)} homes from {len(turbines)} turbines ...")
        nz = noise.at_receptors(grid, turbines, receptors, hub, lw, limit=limit)
        log(f"  Highest level {nz['highest_dba']:.1f} dB(A); {nz['over_limit']} homes over {limit:g} dB(A).")
        log(f"Shadow flicker for {year}, every minute of daylight ...")
        fl = flicker.at_receptors(grid, turbines, receptors, hub, rotor, year, step_min=1, limit_hours_year=h_lim,
                                  limit_min_day=m_lim)
        log(f"  {fl['with_any_flicker']} homes with any flicker; {fl['over_limit']} over a limit "
            f"(worst case: {fl['highest_hours']:.0f} hours a year).")
        levels = noise.on_grid(grid, turbines, hub, lw)
        sub_n = f"{self.s['name']}: sound power {lw:g} dB(A) per turbine; screening after ISO 9613-2"
        sub_f = f"{self.s['name']}: astronomical worst case for {year}; limits {h_lim:g} h a year, {m_lim:g} min a day"
        out = {"noise": nz, "flicker": fl, "hub_m": hub, "rotor_m": rotor}
        for theme, tag in (("dark", ""), ("light", "_print")):
            out[f"noise_map{tag}"] = self.rel(maps.noise_map(grid, levels, turbines, nz["receptors"], self.path("environment", f"noise_map{tag}.png"),
                                                             limit=limit, theme=theme, subtitle=sub_n))
            out[f"flicker_map{tag}"] = self.rel(maps.flicker_map(grid, turbines, fl["receptors"], self.path("environment", f"flicker_map{tag}.png"),
                                                                 rotor_m=rotor, theme=theme, subtitle=sub_f))
        with open(self.path("environment", "noise_at_homes.csv"), "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["home", "latitude", "longitude", "level_dba", "level_plus_uncertainty_dba", "limit_dba", "over_limit",
                        "nearest_turbine", "distance_m"])
            for r in nz["receptors"]:
                w.writerow([r["name"], r["lat"], r["lon"], r["level_dba"], r["level_plus_uncertainty_dba"], limit,
                            "yes" if r["over_limit"] else "no", r["nearest_turbine"], r["distance_m"]])
        with open(self.path("environment", "flicker_at_homes.csv"), "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["home", "latitude", "longitude", "hours_per_year_worst_case", "max_minutes_per_day", "days_with_flicker",
                        "over_limit", "turbines", "nearest_turbine_m"])
            for r in fl["receptors"]:
                w.writerow([r["name"], r["lat"], r["lon"], r["hours_per_year"], r["max_minutes_per_day"], r["days_with_flicker"],
                            "yes" if r["over_limit"] else "no", " ".join(r["turbines"]), r["nearest_turbine_m"]])
        out["noise_csv"], out["flicker_csv"] = "environment/noise_at_homes.csv", "environment/flicker_at_homes.csv"
        self.path("environment", "environment.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
        return self._store("environment", out)

    # ---------- step 6: permitting pack ----------

    def run_pack(self, log=print) -> dict:
        from .permitting import pack  # noqa: PLC0415
        if not self.result("layout"):
            raise ProjectError("Place the turbines first (step 4): the pack is built round the layout.")
        out = pack.build(self, log=log)
        return self._store("pack", out)

    RUNNERS = {"data": "run_data", "longterm": "run_longterm", "map": "run_map", "layout": "run_layout",
               "environment": "run_environment", "pack": "run_pack"}

    def run(self, step: str, log=print) -> dict:
        if step not in self.RUNNERS:
            raise ProjectError(f"Unknown step '{step}'.")
        return getattr(self, self.RUNNERS[step])(log=log)


def describe() -> dict:
    """Fixed texts and choices the interface shows."""
    from .energy import wakes  # noqa: PLC0415
    from .energy.turbine import PRESETS  # noqa: PLC0415
    return {"version": __version__, "disclaimer": DISCLAIMER, "notice": APP_NOTICE, "estimate_note": ESTIMATE_NOTE,
            "steps": [{"key": k, "name": STEP_NAMES[k]} for k in STEPS],
            "turbines": [{"key": k, **v.as_dict()} for k, v in PRESETS.items()],
            "wake_models": [{"key": k, "name": v} for k, v in wakes.WAKE_MODELS.items()],
            "engines": [{"key": "linear", "name": "Own linear flow model (terrain speed-up)"},
                        {"key": "atlas", "name": "Global Wind Atlas pattern, scaled to the mast"}]}
