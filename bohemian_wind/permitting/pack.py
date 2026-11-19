"""Builds the permitting pack from a project's latest results."""
from __future__ import annotations

import csv
import html
import json
import shutil
from datetime import datetime
from pathlib import Path

from .. import ORG, __version__
from ..terrain import utm

NAVY, TEAL, TINT = "#222952", "#008781", "#E6F3F2"
LOGO = Path(__file__).resolve().parents[1] / "app" / "static" / "img" / "bohemian-logo.png"

CSS = f"""
*{{box-sizing:border-box}}body{{font-family:Lato,'Segoe UI',Arial,sans-serif;color:#16202A;background:#EEF1F0;margin:0}}
main{{max-width:1000px;margin:0 auto;background:#fff;padding:36px 44px 56px}}
header{{display:flex;justify-content:space-between;align-items:flex-end;border-bottom:3px solid {TEAL};padding-bottom:14px}}
header img{{height:44px}}header p{{margin:0;color:#4A5560;font-size:12px;text-align:right}}
h1{{color:{NAVY};font-size:26px;margin:26px 0 4px}}h2{{color:{NAVY};font-size:17px;margin:34px 0 8px}}
h3{{color:{TEAL};font-size:13.5px;margin:20px 0 6px}}p,li{{font-size:13px;line-height:1.5;max-width:74ch}}
.lead{{font-size:14.5px;color:#4A5560}}.flag{{background:#FFF6DD;border-left:4px solid #EDA100;padding:10px 14px;font-size:12.5px;margin:16px 0}}
.tiles{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:16px 0}}
.tile{{background:{TINT};border-radius:6px;padding:11px 13px}}.tile b{{display:block;font-size:20px;color:{NAVY}}}
.tile span{{font-size:11.5px;color:#4A5560}}
.tw{{overflow-x:auto}}table{{border-collapse:collapse;font-size:12px;margin:8px 0;width:100%;font-variant-numeric:tabular-nums}}
th,td{{border-bottom:1px solid #DCE3E1;padding:5px 8px;text-align:left;white-space:nowrap}}
th{{background:{NAVY};color:#fff;font-weight:600}}td.wrap{{white-space:normal}}
td.over{{color:#B3261E;font-weight:700}}img.map{{width:100%;border:1px solid #DCE3E1;margin:8px 0}}
footer{{margin-top:40px;border-top:1px solid #DCE3E1;padding-top:12px;font-size:11.5px;color:#4A5560}}
@media print{{body{{background:#fff}}main{{padding:0}}h2{{break-after:avoid}}table,img{{break-inside:avoid}}}}
"""


def _e(v) -> str:
    return html.escape(str(v))


def _table(head: list, rows: list, over_col: int | None = None) -> str:
    out = ["<div class='tw'><table><tr>" + "".join(f"<th>{_e(h)}</th>" for h in head) + "</tr>"]
    for r in rows:
        cells = []
        for i, v in enumerate(r):
            cls = " class='over'" if over_col is not None and i == over_col and str(v).startswith("Over") else ""
            cells.append(f"<td{cls}>{_e(v)}</td>")
        out.append("<tr>" + "".join(cells) + "</tr>")
    out.append("</table></div>")
    return "".join(out)


def _kml(name: str, turbines: list, mast: dict, homes: list, tip_m: float) -> str:
    def mark(n, lat, lon, desc, style):
        return (f"<Placemark><name>{_e(n)}</name><description>{_e(desc)}</description><styleUrl>#{style}</styleUrl>"
                f"<Point><coordinates>{lon:.6f},{lat:.6f},0</coordinates></Point></Placemark>")
    parts = ['<?xml version="1.0" encoding="UTF-8"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>',
             f"<name>{_e(name)}: indicative layout</name>",
             "<description>Developer layout for discussion. Positions may change.</description>"]
    for sid, colour in (("turbine", "ff818700"), ("mast", "ff2659d9"), ("home", "ffe58739")):   # KML colours: aabbggrr
        parts.append(f'<Style id="{sid}"><IconStyle><color>{colour}</color><scale>1.0</scale></IconStyle></Style>')
    parts.append("<Folder><name>Turbines</name>")
    parts += [mark(t["id"], t["lat"], t["lon"], f"Ground {t['ground_elev_m']:.0f} m; blade tip {tip_m:.1f} m above ground", "turbine")
              for t in turbines]
    parts.append("</Folder><Folder><name>Met mast</name>")
    parts.append(mark("Met mast", mast["lat"], mast["lon"], "Measurement mast", "mast"))
    parts.append("</Folder>")
    if homes:
        parts.append("<Folder><name>Homes assessed</name>")
        parts += [mark(h["name"], h["lat"], h["lon"], "Receptor used for the noise and flicker screening", "home") for h in homes]
        parts.append("</Folder>")
    parts.append("</Document></kml>")
    return "".join(parts)


