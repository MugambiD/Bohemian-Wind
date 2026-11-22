"""The three Word reports of a project, written from its latest results.

  wind resource analysis   the measurement: setup, data, wind statistics, long-term correction, shear
  micrositing              the wind map, the siting rules, the layout and its energy with the uncertainty
  project wind summary     one short document that ties the two together with the screening and the open points

Each takes what the project holds and leaves out what it does not have yet: a wind resource analysis can be
written after step 1, a micrositing report needs the layout. Nothing is computed here that the steps did not
compute; the reports only set it out.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from .. import __version__
from ..terrain import utm
from . import figures as fg
from .kit import NOTICE, Report

HUB_HEIGHTS = (80.0, 100.0, 120.0, 140.0, 160.0)
ENGINES = {"linear": "Bohemian Wind's own linear flow model (terrain only)",
           "atlas": "the Global Wind Atlas pattern, scaled to the mast",
           "cfd": "a CFD screening run (OpenFOAM, steady RANS, k-epsilon, neutral)"}
SCOPE_NOT = ("It is a developer's own analysis, made with open-source tools. It has not been made or reviewed by an "
             "accredited laboratory, and it does not follow a measurement or energy-yield standard in full: where it "
             "departs from usual practice is listed in the text. A financing decision needs an independent assessment.")


def _slug(name: str) -> str:
    return (re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-")[:28] or "project").upper()


def _f(v, nd: int = 2, unit: str = "") -> str:
    if v is None:
        return "not available"
    try:
        return f"{float(v):,.{nd}f}{unit}"
    except (TypeError, ValueError):
        return str(v)


def _day(s) -> str:
    return str(s)[:10]


class Source:
    """What a project holds, read once: results, the wind climate, and the tables of the latest step-1 run."""

    def __init__(self, project):
        self.p = project
        self.name = project.s["name"]
        self.data = project.result("data") or {}
        self.lt = project.result("longterm") if project.result("longterm") and not project.stale_reason("longterm") else None
        self.map = project.result("map") if project.result("map") and not project.stale_reason("map") else None
        self.lay = project.result("layout") if project.result("layout") and not project.stale_reason("layout") else None
        self.env = project.result("environment") if project.result("environment") and project.status("environment") == "done" else None
        self.clim = project.climate() if self.data.get("climate") else None
        try:
            self.site = project.site()
        except Exception:  # noqa: BLE001  (no position yet)
            self.site = None
        self.run_dir = project.folder / self.data["run_dir"] if self.data.get("run_dir") else None
        self._sheets = None
        self._speeds = False

    @property
    def sheets(self) -> dict:
        """The tables of summary.xlsx, by sheet name; empty when the file cannot be read."""
        if self._sheets is None:
            self._sheets = {}
            f = self.run_dir / "summary.xlsx" if self.run_dir else None
            if f and f.is_file():
                try:
                    import pandas as pd  # noqa: PLC0415
                    self._sheets = pd.read_excel(f, sheet_name=None)
                except Exception:  # noqa: BLE001
                    self._sheets = {}
        return self._sheets

    def sheet(self, *starts):
        for name, df in self.sheets.items():
            if any(name.lower().startswith(s.lower()) for s in starts) and len(df):
                return df
        return None

    @property
    def series(self):
        """The valid wind speeds by height (columns ws_<height>m), when the run kept its time series; else None."""
        if self._speeds is False:
            self._speeds = None
            f = self.run_dir / "timeseries_by_height.csv" if self.run_dir else None
            if f and f.is_file() and self.clim:
                try:
                    import pandas as pd  # noqa: PLC0415
                    self._speeds = pd.read_csv(f, usecols=lambda c: str(c).startswith("ws_"))
                except Exception:  # noqa: BLE001
                    self._speeds = None
        return self._speeds

    @property
    def speeds(self):
        """The valid wind speeds at the top height."""
        s, col = self.series, f"ws_{self.clim['height_m']:g}m" if self.clim else ""
        return s[col].dropna().to_numpy() if s is not None and col in s.columns else None

    def pairs(self) -> list:
        """(height, table) of every level with two anemometers: their median ratio by direction."""
        out = []
        for name, df in self.sheets.items():
            m = re.match(r"Pair ([\d.]+) m", name)
            if m and len(df):
                out.append((float(m.group(1)), df))
        return sorted(out, key=lambda x: -x[0])

    def alpha(self):
        try:
            return self.p.alpha(self.clim)
        except Exception:  # noqa: BLE001
            return None, ""

    def lt_mean(self):
        """(mean at the measurement height, what it is)."""
        c = self.clim
        if self.lt:
            return float(self.lt["long_term"]), "long-term corrected"
        if c.get("momm_ws") and c.get("calendar_months") == 12:
            return float(c["momm_ws"]), "mean of monthly means, no long-term correction"
        return float(c["mean_ws"]), "mean of all valid records, no long-term correction"

    def position_rows(self) -> list:
        s = self.site
        if not s:
            return [("Position", "not given")]
        u = utm(s["lat"], s["lon"])
        rows = [("Geographic coordinates (WGS 84)", f"{s['lat']:.6f}°, {s['lon']:.6f}°"),
                ("UTM (WGS 84)", f"zone {u['zone']}{u['hemisphere']}, {u['easting']:.0f} m E, {u['northing']:.0f} m N")]
        elev = s.get("elevation_m") if s.get("elevation_m") is not None else (self.map or {}).get("mast_elev")
        if elev is not None:
            rows.append(("Ground elevation", f"about {elev:.0f} m above sea level"
                         + ("" if s.get("elevation_m") is not None else " (from the terrain model)")))
        return rows


def _ref_pct(src: Source) -> float:
    """The reference term of the wind speed uncertainty: fixed with one reanalysis, from their distance with two."""
    from ..energy import budget  # noqa: PLC0415
    return budget.reference_pct(((src.lt or {}).get("second") or {}).get("difference_pct"))


def _tables(c: dict | None) -> dict | None:
    """What a climate made from summary tables says about itself (intake/summary.py); None for logger files."""
    return (c or {}).get("summary") or None


def _meas(src: Source) -> tuple:
    """(measurement term in % of wind speed, its basis)."""
    from ..energy import budget  # noqa: PLC0415
    t = _tables(src.clim)
    if t:
        return float(t.get("measurement_pct") or 4.0), "monthly means taken from reports: sensors, mounting and data handling not checked"
    return budget.DEFAULT_SPEED_UNCERTAINTY[budget.MEASUREMENT], "one mast: sensors, mounting, data recovery"


def _second_rows(lt: dict) -> list:
    s = lt["second"]
    return [["First (carried forward): " + lt.get("credit", "").split(": ", 1)[-1].rstrip("."), f"{lt['factor']:.3f}", f"{lt['long_term']:.2f}", f"{lt['r']:.2f}",
             f"{lt['lt_years'][0]} to {lt['lt_years'][1]}"],
            ["Second: " + s["what"].split(":")[0], f"{s['factor']:.3f}", f"{s['long_term']:.2f}", f"{s['r']:.2f}", f"{s['lt_years'][0]} to {s['lt_years'][1]}"]]


def _hub_table(src: Source) -> list:
    """Long-term mean by hub height with the wind speed uncertainty the energy step would use (1 sigma)."""
    from ..energy import budget  # noqa: PLC0415
    c = src.clim
    alpha, _ = src.alpha()
    mean, _ = src.lt_mean()
    if alpha is None:
        return []
    top = c["height_m"]
    clean = not (c.get("shear") or {}).get("warning")
    ltp, _ = budget.long_term_pct(c.get("calendar_months"), corrected=bool(src.lt), months=len(c.get("monthly") or {}))
    rows = []
    for h in sorted(set(HUB_HEIGHTS) | {top}):
        v = budget.vertical_pct(h, top, clean)
        terms = [_meas(src)[0], ltp, v] + ([_ref_pct(src)] if src.lt else [])
        total = sum(t * t for t in terms) ** 0.5
        rows.append([f"{h:g} m" + (" (top anemometer)" if h == top else ""), f"{mean * (h / top) ** alpha:.2f}", f"{v:.1f}", f"{total:.1f}"])
    return rows


def _df_rows(df, cols: list | None = None, nd: int = 2, limit: int = 40) -> tuple:
    """(head, rows) of a table for the document: numbers rounded, dates shortened, long tables cut at limit."""
    import pandas as pd  # noqa: PLC0415
    df = df[[c for c in (cols or df.columns) if c in df.columns]]
    head = [str(c).replace("_", " ") for c in df.columns]
    rows = []
    for _, r in df.head(limit).iterrows():
        out = []
        for v in r:
            if v is None or (isinstance(v, float) and pd.isna(v)) or v is pd.NaT:
                out.append("")
            elif isinstance(v, (pd.Timestamp, datetime)):
                out.append(f"{v:%Y-%m-%d %H:%M}" if (v.hour or v.minute) else f"{v:%Y-%m-%d}")
            elif isinstance(v, float):
                out.append(f"{v:,.0f}" if float(v).is_integer() and abs(v) >= 1000 else f"{v:g}" if float(v).is_integer() else
                           f"{v:.{nd}f}" if abs(v) < 1e5 else f"{v:,.0f}")
            else:
                out.append(str(v))
        rows.append(out)
    return head, rows


def _basis(src: Source) -> list:
    d = src.data
    rows = [("Project", src.name), ("Measurement data", f"{d.get('format', 'logger files')}; {_day(d.get('period', ['', ''])[0])} to "
                                    f"{_day(d.get('period', ['', ''])[1])}; "
                                    + (f"{src.clim['recovery_pct']:.0f} % of the period valid at the top height, as reported" if _tables(src.clim)
                                       else f"{d.get('records', 0):,} valid records at the top height"))]
    if src.lt:
        rows.append(("Long-term reference", f"{src.lt.get('method', 'monthly index')}; {src.lt.get('credit', '')}"))
    if src.map:
        rows.append(("Terrain and flow model", "; ".join((src.map.get("credits") or [])[:1]) + f" {ENGINES.get(src.map['engine'], src.map['engine'])}."))
    rows.append(("Software", f"Bohemian Wind v{__version__} (open source, MIT licence)"))
    rows.append(("Results of", ", ".join(f"{k} {src.p.result(k)['at']}" for k in ("data", "longterm", "map", "layout", "environment")
                                         if src.p.result(k) and src.p.result(k).get("at"))))
    return rows


def _long_term(doc, src: Source) -> None:
    """The long-term correction as a report section's body (the heading is the caller's)."""
    lt = src.lt
    if lt:
        doc.p(f"The measured period is short against the life of a wind farm. To place it in the long term, the monthly means of the mast "
              f"are set against a reanalysis reference for the same months. Method: {lt.get('method', 'monthly index')}.")
        doc.kv([("Reference", lt.get("credit", "")), *([("Reference file", lt["reference_file"].split(": ", 1)[-1])] if lt.get("reference_file") else []),
                ("Reference record", f"{lt.get('reference_first', '')[:7]} to {lt.get('reference_last', '')[:7]}"),
                ("Long-term years", f"{lt['lt_years'][0]} to {lt['lt_years'][1]}"),
                ("Months used", f"{lt['months']} ({lt['first_month']} to {lt['last_month']}); {lt['calendar_months']} of the twelve calendar months"),
                ("Monthly correlation", f"r = {lt['r']:.2f}"),
                ("Reference, same months / long term", f"{lt['reference_concurrent']:.2f} / {lt['reference_long_term']:.2f} m/s (at the reference's own height)"),
                (f"Mast at {lt['height_m']:g} m, same months", f"{lt['measured']:.2f} m/s"),
                ("Long-term factor", f"{lt['factor']:.3f}"), (f"Long-term mean at {lt['height_m']:g} m", f"{lt['long_term']:.2f} m/s")],
               caption="Long-term correction")
        doc.figure(fg.correlation(lt.get("monthly"), lt.get("r")), "Monthly means of the mast against the reference", 10.5)
        if lt.get("second"):
            s2 = lt["second"]
            doc.h2("A second reference")
            doc.p(f"The correction was made again with a second, independent reanalysis series ({s2['what']}). It puts the long-term mean at "
                  f"{s2['long_term']:.2f} m/s, {s2['difference_pct']:+.1f} % from the first. The first reference's mean is the one carried "
                  f"forward{' (here the reference read from a file was chosen to be carried, and the downloaded one is set beside it)' if lt.get('carried') == 'second' else ''}"
                  f"; the mean of the two would be {s2['mean_of_two']:.2f} m/s. The distance between them sets the reference term of "
                  f"the uncertainty: {_ref_pct(src):.1f} % of the wind speed.")
            doc.table(["Reference", "Long-term factor", f"Long-term mean at {lt['height_m']:g} m, m/s", "Monthly r", "Long-term years"],
                      _second_rows(lt), widths=[6.4, 2.2, 3.0, 1.8, 2.5], caption="The two long-term references side by side")
            doc.figure(fg.correlation(s2.get("monthly"), s2.get("r")), "Monthly means of the mast against the second reference", 10.5)
        v = lt.get("vane_check")
        if v:
            doc.p(f"Vane north check: on {v['days']} days with {v['min_ws']:g} m/s or more, the direction at {v['height_m']:g} m runs "
                  f"{v['median_deg']:+.0f}° from the reanalysis (quartiles {v['q25_deg']:+.0f}° to {v['q75_deg']:+.0f}°)."
                  + (f" {v['caution']}" if v.get("caution") else ""))
        if lt.get("cautions"):
            doc.flag(lt["cautions"], label="Check:")
        doc.note("Two reanalyses share many of their observations, so their agreement narrows the doubt without removing it. No nearby "
                 "long-term station was used." if lt.get("second") else
                 "One reanalysis is one view of the long term. The correction was not checked against a second reference or a "
                 "nearby long-term station; the uncertainty carries a term for this.")
    else:
        doc.flag("No long-term correction has been made (step 2 has not been run, or is out of date). The figures of this report are "
                 "those of the measured period.", label="Check:")


# ---------------------------------------------------------------- wind resource analysis

def wind_resource(project, path, date: datetime | None = None) -> Path:
    src = Source(project)
    if not src.clim:
        raise ValueError("Run step 1 (mast data) first: the wind resource analysis is written from its results.")
    if _tables(src.clim):
        return _wind_resource_from_tables(src, path, date)
    c, d, lt = src.clim, src.data, src.lt
    date = date or datetime.now()
    top = c["height_m"]
    alpha, alpha_note = src.alpha()
    mean, mean_what = src.lt_mean()
    doc = Report("Wind Resource Analysis", src.name, f"BR-WRA-{_slug(src.name)}-{date:%Y%m%d}",
                 [f"Met mast measurement, {_day(c['period'][0])} to {_day(c['period'][1])}"], date)
    doc.control(basis=_basis(src))
    titles = ["Summary", "Introduction", "Site and mast", "Measurement setup", "Measurement data", "Wind statistics",
              "Turbulence, air density and climate", "Long-term correction", "Wind shear and hub heights", "Uncertainty",
              "Findings of the data checks", "Departures from usual practice", "Data sources"]
    doc.contents(titles)

    # 1 summary
    doc.h1(titles[0])
    doc.p(f"This report sets out the wind measured at the met mast of {src.name} and what it means for the long term. "
          f"The mast's top anemometer stands at {top:g} m. Between {_day(c['period'][0])} and {_day(c['period'][1])} it gave "
          f"{c['records']:,} valid ten-minute records ({c['recovery_pct']:.1f} % of the period).")
    rows = src.position_rows() + [
        ("Measurement period", f"{_day(c['period'][0])} to {_day(c['period'][1])} ({len(c.get('monthly') or {})} months touched, "
                               f"{d.get('months_usable', '?')} with at least 70 % valid data)"),
        (f"Mean wind speed at {top:g} m, measured", f"{c['mean_ws']:.2f} m/s over all valid records"
         + (f"; {c['momm_ws']:.2f} m/s as the mean of monthly means" if c.get("momm_ws") else "")),
        (f"Mean wind speed at {top:g} m, long term", f"{mean:.2f} m/s ({mean_what})"),
    ]
    if lt:
        rows += [("Long-term factor", f"{lt['factor']:.3f} ({100 * (lt['factor'] - 1):+.1f} %), from {lt['months']} months"),
                 ("Correlation with the reference", f"monthly r = {lt['r']:.2f}; reference years {lt['lt_years'][0]} to {lt['lt_years'][1]}")]
    rows += [("Weibull fit at the top height", f"A = {c['weibull_A']:.2f} m/s, k = {c['weibull_k']:.2f}"),
             ("Shear exponent", f"{alpha:.3f} ({(c.get('shear') or {}).get('basis') or alpha_note})" if alpha is not None else "not measured"),
             ("Turbulence intensity at 15 m/s", f"{_f((c.get('ti') or {}).get('representative_15'), 3)} (representative)"),
             ("Air density", f"{_f((c.get('density') or {}).get('mean'), 3)} kg/m³ at the sensors")]
    if c.get("sectors"):
        best = max(c["sectors"], key=lambda s: s["freq"] * s["A"] ** 3)
        rows.append(("Main direction of the wind energy", f"{best['centre']:.0f}° ({100 * best['freq']:.0f} % of the time)"))
    doc.kv(rows, caption="Overview of results")
    hub = _hub_table(src)
    if hub:
        doc.table(["Height above ground", "Long-term mean wind speed, m/s", "Vertical extrapolation, % of speed", "Total, % of speed (1 σ)"],
                  hub, widths=[4.6, 4.2, 3.8, 3.3], caption="Long-term mean wind speed by hub height, with its uncertainty")
        doc.note("The total combines measurement, long-term correction, long-term reference and vertical extrapolation as "
                 "independent terms. Carrying the wind away from the mast to turbine positions adds a further term: see the "
                 "micrositing report.")
    doc.flag(NOTICE)

    # 2 introduction
    doc.h1(titles[1])
    doc.p("The report follows the steps of the analysis: the mast and its instruments, the checks on the data, the wind "
          "statistics of the measured period, the correction to the long term, the shear between the measured heights, "
          "and the uncertainty of the result.")
    doc.p("What this report is, and is not. " + SCOPE_NOT)
    doc.p(f"All figures come from Bohemian Wind v{__version__}. The checks and the statistics can be repeated from the logger "
          "files: the run folder keeps a fingerprint of every input file, the flags set on every record and the tables behind "
          "each figure (summary.xlsx).")

    # 3 site
    doc.h1(titles[2])
    doc.kv(src.position_rows() + ([("Terrain within the map area", f"{src.map['elev_min']:.0f} to {src.map['elev_max']:.0f} m above sea level; "
                                                                   f"{src.map['steep_share_pct']:.0f} % of the ground steeper than 30 %")] if src.map else []),
           caption="Position of the mast")
    if src.map and src.map.get("complex_terrain"):
        doc.flag(f"Complex terrain: {src.map.get('steep_share_mast_pct', 0):.0f} % of the ground within 3.5 km of the mast is steeper than "
                 "30 %. The wind measured at the mast stands for less of its surroundings than on gentle ground.", label="Check:")
    pc = (src.map or {}).get("position_check")
    if pc:
        doc.p(f"Position check: the mast's barometer reads {pc['measured_hpa']:.0f} hPa on average; the reanalysis air at the ground "
              f"elevation of these coordinates gives {pc['expected_hpa']:.0f} hPa (difference {pc['difference_hpa']:+.1f} hPa, tolerance "
              f"{pc['tolerance_hpa']:g}). " + (pc.get("caution") or "The coordinates and the elevation fit the measured pressure."))

    # 4 setup
    doc.h1(titles[3])
    st = src.sheet("Sensor table", "Sensors")
    if st is not None:
        head, rws = _df_rows(st, ["sensor", "kind", "height_m", "boom", "serial", "scale_factor", "offset", "units", "recovery_%"])
        doc.table(head, rws, caption="Instruments as the logger files describe them", size=7.5,
                      widths=[3.2, 2.6] + [1.45] * (len(head) - 2))
    else:
        doc.table(["Height, m", "Valid data, %"], [[f"{h['height_m']:g}", _f(h.get("recovery_pct"), 1)] for h in c.get("heights") or []],
                  caption="Wind speed heights")
    prog = src.sheet("Logger programs")
    if prog is not None and len(prog):
        head, rws = _df_rows(prog, ["program", "first_record", "last_record", "files", "added", "removed"])
        doc.table(head, rws, caption="Logger programs over the campaign", size=7.5)
    vis = src.sheet("Site visits")
    if vis is not None and len(vis):
        head, rws = _df_rows(vis)
        doc.table(head[:6], [r[:6] for r in rws], caption="Site visits and restarts seen in the files (records round them are flagged as maintenance)", size=7.5)
    doc.note("Sensor heights, boom directions, calibration and mounting are taken from the logger files as they are. They "
             "were not checked against an installation report or calibration certificates in this analysis.")

    # 5 data
    doc.h1(titles[4])
    arch = d.get("archives")
    doc.p(f"Logger format: {d.get('format', 'not recorded')}." + (f" {arch['logger_files']:,} logger files were read from {arch['opened']} of "
          f"{arch['found']} archives." if arch else "") + " Records that fail a check are flagged and left out of the statistics; "
          "nothing is deleted or filled in.")
    fl = d.get("flags") or []
    if fl:
        keys = [k for k in ("sensor", "records", "missing", "conflict", "maintenance", "no_signal", "range", "inconsistent", "stuck", "spike", "valid_%")
                if k in fl[0]]
        doc.table([k.replace("_", " ") for k in keys], [[(f"{r[k]:.1f}" if k == "valid_%" else f"{r[k]:,}" if isinstance(r[k], (int, float)) else r[k])
                                                         for k in keys] for r in fl], caption="Records flagged by each check, by sensor",
                  size=7, widths=[3.4] + [1.25] * (len(keys) - 1))
    doc.figure(fg.recovery(src.sheet("Recovery % by month")), "Valid data by sensor and month")
    pairs = src.pairs()
    if pairs:
        doc.h2("Tower shading by level")
        doc.p("Where a level carries two anemometers on opposite booms, their ratio by wind direction shows the mast's wake: the "
              "ratio leaves 1 in the directions where one of them stands behind the mast. In those directions the anemometer in "
              "the free wind is used; elsewhere the two are averaged.")
        wake = src.sheet("Mast wake")
        for h, df in pairs:
            marks = []
            if wake is not None and "height_m" in wake.columns and "observed_wake_deg" in wake.columns:
                for _, w in wake[wake["height_m"] == h].iterrows():
                    marks.append((w.get("observed_wake_deg"), f"{w.get('cup', '')} in the wake"))
            doc.figure(fg.shading(df, h, marks), f"Tower shading at {h:g} m: ratio of the two anemometers by wind direction")
        if wake is not None:
            head, rws = _df_rows(wake, ["cup", "height_m", "boom_bearing_deg", "expected_wake_deg", "observed_wake_deg", "wake_deficit_%",
                                        "implied_boom_bearing_deg", "verdict"], nd=1)
            doc.table(head, rws, caption="Mast wake seen in the data, by anemometer", size=7.5, widths=[3.4] + [1.8] * (len(head) - 1))
    ser = src.series
    if ser is not None and ser.shape[1] >= 2:
        doc.h2("Height against height")
        doc.p("Each measured height set against the one below it, record by record. A tight cloud along a straight line says the "
              "two levels saw the same wind; the ratio is the mean shear between them.")
        cols = sorted(ser.columns, key=lambda c: -float(re.sub(r"[^\d.]", "", c) or 0))
        for a, b in zip(cols[:-1], cols[1:]):
            ha, hb = (re.sub(r"[^\d.]", "", c) for c in (a, b))
            doc.figure(fg.scatter(ser[b], ser[a], f"Wind speed at {hb} m, m/s", f"Wind speed at {ha} m, m/s"),
                       f"Wind speed at {ha} m against {hb} m, ten-minute records", 9.0)
    hs = src.sheet("Heights")
    if hs is not None:
        head, rws = _df_rows(hs, ["height_m", "valid_records", "recovery_%", "mean_all_valid", "mean_ws", "momm_months_70pct", "momm_ws",
                                  "months_used", "weibull_A", "weibull_k", "max_10min_mean"])
        doc.table(head, rws, caption="Wind speed by height: valid data, means and Weibull fit", size=7.5)
    else:
        doc.table(["Height, m", "Valid data, %", "Mean, m/s", "Mean of monthly means, m/s", "Weibull A, m/s", "Weibull k"],
                  [[f"{h['height_m']:g}", _f(h.get("recovery_pct"), 1), _f(h.get("mean_ws")), _f(h.get("momm_ws")), _f(h.get("weibull_A")),
                    _f(h.get("weibull_k"))] for h in c.get("heights") or []], caption="Wind speed by height")

    # 6 statistics
    doc.h1(titles[5])
    doc.figure(fg.distribution(c["weibull_A"], c["weibull_k"], src.speeds, top), f"Wind speed distribution at {top:g} m with its Weibull fit", 13.5)
    if not c.get("no_direction"):
        doc.figure(fg.rose(c["sectors"], "freq"), f"Wind direction: share of the time, {top:g} m", 8.5)
        doc.figure(fg.rose(c["sectors"], "energy"), f"Wind direction: share of the wind energy, {top:g} m", 8.5)
        doc.table(["Direction, °", "Share of time, %", "Mean, m/s", "Weibull A, m/s", "Weibull k", "Records"],
                  [[f"{s['centre']:.0f}", f"{100 * s['freq']:.1f}", _f(s.get("mean")), f"{s['A']:.2f}", f"{s['k']:.2f}", f"{s['records']:,}"]
                   for s in c["sectors"]], caption=f"Wind climate by direction sector at {top:g} m", numeric_from=0)
    else:
        doc.flag("No wind direction was measured: no rose is given, and every direction is treated alike in later steps.", label="Check:")
    months = d.get("monthly") or [{"month": k, **v} for k, v in sorted((c.get("monthly") or {}).items())]
    doc.figure(fg.monthly(months, (lt or {}).get("monthly")), f"Monthly mean wind speed at {top:g} m")
    doc.table(["Month", "Mean, m/s", "Valid data, %"], [[m["month"], f"{m['mean']:.2f}", f"{100 * m['share']:.0f}"] for m in months],
              caption=f"Monthly mean wind speed at {top:g} m", widths=[4, 4, 4])
    di = src.sheet("Diurnal WS")
    if di is not None:
        doc.figure(fg.lines(di, "Mean wind speed, m/s", "Hour of day (logger time)"), "Mean wind speed by hour of day and height")

    # 7 turbulence, density
    doc.h1(titles[6])
    ti = src.sheet("TI ")
    t15 = (c.get("ti") or {}).get("representative_15")
    doc.p(f"Turbulence intensity is the standard deviation of the wind speed within ten minutes over its mean. Its mean above 3 m/s "
          f"is {_f((c.get('ti') or {}).get('mean_above_3'), 3)}; the representative value at 15 m/s is {_f(t15, 3)}."
          + (" The curves of the reference turbulence classes are drawn for orientation only: choosing a turbine class is a matter for "
             "the turbine supplier's site assessment." if ti is not None else ""))
    doc.figure(fg.turbulence(ti), "Turbulence intensity by wind speed at the top height")
    dn = c.get("density") or {}
    doc.kv([("Mean air density at the sensors", f"{_f(dn.get('mean'), 3)} kg/m³" + (f" (sensors at {dn['sensor_height_m']:g} m)" if dn.get("sensor_height_m") else "")),
            ("Mean temperature", f"{_f(dn.get('temperature_c'), 1)} °C"),
            ("Mean pressure", f"{_f(dn.get('pressure_hpa'), 0)} hPa" + (f" ({dn['pressure_valid_pct']:.0f} % valid)" if dn.get("pressure_valid_pct") is not None else ""))],
           caption="Air density and climate measured at the mast")
    ad = src.sheet("Air density")
    if ad is not None:
        head, rws = _df_rows(ad, nd=3)
        doc.table(head, rws, caption="Monthly air density, kg/m³", size=7.5)

    # 8 long term
    doc.h1(titles[7])
    _long_term(doc, src)

    # 9 shear
    doc.h1(titles[8])
    sh = c.get("shear") or {}
    doc.p((f"The shear exponent used to carry the wind to other heights is {alpha:.3f}: {alpha_note}." if alpha is not None else
           "Wind speed was measured at one height only: no shear could be measured.")
          + (f" Fitted over all measured heights, the exponent is {sh['alpha_all_heights']:.3f}." if sh.get("alpha_all_heights") is not None else ""))
    if sh.get("warning"):
        doc.flag(sh["warning"], label="Check:")
    sp = src.sheet("Shear")
    if sp is not None and "pair" in sp.columns:
        head, rws = _df_rows(sp, nd=3)
        doc.table(head, rws, caption="Shear exponent between pairs of heights", size=7.5)
    doc.figure(fg.profile(c.get("heights") or [], alpha, mean, top), "Measured profile and the power law used above the mast", 10.5)
    sbh = src.sheet("Shear by hour")
    if sbh is not None:
        doc.figure(fg.lines(sbh, "Shear exponent", "Hour of day (logger time)", suffix=""), "Shear exponent by hour of day")
    stt = src.sheet("Shear test")
    if stt is not None:
        head, rws = _df_rows(stt, ["from_m", "to_m", "train_records", "test_records", "alpha_train", "alpha_test", "MAE_mps", "bias_mps", "mean_error_%"], nd=3)
        doc.table(head, rws, caption="Test of the power law: shear from the earlier records, checked on the later ones", size=7.5)
    if hub:
        doc.table(["Height above ground", "Long-term mean wind speed, m/s", "Vertical extrapolation, % of speed", "Total, % of speed (1 σ)"],
                  hub, widths=[4.6, 4.2, 3.8, 3.3], caption="Long-term mean wind speed by hub height")

    # 10 uncertainty
    doc.h1(titles[9])
    from ..energy import budget  # noqa: PLC0415
    ltp, why = budget.long_term_pct(c.get("calendar_months"), corrected=bool(lt), months=len(c.get("monthly") or {}))
    doc.p("The uncertainty of the long-term mean wind speed is built from independent terms, each one standard deviation in per cent "
          "of the wind speed. They are the developer's working assumptions, each with its reason; they are not derived from "
          "calibration certificates or from a standard's procedure.")
    urows = [["Measurement", f"{_meas(src)[0]:.1f}", _meas(src)[1]],
             ["Long-term correction", f"{ltp:.1f}", why]]
    if lt:
        urows.append(["Long-term reference", f"{_ref_pct(src):.1f}", "half the distance between the two references' long-term means, at least "
                      f"{budget.REFERENCE_TWO_FLOOR_PCT:g} %" if lt.get("second") else "one reanalysis, not checked against a second"])
    urows.append(["Vertical extrapolation", "by height", f"{budget.V_BASE_PCT:g} % within the measured heights, more for every 10 m above the top anemometer"])
    doc.table(["Term", "% of wind speed", "Basis"], urows, widths=[4.2, 2.6, 9.1], caption="Uncertainty terms of the wind speed at the mast")

    # 11 findings
    doc.h1(titles[10])
    found = [x for x in (d.get("findings") or []) if "not an accredited" not in x]
    doc.p("The data checks report what a reader should know before relying on the statistics. They are listed as the run gave them.")
    doc.bullets(found[:60] or ["No findings were recorded."])

    # 12 departures
    doc.h1(titles[11])
    doc.bullets([
        "Calibration: sensors are used with the scale and offset in the logger files. Calibration certificates were not reviewed, and no "
        "post-calibration or in-situ comparison against certificates was made.",
        "Mounting: boom lengths, orientations and the mast's cross-section were not reviewed against the measurement standard. Where a "
        "height has two anemometers, the one out of the mast's wake is used by direction; no further flow-distortion correction is applied.",
        "Site visit: the analysis rests on the files alone. The installation report and the maintenance log were not reviewed here.",
        ("Long term: two reanalysis references and one monthly method; no nearby long-term station." if (lt or {}).get("second") else
         "Long term: one reanalysis reference and one monthly method; no second reference, no nearby long-term station."),
        "Uncertainty: working assumptions by term, not a certificate-based budget.",
        "Review: generated by software and not yet reviewed by a second person (see document control).",
    ])
    doc.p("An independent assessor would close each of these points before a financing decision.")

    # 13 sources
    doc.h1(titles[12])
    credits = list(dict.fromkeys(([lt["credit"]] if lt and lt.get("credit") else []) + ((src.map or {}).get("credits") or [])))
    doc.bullets([f"Mast data: {d.get('format', 'logger files')}, supplied for this project. Input fingerprint and file list: inputs.csv in the run folder."]
                + credits + [f"Software: Bohemian Wind v{__version__}, open source (MIT licence)."])
    doc.flag(NOTICE)
    return doc.save(path)


def _wind_resource_from_tables(src: Source, path, date: datetime | None = None) -> Path:
    """The wind resource analysis of a project whose step 1 read monthly summary tables (intake/summary.py): the
    same order as the full report, without the sections only ten-minute records can fill, and with what the tables
    cannot give said where it matters."""
    from ..energy import budget  # noqa: PLC0415
    c, d, lt, wm, t = src.clim, src.data, src.lt, src.map, _tables(src.clim)
    date = date or datetime.now()
    top = c["height_m"]
    alpha, alpha_note = src.alpha()
    mean, mean_what = src.lt_mean()
    doc = Report("Wind Resource Analysis", src.name, f"BR-WRA-{_slug(src.name)}-{date:%Y%m%d}",
                 ["First view from monthly summary tables", f"Reported period {_day(c['period'][0])} to {_day(c['period'][1])}"], date)
    doc.control(basis=_basis(src))
    titles = ["Summary", "What this analysis rests on", "Site and mast", "The reported data", "Wind statistics", "Long-term correction",
              "Wind shear and hub heights", "The wind over the site", "Uncertainty", "Findings", "What is needed to firm this up", "Data sources"]
    doc.contents(titles)
    months = d.get("monthly") or [{"month": k, **v} for k, v in sorted((c.get("monthly") or {}).items())]
    usable = sum(1 for m in months if m["share"] >= 0.7)

    # 1 summary
    doc.h1(titles[0])
    doc.p(f"This report gives a first view of the wind at the met mast of {src.name}. It is written from monthly summary tables "
          f"({t['source']}), not from the mast's ten-minute records. The top anemometer stands at {top:g} m; the tables cover "
          f"{_day(c['period'][0])} to {_day(c['period'][1])}, with valid data for {c['recovery_pct']:.0f} % of that time.")
    rows = src.position_rows() + [
        ("Reported period", f"{_day(c['period'][0])} to {_day(c['period'][1])} ({len(months)} months touched, {usable} with at least 70 % valid data; "
                            f"{c['calendar_months']} of the 12 calendar months)"),
        (f"Mean wind speed at {top:g} m, reported", f"{c['mean_ws']:.2f} m/s over the valid time"
         + (f"; {c['momm_ws']:.2f} m/s as the mean of monthly means" if c.get("momm_ws") else "")),
        (f"Mean wind speed at {top:g} m, long term", f"{mean:.2f} m/s ({mean_what})"),
    ]
    if lt:
        rows += [("Long-term factor", f"{lt['factor']:.3f} ({100 * (lt['factor'] - 1):+.1f} %), from {lt['months']} months"),
                 ("Correlation with the reference", f"monthly r = {lt['r']:.2f}; reference years {lt['lt_years'][0]} to {lt['lt_years'][1]}")]
        if lt.get("second"):
            rows.append(("Second reference", f"{lt['second']['long_term']:.2f} m/s ({lt['second']['difference_pct']:+.1f} % from the first)"))
    rows += [("Weibull shape factor", f"k = {c['weibull_k']:.2f} ({t['weibull_k_basis']}); A = {c['weibull_A']:.2f} m/s follows from the mean"),
             ("Shear exponent", f"{alpha:.3f} ({(c.get('shear') or {}).get('basis') or alpha_note})" if alpha is not None else "not measured"),
             ("Wind directions", "not given" if c.get("no_direction") else f"not measured; taken from {t['direction']}")]
    if not c.get("no_direction"):
        best = max(c["sectors"], key=lambda s: s["freq"] * s["A"] ** 3)
        rows.append(("Main direction of the wind energy", f"{best['centre']:.0f}° ({100 * best['freq']:.0f} % of the time), by the reanalysis"))
    doc.kv(rows, caption="Overview of results")
    hub = _hub_table(src)
    if hub:
        doc.table(["Height above ground", "Long-term mean wind speed, m/s", "Vertical extrapolation, % of speed", "Total, % of speed (1 σ)"],
                  hub, widths=[4.6, 4.2, 3.8, 3.3], caption="Long-term mean wind speed by hub height, with its uncertainty")
    doc.flag("First view from summary tables. No ten-minute record was checked; the distribution's shape is one entered number; "
             + ("the wind directions are a reanalysis's, not the site's. " if not c.get("no_direction") else "no direction is known. ")
             + "The figures show whether the site is worth the next step, and are to be redone from the records.", label="Check:")
    doc.flag(NOTICE)

    # 2 basis
    doc.h1(titles[1])
    doc.p("A full wind resource analysis starts from the logger's ten-minute records, checks every record, and derives the wind statistics "
          "from the records that pass. Here only monthly tables were at hand: a mean wind speed for each height and month, and the share "
          "of each month that was valid. The table below sets out what each result rests on.")
    doc.table(["Result", "Rests on", "Not available from tables"],
              [["Monthly and overall mean wind speed", "the reports' monthly means, weighted by their valid time", "checks of single records: sensor faults, icing, tower shading, clock errors"],
               ["Long-term correction", "the monthly means against a reanalysis, month by month", "daily or hourly correlation"],
               ["Shear", "the ratio of the monthly means between heights", "shear by hour of day and by direction; a test on held-back records"],
               ["Wind speed distribution", f"one Weibull shape factor, k = {c['weibull_k']:.2f}: {t['weibull_k_basis']}", "the measured histogram; a fit by direction"],
               ["Wind rose", ("none" if c.get("no_direction") else f"{t['direction']}: sector frequencies and each sector's mean in proportion"), "the directions at the mast"],
               ["Turbulence, air density", "not reported", "turbulence intensity; measured temperature and pressure"]],
              widths=[3.9, 6.4, 5.6], caption="What each result rests on", numeric_from=None, size=8)
    doc.p("What this report is, and is not. " + SCOPE_NOT)

    # 3 site
    doc.h1(titles[2])
    doc.kv(src.position_rows() + ([("Position taken from", t["position_basis"])] if t.get("position_basis") else [])
           + ([("Terrain within the map area", f"{wm['elev_min']:.0f} to {wm['elev_max']:.0f} m above sea level; "
                                               f"{wm['steep_share_pct']:.0f} % of the ground steeper than 30 %")] if wm else []),
           caption="Position of the mast")
    if wm and wm.get("complex_terrain"):
        doc.flag(f"Complex terrain: {wm.get('steep_share_mast_pct', 0):.0f} % of the ground within 3.5 km of the mast is steeper than "
                 "30 %. The wind measured at the mast stands for less of its surroundings than on gentle ground.", label="Check:")

    # 4 data
    doc.h1(titles[3])
    doc.p(f"Source of the tables: {t['source']}. The means are used as reported. Overlapping report periods were joined into calendar "
          "months before they were entered; a month counts towards the monthly means and the long-term correction when at least 70 % of it is valid.")
    mt = src.sheet("Monthly means")
    if mt is not None:
        head, rws = _df_rows(mt)
        doc.table(head, rws, caption="Monthly mean wind speed by height, m/s, as entered from the reports", size=8)
    else:
        doc.table(["Month", "Mean, m/s", "Valid data, %"], [[m["month"], f"{m['mean']:.2f}", f"{100 * m['share']:.0f}"] for m in months],
                  caption=f"Monthly mean wind speed at {top:g} m", widths=[4, 4, 4])
    doc.figure(fg.monthly(months, (lt or {}).get("monthly")), f"Monthly mean wind speed at {top:g} m")
    doc.table(["Height, m", "Valid time, % of the period", "Mean over the valid time, m/s", "Mean of monthly means, m/s"],
              [[f"{h['height_m']:g}", _f(h.get("recovery_pct"), 1), _f(h.get("mean_ws")), _f(h.get("momm_ws")) if h.get("momm_ws") else "not all 12 calendar months"]
               for h in c.get("heights") or []], caption="Wind speed by height", widths=[2.6, 4.2, 4.6, 4.5])

    # 5 statistics
    doc.h1(titles[4])
    doc.p(f"The wind speed distribution is drawn from the mean at {top:g} m and the shape factor k = {c['weibull_k']:.2f} ({t['weibull_k_basis']}). "
          "It is not a fit to measured records.")
    doc.figure(fg.distribution(c["weibull_A"], c["weibull_k"], None, top, label="Weibull distribution from the mean and the entered shape"),
               f"Wind speed distribution at {top:g} m, from the mean and the entered shape factor", 13.5)
    if not c.get("no_direction"):
        doc.p(f"The mast's wind directions were not available. The rose below is that of {t['direction']}; each sector's mean wind speed is the "
              f"mast's mean in the proportion the reanalysis gives. On ridges and escarpments the terrain turns and channels the wind, so the "
              "real rose at the mast may differ.")
        doc.figure(fg.rose(c["sectors"], "freq"), "Wind direction: share of the time (reanalysis, not measured)", 8.5)
        doc.figure(fg.rose(c["sectors"], "energy"), "Wind direction: share of the wind energy (reanalysis directions, mast's mean)", 8.5)
        doc.table(["Direction, °", "Share of time, %", "Mean, m/s", "Weibull A, m/s", "Weibull k"],
                  [[f"{s['centre']:.0f}", f"{100 * s['freq']:.1f}", _f(s.get("mean")), f"{s['A']:.2f}", f"{s['k']:.2f}"] for s in c["sectors"]],
                  caption=f"Wind climate by direction sector carried into the later steps, {top:g} m", numeric_from=0)
        doc.note("Each sector carries the same shape factor, set so that all sectors together are as wide as the entered k.")
    else:
        doc.flag("No wind direction is known: every direction is treated alike in later steps.", label="Check:")

    # 6 long term
    doc.h1(titles[5])
    _long_term(doc, src)

    # 7 shear
    doc.h1(titles[6])
    sh = c.get("shear") or {}
    doc.p((f"The shear exponent used to carry the wind to other heights is {alpha:.3f}: {alpha_note}." if alpha is not None else
           "Wind speed was reported at one height only: no shear could be derived.")
          + (f" Fitted over all reported heights, the exponent is {sh['alpha_all_heights']:.3f}." if sh.get("alpha_all_heights") is not None else "")
          + " It comes from the ratio of mean wind speeds between heights over the months in which all heights were reported.")
    if sh.get("warning"):
        doc.flag(sh["warning"], label="Check:")
    doc.figure(fg.profile(c.get("heights") or [], alpha, mean, top), "Reported profile and the power law used above the mast", 10.5)
    if hub:
        doc.table(["Height above ground", "Long-term mean wind speed, m/s", "Vertical extrapolation, % of speed", "Total, % of speed (1 σ)"],
                  hub, widths=[4.6, 4.2, 3.8, 3.3], caption="Long-term mean wind speed by hub height")
    doc.note("A mean shear hides its daily swing: at many sites the wind rises faster with height at night than by day. With the "
             "records, the shear is taken by hour and direction and tested on held-back data.")

    # 8 map
    doc.h1(titles[7])
    if wm:
        doc.p(f"The mast's long-term wind was carried over {wm['area_km2']:.0f} km² of terrain with {ENGINES.get(wm['engine'], wm['engine'])}. At "
              f"{wm['height_m']:g} m above ground the mean wind speed runs from {wm['min_ws']:.1f} to {wm['max_ws']:.1f} m/s over the map. "
              "The classed map is also written as a Google Earth file (wind_map.kmz) with its key and the mast.")
        doc.figure(src.p.folder / wm["map_print"] if wm.get("map_print") else None, f"Mean wind speed at {wm['height_m']:g} m above ground", 15.5)
        if wm.get("warnings"):
            doc.flag(list(wm["warnings"]), label="Check:")
        doc.note("The map is as good as its anchor: one mast, known here from monthly means, at a position that is "
                 + ("stated as: " + t["position_basis"] + "." if t.get("position_basis") else "taken as given."))
    else:
        doc.p("No wind map has been made yet (step 3).")

    # 9 uncertainty
    doc.h1(titles[8])
    ltp, why = budget.long_term_pct(c.get("calendar_months"), corrected=bool(lt), months=len(c.get("monthly") or {}))
    doc.p("The uncertainty of the long-term mean wind speed is built from independent terms, each one standard deviation in per cent "
          "of the wind speed. They are the developer's working assumptions. The measurement term is larger than for a mast analysed "
          "from its records, because nothing behind the monthly means could be checked.")
    urows = [["Measurement", f"{_meas(src)[0]:.1f}", _meas(src)[1]], ["Long-term correction", f"{ltp:.1f}", why]]
    if lt:
        urows.append(["Long-term reference", f"{_ref_pct(src):.1f}", "half the distance between the two references' long-term means, at least "
                      f"{budget.REFERENCE_TWO_FLOOR_PCT:g} %" if lt.get("second") else "one reanalysis, not checked against a second"])
    urows.append(["Vertical extrapolation", "by height", f"{budget.V_BASE_PCT:g} % within the reported heights, more for every 10 m above the top anemometer"])
    doc.table(["Term", "% of wind speed", "Basis"], urows, widths=[4.2, 2.6, 9.1], caption="Uncertainty terms of the wind speed at the mast")
    doc.note("Not in the table: the error of a wrong mast position, and of a wind rose that is not the site's. Both act on the map and on "
             "any energy estimate, not on the mean at the mast.")

    # 10 findings
    doc.h1(titles[9])
    doc.bullets([x for x in (d.get("findings") or []) if "not an accredited" not in x][:60] or ["No findings were recorded."])

    # 11 next
    doc.h1(titles[10])
    doc.bullets(["The ten-minute records of every channel, with the standard deviations, and the logger's own files and program.",
                 "The installation report: surveyed coordinates, sensor heights, boom lengths and bearings, the mast's cross-section.",
                 "The calibration certificates and the maintenance log, with the cause of every gap.",
                 "A wind vane, a thermometer and a barometer on the mast if it has none: direction, air density and stability cannot be measured without them.",
                 "Twelve months in a row with good recovery; until then the seasons not measured rest on the reanalysis.",
                 "Then: step 1 of Bohemian Wind on the records, and this report written again from them."])
    doc.p("An independent assessor would ask for the same before a financing decision.")

    # 12 sources
    doc.h1(titles[11])
    credits = list(dict.fromkeys(([lt["credit"]] if lt and lt.get("credit") else []) + ((wm or {}).get("credits") or [])))
    doc.bullets([f"Mast data: {t['source']}, as monthly summary tables. The file entered and its fingerprint: inputs.csv in the run folder."]
                + ([f"Wind directions: {t['direction']}."] if not c.get("no_direction") else [])
                + ([f"Second long-term reference: {lt['second']['what']}."] if (lt or {}).get("second") else [])
                + credits + [f"Software: Bohemian Wind v{__version__}, open source (MIT licence)."])
    doc.flag(NOTICE)
    return doc.save(path)


# ---------------------------------------------------------------- micrositing

def _energy_rows(lay: dict) -> list:
    u = lay["uncertainty"]
    return [("Gross energy (no wakes, no losses)", f"{lay['gross_gwh']:.1f} GWh a year"),
            ("Wake loss", f"{lay['wake_loss_pct']:.1f} % ({lay['wake_model_name']})"),
            ("Other losses", f"{lay['other_loss_pct']:.1f} %"),
            ("Net energy, P50", f"{lay['p50_gwh']:.1f} GWh a year"),
            ("Capacity factor; full-load hours", f"{100 * lay['capacity_factor']:.1f} %; {lay.get('full_load_hours', 0):,.0f} h"),
            ("Uncertainty of the energy (1 σ)", f"{u['sigma_pct']['10_year']:.1f} % over 10 years; {u['sigma_pct']['1_year']:.1f} % for a single year"),
            ("P75 / P90, 10-year average", f"{u['p75_10_year']:.1f} / {u['p90_10_year']:.1f} GWh a year"),
            ("P90, any single year", f"{u['p90_1_year']:.1f} GWh")]


def micrositing(project, path, date: datetime | None = None) -> Path:
    src = Source(project)
    if not src.lay:
        raise ValueError("Run step 4 (layout and energy) first: the micrositing report is written from its results.")
    lay, wm, c = src.lay, src.map or {}, src.clim
    t = lay["turbine"]
    date = date or datetime.now()
    f = project.folder
    doc = Report("Micrositing and Energy Estimate", src.name, f"BR-MIC-{_slug(src.name)}-{date:%Y%m%d}",
                 [f"{lay['n_placed']} turbines, {lay['capacity_mw']:.1f} MW (generic turbine class)"], date)
    doc.control(basis=_basis(src))
    titles = ["Summary", "Basis: the wind at the mast", "Terrain and wind map", "Siting rules and buildable area", "Layout",
              "Energy estimate", "Uncertainty and exceedance levels", "Noise and shadow flicker screening", "Points to check", "Files and data sources"]
    doc.contents(titles)

    doc.h1(titles[0])
    doc.p(f"This report sets out an indicative wind farm layout for {src.name} and its energy estimate. The wind measured at the met mast "
          f"is carried over the terrain with {ENGINES.get(lay.get('engine'), lay.get('engine'))}; turbines are placed inside the area the "
          "siting rules leave, and wakes, losses and uncertainty are applied.")
    doc.kv([("Turbines", f"{lay['n_placed']} × {t['rated_kw'] / 1000:g} MW, rotor {t['rotor_m']:g} m, hub height {t['hub_m']:g} m ({t['name']})"),
            ("Installed capacity", f"{lay['capacity_mw']:.1f} MW")] + _energy_rows(lay)
           + [("Mean free wind speed at the turbines", f"{lay['mean_free_ws']:.2f} m/s at {t['hub_m']:g} m (mast: {lay['mast_mean_ws']:.2f} m/s)")],
           caption="Overview of results")
    doc.flag([NOTICE, "The turbine is a generic machine of its size: no manufacturer's power or thrust curve was used, and no make or "
                      "model is chosen. Turbine suitability for the site (loads, turbulence, extreme wind) is outside this report."])

    doc.h1(titles[1])
    mean, what = src.lt_mean()
    doc.p(f"The layout rests on the wind climate of the met mast: {mean:.2f} m/s at {c['height_m']:g} m ({what}), measured "
          f"{_day(c['period'][0])} to {_day(c['period'][1])} with {c['recovery_pct']:.1f} % valid data. The measurement, its checks and the "
          "long-term correction are set out in the wind resource analysis report of the same date.")
    doc.figure(fg.rose(c["sectors"], "energy"), f"Wind energy by direction at the mast, {c['height_m']:g} m", 8.5)
    doc.bullets([n for n in lay.get("notes", [])])

    doc.h1(titles[2])
    if wm:
        doc.kv([("Flow model", ENGINES.get(wm["engine"], wm["engine"])), ("Map height", f"{wm['height_m']:g} m above ground"),
                ("Area and cell size", f"{wm['area_km2']:.0f} km², {wm['cell_m']:g} m cells"),
                ("Ground elevation", f"{wm['elev_min']:.0f} to {wm['elev_max']:.0f} m; mast at {wm['mast_elev']:.0f} m"),
                ("Ground steeper than 30 %", f"{wm['steep_share_pct']:.0f} % of the map" + (f"; {wm['steep_share_mast_pct']:.0f} % within 3.5 km of the mast"
                                                                                            if wm.get("steep_share_mast_pct") is not None else "")),
                ("Mean wind speed on the map", f"{wm['min_ws']:.1f} to {wm['max_ws']:.1f} m/s; {wm['mast_mean_ws']:.2f} m/s at the mast")],
               caption="Terrain and wind map")
        doc.figure(f / wm["map_print"] if wm.get("map_print") else None, f"Mean wind speed at {wm['height_m']:g} m, in fixed wind speed classes", 15.5)
        doc.note("The same map is in the Google Earth file (.kmz) of the project, with the key to the classes and the energy rose.")
        doc.bullets(wm.get("notes") or [])
    doc.p("A flow model of this kind is a screening tool. It was not validated against a second mast on this site.")

    doc.h1(titles[3])
    doc.table(["Rule", "Area removed, km²", "Area left, km²"], [[s["rule"], f"{s['removed_km2']:.2f}", f"{s['left_km2']:.2f}"] for s in lay["constraints"]["steps"]],
              widths=[9.5, 3.2, 3.2], caption="Siting rules, in the order applied")
    doc.p(f"Buildable area after all rules: {lay['constraints']['buildable_km2']:.1f} km². The rules and distances are the developer's own "
          "working values; they are to be confirmed against the requirements that apply to the site, the land agreements and the "
          "environmental and social assessment.")

    doc.h1(titles[4])
    sp = lay.get("spacing") or {}
    doc.p(("The positions were given, not designed. " if lay.get("given_layout") else
           f"Turbines were placed on the windiest buildable ground, at least {sp.get('cross_d', 0):g} rotor diameters apart across the main wind "
           f"direction ({lay.get('prevailing_deg', 0):.0f}°) and {sp.get('along_d', 0):g} along it. ")
          + (f"The closest pair stands {sp['closest_pair_d']:.1f} rotor diameters apart." if sp.get("closest_pair_d") else ""))
    doc.figure(f / lay["map_print"] if lay.get("map_print") else None, "Layout over the wind map; the bright area is buildable", 15.5)
    doc.table(["Turbine", "Latitude", "Longitude", "UTM zone", "Easting, m", "Northing, m", "Ground, m", "Tip, m a.s.l."],
              [[p["id"], f"{p['lat']:.6f}", f"{p['lon']:.6f}", p["utm_zone"], f"{p['easting']:.0f}", f"{p['northing']:.0f}",
                f"{p['ground_elev_m']:.0f}", f"{p['tip_amsl_m']:.0f}"] for p in lay["positions"]],
              caption="Turbine positions (WGS 84)", size=7.5)

    doc.h1(titles[5])
    doc.kv(_energy_rows(lay)[:5], caption="From gross to net energy")
    doc.figure(fg.cascade(lay["gross_gwh"], lay.get("after_wake_gwh", lay["gross_gwh"] * (1 - lay["wake_loss_pct"] / 100)), lay["p50_gwh"]),
               "Gross energy, energy after wakes, and net energy", 10.5)
    doc.table(["Group", "Loss", "%", "Basis"], [[x["group"], x["item"], f"{x['pct']:.1f}", x["basis"]] for x in lay.get("losses") or []],
              widths=[3.0, 4.6, 1.2, 7.1], caption="Losses other than wakes (developer assumptions)", numeric_from=2)
    doc.table(["Turbine", "Free wind, m/s", "Gross, MWh", "Wake loss, %", "Net P50, MWh", "Capacity factor, %", "From mast, km", "Energy σ, %"],
              [[p["id"], f"{p['free_ws']:.2f}", f"{p['gross_mwh']:,.0f}", f"{p['wake_loss_pct']:.1f}", f"{p['p50_mwh']:,.0f}",
                f"{100 * p['capacity_factor']:.1f}", f"{p['dist_mast_km']:.1f}", _f(p.get("sigma_energy_pct"), 1) if p.get("sigma_energy_pct") is not None else ""]
               for p in lay["positions"]], caption="Energy by turbine", size=7.5, widths=[1.9] + [2.0] * 7)
    doc.figure(fg.turbine_bars(lay["positions"]), "Net energy and free wind speed by turbine")
    wc = lay.get("wake_checks") or {}
    if wc:
        doc.table(["Wake model", "Wake loss, %", "Net P50, GWh a year"],
                  [[f"{lay['wake_model_name']} (used)", f"{lay['wake_loss_pct']:.1f}", f"{lay['p50_gwh']:.1f}"]]
                  + [[k, f"{v['wake_loss_pct']:.1f}", f"{v['p50_gwh']:.1f}"] for k, v in wc.items()],
                  widths=[8, 4, 4], caption="The same layout with other wake models")
    mc = lay.get("mast_wind_ceiling")
    if mc:
        doc.p(f"How much of the estimate rests on positions windier than the mast: with no position given more wind than the mast measured, "
              f"the net energy would be {mc['p50_gwh']:.1f} GWh a year ({mc['change_pct']:+.1f} %). The flow model's speed-ups are its least "
              "certain part; this figure is the cautious reading.")

    doc.h1(titles[6])
    u = lay["uncertainty"]
    doc.table(["Term", "% of energy (1 σ)"], [[k, f"{v:.1f}"] for k, v in sorted(u["terms_energy_pct"].items(), key=lambda kv: -kv[1])],
              widths=[12.4, 3.5], caption="Uncertainty terms, converted to energy")
    doc.figure(fg.budget(u["terms_energy_pct"]), "Uncertainty terms, largest first")
    doc.p(f"One per cent of wind speed changes the energy of this layout by {u['energy_per_speed']:.2f} per cent. The terms are taken as "
          f"independent. The wind from year to year varies by about {u['iav_speed_pct']:g} % of the annual mean, which widens the figure for a "
          "single year and for a ten-year average.")
    doc.table(["Level", "Long term", "10-year average", "Any single year"],
              [["Standard deviation, %"] + [f"{u['sigma_pct'][k]:.1f}" for k in ("long_term", "10_year", "1_year")],
               ["P50, GWh a year"] + [f"{u['p50']:.1f}"] * 3,
               ["P75, GWh a year"] + [f"{u[f'p75_{k}']:.1f}" for k in ("long_term", "10_year", "1_year")],
               ["P90, GWh a year"] + [f"{u[f'p90_{k}']:.1f}" for k in ("long_term", "10_year", "1_year")]],
              caption="Exceedance levels of the net energy")
    doc.figure(fg.exceedance(u["p50"], u["sigma_pct"]), "Net energy against the probability of exceeding it", 11.5)
    doc.flag("The uncertainty terms are a developer's working assumptions. They are not the result of an accredited assessment, and "
             "the P75 and P90 figures are not to be used in a financing model.")

    doc.h1(titles[7])
    env = src.env
    if env:
        nz, fl = env["noise"], env["flicker"]
        doc.kv([("Homes assessed", f"{nz['n_receptors']:,}"), ("Nearest home to a turbine", f"{nz.get('nearest_home_m', 0):,.0f} m"),
                ("Noise: level used", f"{nz['limit_dba']:g} dB(A); sound power {nz['sound_power_dba']:g} dB(A) per turbine"),
                ("Noise: homes above the level", f"{nz['over_limit']:,} ({nz['over_limit_with_uncertainty']:,} with a {nz['uncertainty_db']:g} dB allowance); highest {nz['highest_dba']:.1f} dB(A)"),
                ("Shadow flicker: limits used", f"{fl['limit_hours_year']:g} hours a year or {fl['limit_min_day']:g} minutes a day, astronomical worst case"),
                ("Shadow flicker: homes", f"{fl['with_any_flicker']:,} could see flicker; {fl['over_limit']:,} above a limit; highest {fl['highest_hours']:.0f} hours a year")],
               caption="Noise and shadow flicker screening")
        doc.figure(f / env["noise_map_print"] if env.get("noise_map_print") else None, "Noise screening", 14)
        doc.figure(f / env["flicker_map_print"] if env.get("flicker_map_print") else None, "Shadow flicker screening, worst case", 14)
        doc.note(f"Methods: {nz['method']}. {fl['method']}. These are screening calculations with a generic turbine's sound power; they do not "
                 "replace the noise and flicker studies an authority may ask for.")
        if env.get("cautions"):
            doc.flag(env["cautions"], label="Check:")
    else:
        doc.p("Not included: the noise and shadow flicker screening (step 5) has not been run for this layout, or is out of date.")

    doc.h1(titles[8])
    checks = list(dict.fromkeys((lay.get("warnings") or []) + (wm.get("warnings") or [])))
    if checks:
        doc.flag(checks, label="Check:")
    doc.bullets(["The flow model is a screening tool and was not validated against a second mast.",
                 "The turbine is generic. A manufacturer's power curve, thrust curve, sound power and site suitability assessment are still needed.",
                 "Siting rules, land, environmental and social constraints, aviation and grid are the developer's working assumptions.",
                 "An independent energy yield assessment is needed before any financing decision."])

    doc.h1(titles[9])
    files = [("reports/", "This report, the wind resource analysis and the project wind summary (Word)"),
             (wm.get("kmz") or "maps/wind_map.kmz", "Wind map for Google Earth, with key and energy rose"),
             (lay.get("kmz") or "layout/layout.kmz", "The same with the turbines and their figures"),
             (lay.get("csv") or "layout/turbines.csv", "Turbine positions and energy by turbine"),
             ("permitting/", "Permitting support pack, once step 6 has been run")]
    doc.table(["In the project folder", "What it holds"], [list(x) for x in files], widths=[5.5, 10.4], caption="Files that go with this report", numeric_from=None)
    credits = list(dict.fromkeys((wm.get("credits") or []) + ([src.lt["credit"]] if src.lt and src.lt.get("credit") else [])))
    doc.bullets(credits + [f"Software: Bohemian Wind v{__version__}, open source (MIT licence); wake models through PyWake (DTU Wind Energy, MIT licence)."])
    doc.flag(NOTICE)
    return doc.save(path)


# ---------------------------------------------------------------- summary

def summary(project, path, date: datetime | None = None) -> Path:
    src = Source(project)
    if not src.clim:
        raise ValueError("Run step 1 (mast data) first.")
    c, d, lt, wm, lay, env = src.clim, src.data, src.lt, src.map, src.lay, src.env
    date = date or datetime.now()
    f = project.folder
    doc = Report("Project Wind Summary", src.name, f"BR-SUM-{_slug(src.name)}-{date:%Y%m%d}",
                 ["Measurement, wind map, layout and energy in one place"], date)
    doc.control(basis=_basis(src))
    titles = ["Where the project stands", "The measurement", "The long term", "The wind over the site", "Layout and energy",
              "Noise and shadow flicker", "What to check, and what comes next", "The documents and files"]
    doc.contents(titles)
    mean, what = src.lt_mean()

    doc.h1(titles[0])
    done = [("Mast data checked", bool(d)), ("Long-term correction", bool(lt)), ("Wind map", bool(wm)), ("Layout and energy", bool(lay)),
            ("Noise and shadow flicker screening", bool(env))]
    doc.table(["Step", "Status"], [[a, "Done" if b else "Not yet, or out of date"] for a, b in done], widths=[9, 6.9], caption="Steps of the analysis", numeric_from=None)
    rows = [(f"Long-term mean wind speed at {c['height_m']:g} m", f"{mean:.2f} m/s ({what})"),
            ("Measured", f"{_day(c['period'][0])} to {_day(c['period'][1])}; {c['recovery_pct']:.1f} % valid data")]
    if lay:
        t, u = lay["turbine"], lay["uncertainty"]
        rows += [("Indicative layout", f"{lay['n_placed']} × {t['rated_kw'] / 1000:g} MW = {lay['capacity_mw']:.1f} MW, hub height {t['hub_m']:g} m (generic turbine)"),
                 ("Net energy, P50", f"{lay['p50_gwh']:.1f} GWh a year (capacity factor {100 * lay['capacity_factor']:.1f} %)"),
                 ("P75 / P90, 10-year average", f"{u['p75_10_year']:.1f} / {u['p90_10_year']:.1f} GWh a year (σ {u['sigma_pct']['10_year']:.1f} %)")]
    if env:
        rows.append(("Homes above the noise level / a flicker limit", f"{env['noise']['over_limit']:,} / {env['flicker']['over_limit']:,} of {env['noise']['n_receptors']:,}"))
    doc.kv(rows, caption="Key figures")
    doc.flag(NOTICE)

    doc.h1(titles[1])
    alpha, _ = src.alpha()
    doc.p(f"The mast's top anemometer stands at {c['height_m']:g} m. "
          + (f"The wind climate rests on monthly summary tables ({_tables(c)['source']}), valid for {c['recovery_pct']:.0f} % of the period; no "
             "ten-minute record was checked. The measured " if _tables(c) else
             f"The data checks kept {c['records']:,} valid ten-minute records. The measured ")
          + f"mean is {c['mean_ws']:.2f} m/s" + (f" ({c['momm_ws']:.2f} m/s as the mean of monthly means)" if c.get("momm_ws") else "")
          + (f"; with the shape factor entered, the Weibull distribution has A = {c['weibull_A']:.2f} m/s and k = {c['weibull_k']:.2f}." if _tables(c)
             else f"; the Weibull fit gives A = {c['weibull_A']:.2f} m/s and k = {c['weibull_k']:.2f}.")
          + (f" Wind speed rises with height with a shear exponent of {alpha:.3f}." if alpha is not None else ""))
    doc.kv(src.position_rows(), caption="The mast")
    months = d.get("monthly") or [{"month": k, **v} for k, v in sorted((c.get("monthly") or {}).items())]
    doc.figure(fg.monthly(months, (lt or {}).get("monthly")), f"Monthly mean wind speed at {c['height_m']:g} m")
    if not c.get("no_direction"):
        doc.figure(fg.rose(c["sectors"], "energy"), "Wind energy by direction" + (" (directions from the reanalysis, not measured)" if _tables(c) else ""), 8.5)
    if _tables(c):
        doc.flag([x for x in (d.get("findings") or [])[:3]], label="Check:")

    doc.h1(titles[2])
    if lt:
        doc.p(f"Set against {lt.get('credit', 'a reanalysis reference').split(': ', 1)[-1].rstrip('.')} for {lt['lt_years'][0]} to {lt['lt_years'][1]}, the measured months "
              f"were {abs(100 * (1 / lt['factor'] - 1)):.1f} % {'windier' if lt['factor'] < 1 else 'calmer'} than the long term. The long-term factor is "
              f"{lt['factor']:.3f} (monthly correlation r = {lt['r']:.2f}, {lt['months']} months), giving {lt['long_term']:.2f} m/s at {lt['height_m']:g} m.")
        if lt.get("second"):
            doc.p(f"A second reference ({lt['second']['what'].split(':')[0]}) gives {lt['second']['long_term']:.2f} m/s, "
                  f"{lt['second']['difference_pct']:+.1f} % from the first.")
        if lt.get("cautions"):
            doc.flag(lt["cautions"], label="Check:")
    else:
        doc.p("No long-term correction has been made yet. The figures are those of the measured period.")
    hub = _hub_table(src)
    if hub:
        doc.table(["Height above ground", "Long-term mean wind speed, m/s", "Vertical extrapolation, % of speed", "Total, % of speed (1 σ)"],
                  hub, widths=[4.6, 4.2, 3.8, 3.3], caption="Long-term mean wind speed by hub height")

    doc.h1(titles[3])
    if wm:
        doc.p(f"The mast's wind was carried over {wm['area_km2']:.0f} km² of terrain with {ENGINES.get(wm['engine'], wm['engine'])}. At {wm['height_m']:g} m "
              f"the mean wind speed runs from {wm['min_ws']:.1f} to {wm['max_ws']:.1f} m/s; {wm['steep_share_pct']:.0f} % of the ground is steeper than 30 %.")
        doc.figure(f / wm["map_print"] if wm.get("map_print") else None, f"Mean wind speed at {wm['height_m']:g} m", 15.5)
        if wm.get("complex_terrain"):
            doc.flag("Complex terrain round the mast: the wind away from the mast is less certain here, and the energy estimate carries a "
                     "larger uncertainty for it. An independent flow study is needed before the map is relied on.", label="Check:")
    else:
        doc.p("No wind map has been made yet (step 3).")

    doc.h1(titles[4])
    if lay:
        doc.kv(_energy_rows(lay), caption="Energy estimate of the indicative layout")
        doc.figure(f / lay["map_print"] if lay.get("map_print") else None, "Indicative layout over the wind map", 15.5)
        mc = lay.get("mast_wind_ceiling")
        if mc:
            doc.p(f"Cautious reading: with no turbine position given more wind than the mast measured, the net energy would be "
                  f"{mc['p50_gwh']:.1f} GWh a year ({mc['change_pct']:+.1f} %).")
    else:
        doc.p("No layout has been made yet (step 4).")

    doc.h1(titles[5])
    if env:
        nz, fl = env["noise"], env["flicker"]
        doc.p(f"{nz['n_receptors']:,} homes were assessed. {nz['over_limit']:,} are above the {nz['limit_dba']:g} dB(A) level used; "
              f"{fl['over_limit']:,} exceed a shadow flicker limit in the astronomical worst case ({fl['with_any_flicker']:,} could see any flicker). "
              f"The nearest home stands {nz.get('nearest_home_m', 0):,.0f} m from a turbine. These are screening results with a generic turbine.")
    else:
        doc.p("Not screened yet for this layout (step 5).")

    doc.h1(titles[6])
    checks = list(dict.fromkeys(((lay or {}).get("warnings") or []) + ((wm or {}).get("warnings") or []) + ((lt or {}).get("cautions") or [])))
    if checks:
        doc.flag(checks, label="Check:")
    doc.bullets((["Obtain the ten-minute records, the installation report and the calibration certificates, and run step 1 on them: "
                  "until then every figure here is a first view."] if _tables(c) else []) +
                ["Have the measurement reviewed against the installation report, the calibration certificates and the maintenance log.",
                 "Choose candidate turbines with their suppliers; replace the generic turbine with their data under the suppliers' terms.",
                 "Confirm the siting rules with the land agreements and the environmental and social assessment.",
                 "Commission an independent energy yield assessment, with a flow study where the terrain is complex, before any financing decision."])

    doc.h1(titles[7])
    doc.table(["Document or file", "What it holds"],
              [["Wind Resource Analysis (Word)", "The measurement in full: setup, checks, statistics, long term, shear, uncertainty"],
               ["Micrositing and Energy Estimate (Word)", "Wind map, siting rules, layout, energy by turbine, uncertainty, screening"],
               ["Wind map (.kmz)", "The classed wind map for Google Earth with key, energy rose, mast and turbines"],
               ["summary.xlsx in the run folder", "Every table behind the measurement figures"],
               ["Permitting pack", "Coordinates, maps and screening tables for first talks with authorities"]],
              widths=[6, 9.9], caption="What goes with this summary", numeric_from=None)
    doc.flag(NOTICE)
    return doc.save(path)


def write_all(project, log=print, date: datetime | None = None) -> dict:
    """Write every report the project's results allow into reports/. Returns {"files": [...], "skipped": [...]};
    a report that cannot be written is said and left out, and never stops the step that asked for it."""
    from .kit import ReportError, available  # noqa: PLC0415
    out = {"files": [], "skipped": []}
    if not available():
        out["skipped"].append("The Word reports need the python-docx package, which is not installed.")
        log(out["skipped"][-1])
        return out
    date = date or datetime.now()
    slug = re.sub(r"[^A-Za-z0-9]+", "_", project.s["name"]).strip("_")[:40] or "project"
    for label, fn, fname in (("Wind resource analysis", wind_resource, f"Wind_Resource_Analysis_{slug}.docx"),
                             ("Micrositing report", micrositing, f"Micrositing_{slug}.docx"),
                             ("Project wind summary", summary, f"Project_Wind_Summary_{slug}.docx")):
        try:
            p = fn(project, project.path("reports", fname), date)
            out["files"].append({"file": project.rel(p), "what": label})
        except ValueError as e:                    # a step this report needs has not been run
            out["skipped"].append(f"{label}: {e}")
        except (ReportError, OSError) as e:
            out["skipped"].append(f"{label} not written: {e}")
            log(out["skipped"][-1])
    if out["files"]:
        from . import pdf  # noqa: PLC0415
        made = {p.stem for p in pdf.convert([project.folder / x["file"] for x in out["files"]], log=log)}
        out["files"] += [{"file": str(Path(x["file"]).with_suffix(".pdf").as_posix()), "what": x["what"]}
                         for x in list(out["files"]) if Path(x["file"]).stem in made]
        log("Reports: " + ", ".join(Path(x["file"]).name for x in out["files"]) + " in reports/.")
    return out
