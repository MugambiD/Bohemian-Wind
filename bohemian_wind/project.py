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

Steps run in order; each uses the latest result of the steps before it. A result is out of date when a setting or
a file it was made from has changed since, when an earlier step was run again since, or when it rests on a result
that is itself out of date. Nothing new is built on an out-of-date result.

[COMPLIANCE] Results are developer estimates. Nothing here is an accredited or bankable assessment.
"""
from __future__ import annotations

import csv
import hashlib
import json
import shutil
import time
from datetime import date, datetime, timedelta
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
MAX_CELLS = 1.6e6        # terrain cells in one wind map (25 km each way in 50 m cells): beyond it the flow model's memory runs to gigabytes
LISTED_HOMES = 500       # homes kept in the project file per list (noise, flicker), the most affected first; with more
#                          homes than this the full lists live in the CSV files only, or the project file runs to tens of MB


# every earlier step a step rests on, in order: the ones that are out of date are run again before it
RESTS_ON = {"data": (), "longterm": ("data",), "map": ("data", "longterm"), "layout": ("data", "longterm", "map"),
            "environment": ("data", "longterm", "map", "layout"),
            "pack": ("data", "longterm", "map", "layout", "environment")}


def _feed_longterm(s: dict) -> dict:
    cfg = s["longterm"]
    by_file = cfg.get("source") == "file"          # the files count only when the reference is read from them
    return {"source": "file" if by_file else "download", "monthly_file": cfg.get("monthly_file") if by_file else None,
            "daily_file": cfg.get("daily_file") if by_file else None}


def _feed_map(s: dict) -> dict:
    m = dict(s["map"])
    if m.get("engine") != "atlas":                 # the atlas folder counts only for the atlas engine
        m.pop("gwa_dir", None)
    return {**m, "direction_offset_deg": s["data"].get("direction_offset_deg") or 0.0,
            "shear_alpha": s["data"].get("shear_alpha")}


# The settings each step works from. When one of them, or a file it names, changes after the step ran, the result no
# longer belongs to the settings on the screen and the step is out of date. Settings that reach a step only through
# an earlier one are left to that one: the position of the mast is in step 1, and everything rests on step 1.
FEEDS = {
    "data": lambda s: {"site": s["site"], "folder": s["data"].get("folder"), "channels": s["data"].get("channels") or {}},
    "longterm": _feed_longterm,
    "map": _feed_map,
    "layout": lambda s: dict(s["layout"]),
    "environment": lambda s: dict(s["environment"]),
    "pack": lambda s: {"name": s["name"]},
}
_FILE_MARKS: dict = {}


def _canon(v):
    """Settings in one form, so that 3, 3.0 and "3", an empty text and nothing, or the order of keys do not read as
    a change. A setting that is empty or switched off counts the same as one that is not there: a later version
    that brings a new setting, off by default, does not put the results of this one out of date."""
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, dict):
        items = ((str(k), _canon(x)) for k, x in sorted(v.items(), key=lambda kv: str(kv[0])))
        return {k: c for k, c in items if c is not None and c is not False and c != {} and c != []}
    if isinstance(v, (list, tuple)):
        return [_canon(x) for x in v]
    if isinstance(v, str) and not v.strip():
        return None
    try:
        f = float(v)
        return repr(round(f, 9)) if np.isfinite(f) else str(v)
    except (TypeError, ValueError, OverflowError):
        return str(v)


def _file_mark(path) -> str:
    """A short mark of a file's content (empty when there is no file). The content is read once per version of the
    file, told apart by size and time of writing; a file written within the last few seconds is read again each
    time, because a clock that ticks coarsely can give two versions the same time."""
    try:
        st = Path(path).stat()
    except (OSError, TypeError):
        return ""
    key = (str(path), st.st_size, st.st_mtime_ns)
    just_written = abs(time.time_ns() - st.st_mtime_ns) < 3_000_000_000
    if just_written or key not in _FILE_MARKS:
        h = hashlib.sha1()  # noqa: S324 (a change mark, not a secret)
        try:
            with open(path, "rb") as fh:
                for block in iter(lambda: fh.read(1 << 20), b""):
                    h.update(block)
        except OSError:
            return ""
        if len(_FILE_MARKS) > 500:              # versions of files long gone
            _FILE_MARKS.clear()
        _FILE_MARKS[key] = f"{st.st_size}:{h.hexdigest()[:16]}"
    return _FILE_MARKS[key]


def _homes(n: int, one: str = "", many: str = "") -> str:
    """'1 home' or '2,345 homes', with the verb that goes with it when one is given."""
    return f"{n:,} {'home' if n == 1 else 'homes'}" + (f" {one if n == 1 else many}" if one else "")


class ProjectError(RuntimeError):
    """A problem the user can fix; the message says how."""


def defaults() -> dict:
    from .energy import budget  # noqa: PLC0415
    return {
        "name": "", "created": f"{datetime.now():%Y-%m-%d %H:%M}", "demo": False, "version": __version__,
        "site": {"lat": None, "lon": None, "elevation_m": None},
        "data": {"folder": "", "watch": False, "watch_minutes": 60, "stale_hours": 36,
                 # a vane mounted off north: degrees added to every direction from the wind map onwards
                 "direction_offset_deg": 0.0,
                 # shear exponent to use instead of the measured one (empty = measured)
                 "shear_alpha": None,
                 # for loggers whose files carry no sensor heights (Nomad2): {"A4": {"height_m": 78, "direction_offset_deg": -28}}
                 "channels": {}},
        "longterm": {"source": "power", "monthly_file": "", "daily_file": ""},
        # 50 m cells: on a real ridge the crests were narrower than 100 m cells, and a layout lost several per cent of its energy
        "map": {"engine": "linear", "height_m": 120.0, "half_km": 8.0, "res_m": 50.0, "z0_m": 0.05, "gwa_dir": ""},
        "layout": {"turbine": "4.5 MW, 163 m rotor", "rated_kw": 4500.0, "rotor_m": 163.0, "hub_m": 120.0,
                   "n_turbines": 10, "cross_d": 3.0, "along_d": 7.0, "link_m": 2500.0, "wake": "niayifar", "refine": True,
                   "exclusions_file": "", "receptors_file": "", "boundary_file": "",
                   # buildings fetched from open data (environment.buildings): the homes file above goes on top of them
                   "buildings_file": "",
                   # turbine positions of the user's own (one point per turbine): the layout is taken as given
                   "positions_file": "",
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


def _channels(raw) -> dict:
    """Sensor settings by logger port, as the Nomad2 reader takes them."""
    if not isinstance(raw, dict):
        raise ProjectError("Sensor settings must be given by port, for example A4.")
    out = {}
    for port, v in raw.items():
        port = str(port).strip().upper()
        if not port or not isinstance(v, dict):
            continue
        row = {}
        h = _num(v.get("height_m"), f"Height of {port}", 0, 300, True)
        off = _num(v.get("direction_offset_deg"), f"Vane offset of {port}", -360, 360, True)
        if h is not None:
            row["height_m"] = h
        if off:
            row["direction_offset_deg"] = off
        if row:
            out[port] = row
    return out


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

    def resolve(self, path) -> Path | None:
        """A path from the settings: as it is when absolute, otherwise from the project folder."""
        if not path:
            return None
        p = Path(str(path))
        return p if p.is_absolute() else self.folder / p

    # ---------- settings ----------

    def update(self, section: str, values: dict) -> None:
        if section == "name":
            self.s["name"] = str(values.get("name") or self.s["name"]).strip()
        elif section in ("site", "data", "longterm", "map", "layout", "environment"):
            values = dict(values)
            if section == "layout" and "losses" in values:         # checked when entered, not when the layout runs
                from .energy import EnergyError, budget  # noqa: PLC0415
                try:
                    values["losses"] = budget.losses_table(values["losses"])    # nothing given: the table of defaults
                except EnergyError as e:
                    raise ProjectError(str(e)) from e
            channels = values.pop("channels", None) if section == "data" else None
            self.s[section] = _merge(self.s[section], values)
            if section == "layout" and "buildings_file" in values and not values["buildings_file"]:
                self.s["results"].pop("buildings", None)       # the fetched buildings were removed: so is their summary
            if channels is not None:            # replaced as a whole, so a sensor can be taken out again
                self.s["data"]["channels"] = _channels(channels)
        else:
            raise ProjectError(f"Unknown settings section '{section}'.")
        self.save()

    def attach(self, kind: str, source) -> str:
        """Copy a constraints file (exclusions, receptors or boundary) or a file of turbine positions into the
        project. Returns the stored path."""
        if kind not in ("exclusions", "receptors", "boundary", "positions"):
            raise ProjectError(f"Unknown file kind '{kind}'.")
        src = Path(source)
        if not src.is_file():
            raise ProjectError(f"File not found: {src}")
        from .environment import constraints  # noqa: PLC0415
        try:
            feats = constraints.read_features(src)
        except constraints.ConstraintError as e:
            raise ProjectError(str(e)) from e
        if kind == "receptors":
            from .environment.buildings import NOT_A_HOME  # noqa: PLC0415
            if not any(f.kind == "point" or NOT_A_HOME.match(f.name or "") for f in feats):
                raise ProjectError("The homes file holds no points. Give a GeoJSON, KML or CSV file with one point per "
                                   "home, or outlines named 'not a home' to take fetched buildings out.")
        if kind == "positions" and not any(f.kind == "point" for f in feats):
            raise ProjectError("The turbine positions file holds no points. Give a GeoJSON, KML or CSV file with one "
                               "point per turbine.")
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
        summary["at"] = self._stamp(step)
        summary["based_on"] = {n: (self.result(n) or {}).get("at") for n in NEEDS[step]}
        if step in ("map", "layout"):   # both rest on the long-term mean: a new long-term run marks them out of date
            summary["based_on"]["longterm"] = (self.result("longterm") or {}).get("at")
        if step == "layout":            # and the layout on the buildings: fetching or removing them marks it out of date
            summary["based_on"]["buildings"] = (self.result("buildings") or {}).get("at")
        if step == "pack":              # the screening is in the pack only when it was up to date
            summary["based_on"]["environment"] = (self.result("environment") or {}).get("at") if self.status("environment") == "done" else None
        if step in FEEDS:
            summary["inputs"] = self.inputs(step)
        self.s["results"][step] = summary
        self.save()
        return summary

    def _stamp(self, step: str) -> str:
        """The time of a run, to the second. The steps tell each other's runs apart by it, so a second run of a step
        within the same second waits for the next one."""
        before = (self.result(step) or {}).get("at")
        while True:
            at = f"{datetime.now():%Y-%m-%d %H:%M:%S}"
            if at != before:
                return at
            time.sleep(0.05)

    def inputs(self, step: str) -> str:
        """A mark of the settings a step works from and of the files they name (see FEEDS)."""
        feed = FEEDS[step](self.s)
        files = {k: _file_mark(self.resolve(v)) for k, v in feed.items() if k.endswith("_file") and v}
        text = json.dumps([_canon(feed), files], sort_keys=True)
        return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]  # noqa: S324 (a change mark, not a secret)

    def stale_reason(self, step: str, memo: dict | None = None) -> str | None:
        """Why a result is out of date, in a sentence; None when it is up to date or there is none. memo: answers
        already worked out in the same pass (the state of all six steps asks about each step many times over)."""
        if memo is not None and step in memo:
            return memo[step]
        why = self._stale_reason(step, memo)
        if memo is not None:
            memo[step] = why
        return why

    def _stale_reason(self, step: str, memo: dict | None) -> str | None:
        r = self.result(step)
        if not r:
            return None
        if step in FEEDS and r.get("inputs") not in (None, self.inputs(step)):      # results from before this mark was kept carry none
            return "A setting or a file it works from was changed after it ran."
        based = dict(r.get("based_on") or {})
        if step == "layout":
            based.setdefault("buildings", None)     # a layout from before any buildings were fetched
        if step == "pack" and "environment" in based:
            now = (self.result("environment") or {}).get("at") if self.status("environment", memo) == "done" else None
            if based.pop("environment") != now:
                return "The noise and shadow flicker screening changed after the pack was built."
        for n, at in based.items():
            if (self.result(n) or {}).get("at") != at:
                return ("The buildings from open data were fetched or removed after it ran." if n == "buildings"
                        else f"'{STEP_NAMES[n]}' was run again after it.")
            if n in STEPS and self.stale_reason(n, memo):
                return f"It rests on '{STEP_NAMES[n]}', which is out of date."
        return None

    def status(self, step: str, memo: dict | None = None) -> str:
        """'done', 'stale' (out of date: see stale_reason), 'ready' or 'blocked'."""
        if self.result(step):
            return "stale" if self.stale_reason(step, memo) else "done"
        return "ready" if all(self.result(n) for n in NEEDS.get(step, ())) else "blocked"

    def redo(self, step: str, memo: dict | None = None) -> list:
        """The earlier steps that are out of date, in order: they are run again before this one."""
        return [n for n in RESTS_ON.get(step, ()) if self.result(n) and self.status(n, memo) == "stale"]

    def _guard(self, step: str) -> None:
        """Nothing new is built on an out-of-date result."""
        for n in NEEDS.get(step, ()):
            why = self.stale_reason(n)
            if why:
                raise ProjectError(f"'{STEP_NAMES[n]}' (step {STEPS.index(n) + 1}) is out of date. {why} "
                                   "Run it again, then this step.")

    def state(self) -> dict:
        out = {k: v for k, v in self.s.items() if k != "results"}
        out["folder"] = str(self.folder)
        memo: dict = {}
        out["steps"] = [{"key": k, "name": STEP_NAMES[k], "status": self.status(k, memo), "result": self.result(k),
                         "needs": [STEP_NAMES[n] for n in NEEDS[k] if not self.result(n)],
                         "why": self.stale_reason(k, memo), "redo": self.redo(k, memo)} for k in STEPS]
        out["buildings"] = self.result("buildings")
        try:
            out["buildings_half_km"] = self.buildings_half_km()       # how far out "Get buildings" would fetch now
        except ProjectError:
            out["buildings_half_km"] = None
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
                               "longitude under 'Mast data', check the data again (step 1), then run this step.")
        return {"lat": lat, "lon": lon, "elevation_m": elev}

    def speed_scale(self, clim: dict | None = None) -> dict:
        """What the wind climate's speeds are multiplied by, and why (see climate.speed_scale). A long-term
        correction that is out of date is not used: its mean belongs to other data or to another reference."""
        clim = clim or self.climate()
        r = self.result("longterm")
        why = self.stale_reason("longterm")
        if why:
            raise ProjectError(f"The long-term correction is out of date. {why} Run step 2 (long-term correction) "
                               "again, then this step, so both rest on the same data.")
        return _climate.speed_scale(clim, r)

    def vane_offset(self) -> tuple:
        off = _num(self.s["data"].get("direction_offset_deg"), "Vane correction", -360, 360, True) or 0.0
        return off, (f"wind directions turned by {off:+g} degrees (vane correction entered in the project)" if off else "")

    def alpha(self, clim: dict) -> tuple:
        own = _num(self.s["data"].get("shear_alpha"), "Shear exponent", -0.2, 0.8, True)
        if own is not None:
            return own, f"shear exponent {own:g} entered in the project carries the wind to hub height"
        a = (clim.get("shear") or {}).get("alpha")
        if a is None:
            return DEFAULT_ALPHA, (f"no shear measured (one height): an assumed power-law exponent of {DEFAULT_ALPHA:g} "
                                   "carries the wind to hub height")
        return float(a), f"measured shear exponent {a:.3f} ({clim['shear']['basis']}) carries the wind to hub height"

    def warnings(self, clim: dict) -> list:
        """What a reader must know before trusting anything carried away from the mast."""
        out = []
        w = (clim.get("shear") or {}).get("warning")
        if w and self.s["data"].get("shear_alpha") in (None, ""):
            out.append(w)
        if clim.get("no_direction"):
            out.append("No wind direction was measured: every direction is given the same wind, so the wind map and the "
                       "wake losses are rough.")
        lt = self.result("longterm") or {}
        cautions = list(lt.get("cautions") or [])            # a part-year, a thin month or a doubtful vane reaches the energy too
        v = lt.get("vane_check")
        if v and float(v.get("offset_applied_deg") or 0.0) != self.vane_offset()[0]:
            cautions = [c for c in cautions if c != v.get("caution")]
            cautions.append("The vane correction was changed after the vane north check of step 2 (long-term correction): "
                            "run step 2 again to check the wind direction with the new correction.")
        return out + cautions

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

    def receptors(self, grid, outside: list | None = None) -> tuple:
        """The homes the siting rules, the noise and the flicker work with: the buildings fetched from open data, if
        any, with the user's own file on top (environment.buildings.combine). Returns (rows inside the grid given,
        counts by origin). Homes beyond the grid are counted, and added to outside when a list is given: step 4
        gives the grid of the wind map, step 5 the wider terrain round it."""
        from .environment import buildings  # noqa: PLC0415
        own = self.features("receptors")
        rel = self.s["layout"].get("buildings_file")
        fetched = []
        if rel:
            f = self.folder / rel
            if not f.is_file():
                raise ProjectError(f"The buildings file is missing from the project: {rel}. Get the buildings again, or "
                                   "remove them under 'Layout and energy'.")
            try:
                fetched = buildings.load(f)
            except buildings.BuildingsError as e:
                raise ProjectError(str(e)) from e
        rows, counts = buildings.combine(fetched, own, grid.to_xy)
        inside = [r for r in rows if grid.inside(r["x"], r["y"])]
        counts["inside"], counts["outside"] = len(inside), len(rows) - len(inside)
        if outside is not None and len(inside) < len(rows):
            keep = {id(r) for r in inside}
            outside.extend(r for r in rows if id(r) not in keep)
        return inside, counts

    @staticmethod
    def reach_m(lw: float, limit: float, n_turbines: int, rotor_m: float) -> float:
        """How far from its nearest turbine a home can be and still be over the noise level used (allowance
        included) or see shadow flicker, by the methods of step 5. Beyond it neither can happen."""
        from .environment import flicker, noise  # noqa: PLC0415
        return max(noise.reach_m(lw, limit - noise.UNCERTAINTY_DB, n_turbines), flicker.MAX_DISTANCE_ROTORS * rotor_m)

    def buildings_half_km(self) -> float:
        """How far from the mast the buildings are fetched (half the side of the square): the search radius of step 4,
        or the farthest turbine of a layout that was given, plus a margin wide enough for the setback rule and for
        the homes the noise or the flicker of step 5 can reach, by the settings as they are."""
        from .environment import buildings  # noqa: PLC0415
        L, E = self.s["layout"], self.s["environment"]
        R = L.get("rules") or {}
        radius = _num(R.get("radius_km"), "Search radius", 1, 30)
        mapped = _num(self.s["map"].get("half_km"), "Area half-width", 2, 30, True)
        if mapped:                              # as in step 4: turbines are sought no farther out than the wind map
            radius = min(radius, mapped)
        margin, far = buildings.MARGIN_KM, 0.0
        try:
            given = [f.coords[0] for f in self.features("positions") if f.kind == "point"]
            if given:                           # turbine positions of the user's own may lie beyond the search radius
                site = self.site()
                m_lon, m_lat = self._metres(site["lat"])
                far = max(max(abs((lon - site["lon"]) * m_lon), abs((lat - site["lat"]) * m_lat)) for lat, lon in given) / 1000.0
            n = len(given) or int(_num(L.get("n_turbines"), "Number of turbines", 1, 150))
            reach = self.reach_m(_num(E.get("sound_power_dba"), "Sound power level", 85, 120),
                                 _num(E.get("noise_limit_dba"), "Noise limit", 20, 80), n, self.turbine().rotor_m)
            setback = _num(R.get("receptor_setback_m"), "Setback from homes", 0, 5000)
            margin = max(margin, float(np.ceil(max(reach, setback) / 100.0)) / 10.0 + 0.2)
        except ProjectError:
            pass                                # settings still to be filled in: the usual margin
        return float(min(max(radius, far) + margin, buildings.MAX_HALF_KM))

    def buildings_edge_m(self, grid, x, y) -> float | None:
        """The shortest distance from the points x, y (local metres) to the edge of the square the buildings were
        fetched for; negative when a point lies outside it. None when the project uses no fetched buildings."""
        bld = self.result("buildings")
        if not bld or not self.s["layout"].get("buildings_file"):
            return None
        c = bld.get("centre")
        cx, cy = grid.to_xy(c["lat"], c["lon"]) if c else (0.0, 0.0)
        h = float(bld["half_km"]) * 1000.0
        x, y = np.atleast_1d(np.asarray(x, dtype=float)), np.atleast_1d(np.asarray(y, dtype=float))
        return float(np.min(h - np.maximum(np.abs(x - cx), np.abs(y - cy))))

    def wind_field(self, height_m: float, log=lambda m: None):
        """Wind field at height_m: the stored one if it matches, else computed from the stored terrain."""
        from .flow import fields  # noqa: PLC0415
        m = self.s["map"]
        clim = self.climate()
        factor = self.speed_scale(clim)["scale"]
        alpha, _ = self.alpha(clim)
        offset, _ = self.vane_offset()
        key = {"height_m": float(height_m), "engine": m["engine"], "z0_m": float(m["z0_m"]), "factor": round(factor, 5),
               "alpha": round(alpha, 5), "climate": (self.result("data") or {}).get("at"), "half_km": float(m["half_km"]),
               "rotate_deg": offset, "gwa_dir": str(m.get("gwa_dir") or "") if m["engine"] == "atlas" else "",
               "field_version": fields.VERSION}
        store, meta = self.folder / "maps" / f"windfield_{height_m:g}m.npz", self.folder / "maps" / f"windfield_{height_m:g}m.json"
        if store.is_file() and meta.is_file() and json.loads(meta.read_text(encoding="utf-8")) == key:
            return fields.WindField.load(store)
        grid = self.grid()
        log(f"Computing the wind field at {height_m:g} m ({'own linear flow model' if m['engine'] == 'linear' else 'atlas pattern'}) ...")
        gwa = self.resolve(m.get("gwa_dir")) if m["engine"] == "atlas" else None
        if m["engine"] == "atlas" and not gwa:
            raise ProjectError("The atlas engine needs the folder with the Global Wind Atlas GeoTIFF files for this area "
                               "(download them from globalwindatlas.info), or choose the own flow model.")
        try:
            wf = fields.wind_field(grid, clim, height_m, m["engine"], z0_m=float(m["z0_m"]), lt_factor=factor, alpha=alpha,
                                   gwa_dir=gwa, keep_half_m=float(m["half_km"]) * 1000.0, rotate_deg=offset)
        except fields.FlowError as e:
            raise ProjectError(str(e)) from e
        self.path("maps", "x")
        wf.save(store)
        meta.write_text(json.dumps(key), encoding="utf-8")
        return wf

    # ---------- step 1: mast data ----------

    def run_data(self, log=print) -> dict:
        from . import intake  # noqa: PLC0415
        self._guard("data")
        folder = self.s["data"].get("folder")
        if not folder:
            raise ProjectError("Choose the folder that holds the mast's logger files.")
        src = self.resolve(folder)
        if not src.exists():
            raise ProjectError(f"The data folder was not found: {src}")
        settings_path = None
        s = self.s["site"]
        vals = {}                       # only what replaces the logger's own values; the name travels separately
        if s.get("elevation_m") is not None:
            vals["elevation_m"] = float(s["elevation_m"])
        if s.get("lat") is not None and s.get("lon") is not None:
            vals.update(latitude=float(s["lat"]), longitude=float(s["lon"]))
        if vals:
            vals["source"] = "entered in the Bohemian Wind project"
            settings_path = self.path("runs", "site_settings.json")
            settings_path.write_text(json.dumps(vals, indent=1), encoding="utf-8")
        try:
            out = intake.run_checks(src, self.folder, site_name=self.s["name"] or None, settings_path=settings_path, log=log,
                                    channels=self.s["data"].get("channels") or None)
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
        self._guard("longterm")
        clim = self.climate()
        cfg = self.s["longterm"]
        if cfg.get("source") == "file":
            monthly, daily = self.resolve(cfg.get("monthly_file")), self.resolve(cfg.get("daily_file"))
            if monthly is None or not monthly.is_file():
                raise ProjectError("Choose the NASA POWER monthly file (JSON, wind speed at 50 m), or switch to 'Download'.")
            files = {"monthly": str(monthly), "daily": str(daily) if daily else None}
            log(f"Reference data from {monthly.name}.")
        else:
            site = self.site()
            log(f"Downloading the reference from NASA POWER for {site['lat']:.4f}, {site['lon']:.4f} ...")
            dd = clim.get("direction_days")      # the days measured: their reanalysis directions check the vane
            period = None
            if dd and dd.get("deg"):
                first = date.fromisoformat(dd["first"])
                period = (dd["first"], (first + timedelta(days=len(dd["deg"]) - 1)).isoformat())
            try:
                files = longterm.fetch_power(site["lat"], site["lon"], self.path("longterm", "x").parent, period=period)
            except longterm.ReferenceError_ as e:
                raise ProjectError(str(e)) from e
        try:
            ref = longterm.read_power(files["monthly"], files.get("daily"))
            res = longterm.correct(clim, ref)
        except (ValueError, KeyError) as e:
            raise ProjectError(f"The long-term correction could not be made: {e}") from e
        res["reference_first"], res["reference_last"] = str(ref.index[0]), str(ref.index[-1])
        res["measured_height_m"] = clim["height_m"]
        res["vane_check"] = longterm.vane_check(clim, files.get("measured"), self.vane_offset()[0])
        res["reference_air"] = longterm.reference_air(files["monthly"], *(res.get("lt_years") or (None, None)))
        if res["vane_check"] and res["vane_check"]["caution"]:
            res["cautions"].append(res["vane_check"]["caution"])
        self.path("longterm", "longterm.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
        log(f"Long-term factor {res['factor']:.3f}: {res['measured']:.2f} m/s measured becomes {res['long_term']:.2f} m/s "
            f"at {clim['height_m']:g} m (monthly correlation r = {res['r']:.2f}, {res['months']} months).")
        if res["vane_check"]:
            v = res["vane_check"]
            log(f"Vane north check: the direction at {v['height_m']:g} m runs {v['median_deg']:+.0f} degrees from the "
                f"reanalysis on {v['days']} days with {v['min_ws']:g} m/s or more.")
        for c in res["cautions"]:
            log("  " + c)
        return self._store("longterm", res)

    # ---------- step 3: terrain and wind map ----------

    def run_map(self, log=print) -> dict:
        from . import maps, terrain  # noqa: PLC0415
        from .flow import fields  # noqa: PLC0415
        self._guard("map")
        m = self.s["map"]
        height = _num(m.get("height_m"), "Map height", 10, 300)
        half = _num(m.get("half_km"), "Area half-width", 2, 30)
        res_m = _num(m.get("res_m"), "Grid cell size", 30, 500)
        cells = (2 * half * 1.25 * 1000.0 / res_m) ** 2
        if cells > MAX_CELLS:
            fit = int(np.ceil(2 * half * 1.25 * 1000.0 / np.sqrt(MAX_CELLS) / 10.0) * 10)
            raise ProjectError(f"{half:g} km each way in {res_m:g} m cells is {cells / 1e6:.1f} million cells, more than this "
                               f"computer should be asked to hold. Use cells of {fit} m or more, or map a smaller area.")
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
        scale = self.speed_scale(clim)
        f_note = scale["basis"]
        _, a_note = self.alpha(clim)
        sub = f"{self.s['name']}: {'own linear flow model' if wf.engine == 'linear' else 'Global Wind Atlas pattern scaled to the mast'}; {scale['short']}"
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
               "mast_elev": float(wf.grid.sample(0.0, 0.0)),
               "notes": wf.notes + [n[0].upper() + n[1:] + "." for n in (f_note, a_note, self.vane_offset()[1]) if n]
               + ([scale["lean"]] if scale["lean"] else []),
               "credits": credits, "lt_factor": float((self.result("longterm") or {}).get("factor") or 1.0),
               "annual_mean_ws": scale["mean_ws"], "annual_mean_height_m": clim["height_m"],
               "climate_scale": scale["scale"], "warnings": self.warnings(clim) + list(wf.cautions)}
        log(f"Wind map at {height:g} m: {out['min_ws']:.1f} to {out['max_ws']:.1f} m/s; {wf.mast_mean_ws:.2f} m/s at the mast.")
        return self._store("map", out)

    # ---------- step 4: constraints, layout, energy ----------

    def run_layout(self, log=print) -> dict:
        from . import maps  # noqa: PLC0415
        from .energy import EnergyError, assess  # noqa: PLC0415
        from .energy.turbine import air_density  # noqa: PLC0415
        from .environment import constraints  # noqa: PLC0415
        self._guard("layout")
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
        outside: list = []
        receptors, origin = self.receptors(grid, outside)
        if origin["fetched"] or origin["taken_out"] or origin["replaced"]:
            did = ([f"its points stand for {origin['replaced']:,} fetched buildings"] if origin["replaced"] else []) + (
                [f"it took {origin['taken_out']:,} buildings out"] if origin["taken_out"] else [])
            log(f"Homes: {origin['fetched']:,} buildings from open data and {origin['yours']:,} of your own file"
                + ("; " + " and ".join(did) if did else "") + ".")
        if outside:
            log(f"{_homes(len(receptors), 'is', 'are')} on the wind map and {len(outside):,} beyond its edge.")
        sb = rules.receptor_setback_m           # a home just beyond the wind map still keeps turbines away from its edge
        at_edge = [r for r in outside if grid.x[0] - sb <= r["x"] <= grid.x[-1] + sb and grid.y[0] - sb <= r["y"] <= grid.y[-1] + sb]
        mask, rep = constraints.buildable(grid, rules, exclusions=exclusions, receptors=receptors + at_edge,
                                          boundary=boundary, mean_ws=wf.mean_ws)
        log(f"Buildable area: {rep['buildable_km2']:.1f} km2 after {len(rep['steps']) - 1} rules.")
        elev = site.get("elevation_m") if site.get("elevation_m") is not None else float(grid.sample(0.0, 0.0))
        d = clim.get("density") or {}
        rho, rho_src = air_density(elev, turbine.hub_m, d.get("mean"), d.get("sensor_height_m"),
                                   reference=(self.result("longterm") or {}).get("reference_air"))
        scale = self.speed_scale(clim)
        f_note = scale["basis"]
        alpha, a_note = self.alpha(clim)
        given = constraints.points_of(self.features("positions"), grid, prefix="T")
        fixed = None
        if given:
            out = [g["name"] for g in given if not grid.inside(g["x"], g["y"])]
            if out:
                raise ProjectError(f"{len(out)} of the {len(given)} turbine positions given {'lies' if len(out) == 1 else 'lie'} outside the mapped area "
                                   f"({', '.join(out[:5])}{' and others' if len(out) > 5 else ''}). Widen the wind map "
                                   "(step 3) or correct the positions file.")
            fixed = (np.array([g["x"] for g in given]), np.array([g["y"] for g in given]))
            n = len(given)
            log(f"Using the {n} turbine positions given in {self.s['layout']['positions_file']}: the layout is taken as it is.")
        try:
            res = assess.design(wf, clim, mask, turbine, n, lt_factor=scale["scale"], alpha=alpha, rho_hub=rho, fixed_xy=fixed,
                                cross_d=_num(L.get("cross_d"), "Spacing across the wind", 1.5, 15),
                                along_d=_num(L.get("along_d"), "Spacing along the wind", 1.5, 20),
                                link_m=_num(L.get("link_m"), "Link distance", 0, 20000, True) or None,
                                wake=L.get("wake") or "niayifar", refine=bool(L.get("refine")), refine_seconds=45.0,
                                losses=L.get("losses"), log=log)
        except EnergyError as e:
            raise ProjectError(str(e)) from e
        res["notes"] = res["notes"] + [n[0].upper() + n[1:] + "." for n in (f_note, a_note, self.vane_offset()[1]) if n] + (
            [scale["lean"]] if scale["lean"] else []) + [f"Air density at hub height {rho:.3f} kg/m3 ({rho_src})."]
        res["annual_mean_ws"], res["annual_mean_height_m"], res["climate_scale"] = scale["mean_ws"], clim["height_m"], scale["scale"]
        res["warnings"] = self.warnings(clim) + list(wf.cautions) + list(res.pop("cautions", []))
        need = rules.radius_km + sb / 1000.0    # the setback rule looks this far from the mast
        edge = self.buildings_edge_m(grid, [-need * 1000.0, need * 1000.0], [-need * 1000.0, need * 1000.0])
        if edge is not None and edge < -1.0:
            res["warnings"].append(
                f"The buildings from open data were fetched for {float(self.result('buildings')['half_km']):g} km round the mast, "
                f"and the siting rules now reach {need:.1f} km: homes beyond the fetched area are missing from the setback "
                "rule. Get the buildings again, then place the turbines again.")
        res["given_layout"] = bool(given)
        if given:
            names = [g["name"] for g in given]
            if len(set(names)) == len(names):
                for q, name in zip(res["positions"], names):
                    q["id"] = name
            r_, c_ = grid.index(fixed[0], fixed[1])
            off = [q["id"] for q, ok in zip(res["positions"], mask[r_, c_]) if not ok]
            res["notes"].insert(0, f"The {len(given)} turbine positions were given, not designed: number of turbines, "
                                   "spacing rules and fine-tuning were not applied.")
            if off:
                res["warnings"].append(f"{len(off)} of the {len(given)} given positions {'is' if len(off) == 1 else 'are'} outside the buildable area "
                                       f"of your constraints: {', '.join(off[:12])}{' and others' if len(off) > 12 else ''}.")
            res["outside_buildable"] = off
        res["constraints"] = rep
        res["rules"] = {k: getattr(rules, k) for k in ("radius_km", "slope_max_pct", "elevation_max_m", "min_mean_ws",
                                                       "exclusion_buffer_m", "line_buffer_m", "receptor_setback_m")}
        res["receptors_in_area"] = len(receptors)
        res["receptor_origin"] = origin
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
        from .environment import flicker, noise  # noqa: PLC0415
        self._guard("environment")
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
        grid = self.grid()                      # the whole terrain: it reaches a quarter farther out than the wind map,
        outside: list = []                      # and noise and flicker need its heights only, so homes out there count too
        receptors, _ = self.receptors(grid, outside)
        if not receptors and outside:
            raise ProjectError(f"{'The only home lies' if len(outside) == 1 else f'All {len(outside):,} homes lie'} outside "
                               "the terrain of the wind map, so there is nothing to assess. Draw the wind map over a wider "
                               "area (step 3) and place the turbines again, or check the positions in the homes file.")
        if not receptors:
            raise ProjectError("There are no homes in the project yet. Under 'Layout and energy', get the buildings from "
                               "open data, or choose a file with one "
                               "point per home (GeoJSON, KML or CSV), run the layout again, then come back.")
        turbines = lay["positions"]
        log(f"Noise at {len(receptors)} homes from {len(turbines)} turbines ...")
        nz = noise.at_receptors(grid, turbines, receptors, hub, lw, limit=limit)
        log(f"  Highest level {nz['highest_dba']:.1f} dB(A); {nz['over_limit']} homes over {limit:g} dB(A); the nearest "
            f"home is {nz['nearest_home_m']:.0f} m from a turbine.")
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
        tx, ty = np.array([q["x"] for q in turbines], dtype=float), np.array([q["y"] for q in turbines], dtype=float)
        reach = self.reach_m(lw, limit, len(turbines), rotor)
        out["reach_m"] = round(reach, 0)
        cautions = []
        if outside:                             # supplied, but beyond the terrain: said, listed and weighed
            for r in outside:
                r["nearest_turbine_m"] = float(np.hypot(tx - r["x"], ty - r["y"]).min())
            outside.sort(key=lambda r: r["nearest_turbine_m"])
            with open(self.path("environment", "homes_not_assessed.csv"), "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["home", "latitude", "longitude", "nearest_turbine_m", "within_reach_of_noise_or_flicker", "why_not_assessed"])
                for r in outside:
                    w.writerow([r["name"], r["lat"], r["lon"], f"{r['nearest_turbine_m']:.0f}",
                                "yes" if r["nearest_turbine_m"] <= reach else "no", "outside the terrain of the wind map"])
            near, nearest = sum(r["nearest_turbine_m"] <= reach for r in outside), outside[0]["nearest_turbine_m"]
            out["not_assessed"] = {"n": len(outside), "of": len(outside) + len(receptors), "nearest_turbine_m": round(nearest, 0),
                                   "within_reach": near, "csv": "environment/homes_not_assessed.csv"}
            log(f"  {len(outside):,} of the {len(outside) + len(receptors):,} homes lie outside the terrain of the wind map and "
                f"were not assessed; the nearest is {nearest / 1000:.1f} km from a turbine.")
            if near:
                cautions.append(
                    f"{_homes(near, 'was', 'were')} not assessed, though within {reach / 1000:.1f} km of a turbine and so near "
                    "enough for noise over the level used or for shadow flicker: the terrain of the wind map ends before "
                    f"{'it' if near == 1 else 'them'}. Draw the wind map over a wider area (step 3), then run steps 4 and 5 again.")
        else:                                   # the list of an earlier run no longer holds
            (self.folder / "environment" / "homes_not_assessed.csv").unlink(missing_ok=True)
        edge = self.buildings_edge_m(grid, tx, ty)
        if edge is not None and edge < reach:   # the fetched buildings stop short of where the turbines can be heard or seen
            cautions.append(
                f"The buildings from open data were fetched for {float(self.result('buildings')['half_km']):g} km round the mast. "
                + (f"A turbine stands {edge / 1000:.1f} km from the edge of that area" if edge >= 0 else "A turbine stands outside that area")
                + f", and noise over the level used or shadow flicker can reach {reach / 1000:.1f} km from a turbine: homes "
                "beyond the edge are not in the data. Get the buildings again (the area follows the settings as they are "
                "now), then run steps 4 and 5 again.")
        out["cautions"] = cautions
        for c in cautions:
            log(f"  Caution: {c}")
        if len(receptors) > LISTED_HOMES:       # a survey of thousands of buildings: the project keeps the most affected
            nz["receptors"], fl["receptors"] = nz["receptors"][:LISTED_HOMES], fl["receptors"][:LISTED_HOMES]
            nz["receptors_listed"] = fl["receptors_listed"] = LISTED_HOMES
            log(f"  The project keeps the {LISTED_HOMES} most affected of the {len(receptors):,} homes; every home is in "
                "noise_at_homes.csv and flicker_at_homes.csv.")
        self.path("environment", "environment.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
        return self._store("environment", out)

    # ---------- step 6: permitting pack ----------

    def run_pack(self, log=print) -> dict:
        from .permitting import pack  # noqa: PLC0415
        self._guard("pack")
        if not self.result("layout"):
            raise ProjectError("Place the turbines first (step 4): the pack is built round the layout.")
        out = pack.build(self, log=log)
        return self._store("pack", out)

    def run_buildings(self, log=print) -> dict:
        """Fetches the buildings round the site from open data, for the siting rules of step 4 and the screening of
        step 5 (environment.buildings). Not one of the six steps: the layout is run again afterwards."""
        from . import maps  # noqa: PLC0415
        from .environment import buildings  # noqa: PLC0415
        site = self.site()
        m_lon, m_lat = self._metres(site["lat"])
        half = self.buildings_half_km()
        try:
            out = buildings.fetch(site["lat"], site["lon"], half, self.path("constraints", buildings.FILE).parent, log=log)
            rows = buildings.load(self.path("constraints", buildings.FILE))
        except buildings.BuildingsError as e:
            raise ProjectError(str(e)) from e
        out["file"] = f"constraints/{buildings.FILE}"
        out["centre"] = {"lat": site["lat"], "lon": site["lon"]}
        for r in rows:
            r["x"], r["y"] = (r["lon"] - site["lon"]) * m_lon, (r["lat"] - site["lat"]) * m_lat
        sub = (f"{self.s['name']}: {out['n_osm']:,} in OpenStreetMap, {out['n_added_from_microsoft']:,} more in Microsoft's "
               f"footprints, within {out['half_km']:g} km")
        out["map"] = self.rel(maps.buildings_map(rows, self.path("constraints", "buildings_map.png"), half_km=out["half_km"],
                                                 subtitle=sub))
        self.s["layout"]["buildings_file"] = out["file"]
        out["at"] = f"{datetime.now():%Y-%m-%d %H:%M:%S}"
        self.s["results"]["buildings"] = out
        self.save()
        return out

    @staticmethod
    def _metres(lat: float) -> tuple:
        from .terrain import metres_per_degree  # noqa: PLC0415
        return metres_per_degree(lat)

    RUNNERS = {"data": "run_data", "longterm": "run_longterm", "map": "run_map", "layout": "run_layout",
               "environment": "run_environment", "pack": "run_pack", "buildings": "run_buildings"}

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