def _sources(bld: dict | None) -> str:
    src = (bld or {}).get("sources") or {}
    names = [n for k, n in (("osm", "OpenStreetMap"), ("microsoft", "Microsoft's building footprints")) if (src.get(k) or {}).get("ok")]
    return " and ".join(names) + (f", fetched {bld['fetched']}" if bld and bld.get("fetched") else "") if names else "open data"


def build(project, log=print) -> dict:
    """project: bohemian_wind.project.Project with a layout result. Returns the pack's files (paths relative to the
    project folder)."""
    lay = project.result("layout")
    env = project.result("environment") if project.status("environment") == "done" else None
    data, lt, wmap = project.result("data") or {}, project.result("longterm"), project.result("map") or {}
    name = project.s["name"]
    site = project.site()
    stamp = f"{datetime.now():%Y-%m-%d}"
    out_dir = project.path("permitting", f"pack_{stamp}", "x").parent
    t = lay["turbine"]
    files = []
    origin = lay.get("receptor_origin") or {}                      # where the homes came from (project.receptors)
    bld = project.result("buildings") if origin.get("fetched") else None

    def put(fname: str, what: str) -> Path:
        files.append({"file": project.rel(out_dir / fname), "what": what})
        return out_dir / fname

    # 1 turbine coordinates
    with open(put("turbine_coordinates.csv", "Turbine positions: latitude and longitude (WGS 84), UTM, ground elevation, hub and tip heights"),
              "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "latitude_wgs84", "longitude_wgs84", "utm_zone", "easting_m", "northing_m", "ground_elevation_m_amsl",
                    "hub_height_m_agl", "rotor_diameter_m", "tip_height_m_agl", "tip_elevation_m_amsl"])
        for p in lay["positions"]:
            w.writerow([p["id"], f"{p['lat']:.6f}", f"{p['lon']:.6f}", p["utm_zone"], p["easting"], p["northing"],
                        f"{p['ground_elev_m']:.1f}", t["hub_m"], t["rotor_m"], t["tip_height_m"], p["tip_amsl_m"]])
    # 2 GIS files
    homes = (env or {}).get("noise", {}).get("receptors", [])
    feats = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(p["lon"], 6), round(p["lat"], 6)]},
              "properties": {"kind": "turbine", "id": p["id"], "ground_elevation_m": round(p["ground_elev_m"], 1),
                             "hub_height_m": t["hub_m"], "tip_height_m": t["tip_height_m"]}} for p in lay["positions"]]
    feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(site["lon"], 6), round(site["lat"], 6)]},
                  "properties": {"kind": "met mast"}})
    for h in homes:
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(h["lon"], 6), round(h["lat"], 6)]},
                      "properties": {"kind": "home", "name": h["name"]}})
    n_homes = int(((env or {}).get("noise") or {}).get("n_receptors") or len(homes))
    which = "assessed homes" if len(homes) >= n_homes else f"the {len(homes)} homes with the highest noise levels (of {n_homes:,} assessed)"
    put("layout.geojson", f"Turbines, mast and {which} for GIS software (WGS 84)").write_text(
        json.dumps({"type": "FeatureCollection", "features": feats}, indent=1), encoding="utf-8")
    put("layout.kml", "The same, for Google Earth").write_text(_kml(name, lay["positions"], site, homes, t["tip_height_m"]), encoding="utf-8")
    # 3 constraints
    with open(put("constraints_applied.csv", "Each siting rule and the area it removed"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["rule", "area_removed_km2", "area_left_km2"])
        for s in lay["constraints"]["steps"]:
            w.writerow([s["rule"], s["removed_km2"], s["left_km2"]])
    # 4 copies of tables and maps
    for rel, fname, what in ((lay.get("map_print"), "map_layout.png", "Layout over the wind map"),
                             (wmap.get("map_print"), "map_wind.png", "Wind map"),
                             ((env or {}).get("noise_map_print"), "map_noise.png", "Noise screening map"),
                             ((env or {}).get("flicker_map_print"), "map_shadow_flicker.png", "Shadow flicker screening map"),
                             ((env or {}).get("noise_csv"), "noise_at_homes.csv", "Noise level at each home against the limit"),
                             ((env or {}).get("flicker_csv"), "shadow_flicker_at_homes.csv", "Worst-case shadow flicker at each home")):
        if rel and (project.folder / rel).is_file():
            shutil.copyfile(project.folder / rel, put(fname, what))
    if LOGO.is_file():
        shutil.copyfile(LOGO, out_dir / "logo.png")

    # 5 summary page
    u = lay["uncertainty"]
    mz = utm(site["lat"], site["lon"])
    tiles = [(f"{lay['n_placed']} × {t['rated_kw'] / 1000:g} MW", "Turbines (generic class)"), (f"{lay['capacity_mw']:.1f} MW", "Installed capacity"),
             (f"{t['hub_m']:g} m / {t['tip_height_m']:g} m", "Hub height / blade tip height"),
             (f"{lay['p50_gwh']:.0f} GWh/yr", "Net energy, P50 (developer estimate)")]
    rows_t = [[p["id"], f"{p['lat']:.6f}", f"{p['lon']:.6f}", p["utm_zone"], f"{p['easting']:.0f}", f"{p['northing']:.0f}",
               f"{p['ground_elev_m']:.0f}", f"{p['tip_amsl_m']:.0f}"] for p in lay["positions"]]
    sec = [f"""<header><div>{'<img src="logo.png" alt="Bohemian Renewables">' if LOGO.is_file() else f'<b>{_e(ORG)}</b>'}</div>
<p>Permitting support pack<br>{_e(stamp)}<br>Bohemian Wind v{_e(__version__)}</p></header>
<h1>{_e(name)}</h1><p class="lead">Indicative wind farm layout and screening results, prepared to open discussions with
authorities and consultants.</p>
<div class="flag"><b>[COMPLIANCE]</b> {_e(lay['estimate_note'])} The noise and shadow flicker figures are screening
calculations. This pack does not replace the environmental and social impact assessment, accredited noise and
flicker studies, aviation clearance or grid studies that a permit may need, and it makes no statement about what
the law requires. Turbine positions are indicative and may change.</div>
<div class="tiles">{''.join(f"<div class='tile'><b>{_e(a)}</b><span>{_e(b)}</span></div>" for a, b in tiles)}</div>"""]
    sec.append(f"""<h2>1. Site and measurement</h2>
<p>Met mast at {site['lat']:.6f}, {site['lon']:.6f} (WGS 84; UTM zone {mz['zone']}{mz['hemisphere']}, {mz['easting']:.0f} E,
{mz['northing']:.0f} N){f", ground elevation about {site['elevation_m']:.0f} m" if site.get('elevation_m') is not None else ""}.
Measured {_e(str(data.get('period', ['', ''])[0])[:10])} to {_e(str(data.get('period', ['', ''])[1])[:10])}:
mean wind speed {data.get('mean_ws', float('nan')):.2f} m/s at {data.get('top_height_m', 0):g} m over all valid records{f" ({data['momm_ws']:.2f} m/s as the mean of monthly means)" if data.get('momm_ws') else ""}, {data.get('recovery_pct', 0):.1f} % valid data
({_e(data.get('format', ''))}).
{f"Long-term mean {lt['long_term']:.2f} m/s at {lt['height_m']:g} m: the mean of the {lt['months']} months used, {lt['measured']:.2f} m/s, times a long-term factor of {lt['factor']:.3f} (monthly correlation r = {lt['r']:.2f})." if lt else "No long-term correction was applied."}</p>""")
    if (out_dir / "map_layout.png").is_file():
        sec.append("<h2>2. Layout</h2><img class='map' src='map_layout.png' alt='Layout map'>")
    sec.append("<h3>Turbine positions</h3>" + _table(
        ["Turbine", "Latitude", "Longitude", "UTM zone", "Easting, m", "Northing, m", "Ground, m amsl", "Blade tip, m amsl"], rows_t))
    sec.append(f"<p>Turbine class: {_e(t['name'])}, rotor {t['rotor_m']:g} m, hub height {t['hub_m']:g} m, blade tip {t['tip_height_m']:g} m "
               "above ground. A generic turbine of this size was used; the make and model are not chosen.</p>")
    sec.append("<h2>3. Siting rules applied</h2>" + _table(
        ["Rule", "Area removed, km²", "Area left, km²"],
        [[s["rule"], f"{s['removed_km2']:.2f}", f"{s['left_km2']:.2f}"] for s in lay["constraints"]["steps"]]))
    sec.append(f"<p>Buildable area after all rules: {lay['constraints']['buildable_km2']:.1f} km². The rules and buffer distances are the "
               "developer's own; confirm them against the requirements that apply to the site.</p>")
    sec.append(f"""<h2>4. Energy estimate</h2>
{_table(["Item", "Value"], [["Gross energy (no wakes, no losses)", f"{lay['gross_gwh']:.0f} GWh a year"],
        ["Wake loss", f"{lay['wake_loss_pct']:.1f} % ({lay['wake_model_name']})"],
        ["Other losses", f"{lay['other_loss_pct']:.1f} %"], ["Net energy, P50", f"{lay['p50_gwh']:.0f} GWh a year"],
        ["Capacity factor", f"{100 * lay['capacity_factor']:.1f} %"],
        ["P75 / P90, 10-year average", f"{u['p75_10_year']:.0f} / {u['p90_10_year']:.0f} GWh a year"],
        ["P90, any single year", f"{u['p90_1_year']:.0f} GWh"]])}
<p>{_e(lay['estimate_note'])}</p>""")
    checks = list(dict.fromkeys((lay.get("warnings") or []) + ((lt or {}).get("cautions") or [])))
    if checks:
        sec.append("<div class='flag'><b>Check before relying on these figures</b><ul>"
                   + "".join(f"<li>{_e(c)}</li>" for c in checks) + "</ul></div>")
    n = 5
    if env:
        nz, fl = env["noise"], env["flicker"]
        sec.append(f"""<h2>{n}. Noise screening</h2>
<p>{nz['n_receptors']:,} homes assessed; {nz['over_limit']:,} above the {nz['limit_dba']:g} dB(A) level used here
({nz['over_limit_with_uncertainty']:,} with a {nz['uncertainty_db']:g} dB allowance). Sound power {nz['sound_power_dba']:g} dB(A) per turbine.
Method: {_e(nz['method'])}.</p>""")
        if origin.get("fetched"):
            sec.append(f"<p>Where the homes come from: {origin['fetched']:,} buildings from open data ({_e(_sources(bld))})"
                       + (f" and {origin['yours']:,} marked by the developer" if origin.get("yours") else "")
                       + (f"; the developer's file took {origin['taken_out']:,} buildings out" if origin.get("taken_out") else "")
                       + ". Every building is counted as a home. The open data list buildings of every kind, the footprints "
                       "are machine detections in imagery, and none of it was surveyed on the ground.</p>")
            gaps = [w for w in (bld or {}).get("warnings") or []]
            if gaps:
                sec.append("<div class='flag'><b>Where the building sources disagree</b><ul>"
                           + "".join(f"<li>{_e(g)}</li>" for g in gaps) + "</ul></div>")
        if (out_dir / "map_noise.png").is_file():
            sec.append("<img class='map' src='map_noise.png' alt='Noise map'>")
        sec.append(_table(["Home", "Level, dB(A)", "With allowance", "Nearest turbine", "Distance, m", "Against the level used"],
                          [[r["name"], f"{r['level_dba']:.1f}", f"{r['level_plus_uncertainty_dba']:.1f}", r["nearest_turbine"],
                            f"{r['distance_m']:.0f}", "Over" if r["over_limit"] else "Within"] for r in nz["receptors"][:25]], over_col=5))
        if nz["n_receptors"] > 25:
            sec.append(f"<p>The 25 highest of {nz['n_receptors']:,} homes are shown; the full list is in noise_at_homes.csv.</p>")
        n += 1
        sec.append(f"""<h2>{n}. Shadow flicker screening</h2>
<p>{fl['with_any_flicker']:,} of {fl['n_receptors']:,} homes could see flicker; {fl['over_limit']:,} exceed {fl['limit_hours_year']:g} hours a year or
{fl['limit_min_day']:g} minutes a day in the astronomical worst case (sun always shining, rotor always turning and facing the
sun, nothing in between). Real flicker is a fraction of this. Stopping turbines whenever a home could be affected would cost at most
{fl['shutdown_upper_bound_pct']:.2f} % of operating time. Method: {_e(fl['method'])}.</p>""")
        if (out_dir / "map_shadow_flicker.png").is_file():
            sec.append("<img class='map' src='map_shadow_flicker.png' alt='Shadow flicker map'>")
        shown = [r for r in fl["receptors"] if r["hours_per_year"] > 0][:25]
        more = fl["with_any_flicker"] - len(shown)
        sec.append(_table(["Home", "Hours a year, worst case", "Longest day, min", "Days", "Turbines", "Against the limits used"],
                          [[r["name"], f"{r['hours_per_year']:.1f}", f"{r['max_minutes_per_day']:.0f}", r["days_with_flicker"],
                            ", ".join(r["turbines"]), "Over" if r["over_limit"] else "Within"] for r in shown], over_col=5)
                   if shown else "<p>No home is within range of flicker.</p>")
        if shown and more > 0:
            sec.append(f"<p>The {len(shown)} longest of {fl['with_any_flicker']:,} homes with flicker are shown; the full list is in "
                       "shadow_flicker_at_homes.csv.</p>")
        n += 1
    else:
        sec.append(f"<h2>{n}. Noise and shadow flicker</h2><p>Not included: run step 5 with a file of homes, then build the pack again.</p>")
        n += 1
    credits = list(dict.fromkeys((wmap.get("credits") or []) + ([lt["credit"]] if lt and lt.get("credit") else [])))
    if origin.get("fetched") and bld:
        credits += [f"Buildings. {a} The lists of homes in this pack are drawn from it: keep this notice with them."
                    for a in bld.get("attribution") or []]
    sec.append(f"""<h2>{n}. What this pack is based on</h2>
<ul>{''.join(f'<li>{_e(x)}</li>' for x in lay.get('notes', []))}</ul>
<h3>Data sources</h3><ul>{''.join(f'<li>{_e(c)}</li>' for c in credits) or '<li>Project measurements and files supplied by the developer.</li>'}</ul>
<h3>Usually still needed for a permit</h3>
<ul><li>Environmental and social impact assessment by a registered expert, including birds and bats, and consultation with the community.</li>
<li>Noise and shadow flicker studies to the standard the authority asks for, with the chosen turbine's data.</li>
<li>Aviation and telecommunication clearances: the coordinates and tip elevations in turbine_coordinates.csv are the usual starting point.</li>
<li>Land agreements, grid connection study and the energy regulator's licences.</li></ul>
<p>The list is a reminder, not legal advice: requirements differ by country and change.</p>
<h3>Files in this pack</h3>{_table(["File", "What it holds"], [[Path(f["file"]).name, f["what"]] for f in files])}
<footer>{_e(ORG)}. Clean Energy. Conscious Future.<br>Built with Bohemian Wind v{_e(__version__)}, open-source software.</footer>""")
    page = (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>{_e(name)}: permitting support pack</title>"
            f"<style>{CSS}</style></head><body><main>{''.join(sec)}</main></body></html>")
    index = out_dir / "index.html"
    index.write_text(page, encoding="utf-8")
    log(f"Permitting pack: {len(files) + 1} files in {project.rel(out_dir)}.")
    return {"folder": project.rel(out_dir), "index": project.rel(index), "files": files, "with_environment": bool(env)}
