"""The wind map as a Google Earth file (.kmz).

One file holds the map in its wind speed classes, laid over the ground; beside it the key to the classes, a sign
with the height, and the energy rose of the mast; the mast, the edge of the area mapped and, once there is a
layout, the turbines with their figures. Everything is inside the file: it opens without the project.

The pictures beside the map are laid on the ground like the map itself, so they keep their place and size when
the view is turned or printed.
"""
from __future__ import annotations

import html
import io
import textwrap
import zipfile
from math import gamma
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from . import ORG, __version__
from .maps import WIND_COLOURS, _font, wind_class_labels, wind_rgb
from .terrain import Grid, utm

NAVY, TEAL, INK, MUTED = "#222952", "#008781", "#16202A", "#4A5560"
NOTICE = ("Developer estimate prepared by Bohemian Renewables with open-source tools. It is not an accredited or "
          "bankable energy yield assessment and must not be used for financing decisions.")


def _png(fig, dpi: int = 200, transparent: bool = False) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, facecolor=fig.get_facecolor(), transparent=transparent)
    plt.close(fig)
    return buf.getvalue()


def overlay_png(mean_ws: np.ndarray, upscale: int = 2) -> bytes:
    """The map itself: one pixel block per cell in its class colour, north at the top; cells without a value are
    see-through."""
    a = np.asarray(mean_ws, dtype=float)[::-1]
    rgba = np.concatenate([wind_rgb(a), np.isfinite(a)[..., None].astype(float)], axis=2)
    rgba = np.kron(rgba, np.ones((upscale, upscale, 1)))
    buf = io.BytesIO()
    plt.imsave(buf, np.clip(rgba, 0, 1), format="png")
    return buf.getvalue()


def legend_png(height_m: float) -> bytes:
    """The key: every class with its limits, weakest at the top, as the classes run on the map."""
    labels = wind_class_labels()
    n = len(labels)
    plt.rcParams.update({"font.family": _font()})
    fig = plt.figure(figsize=(2.3, 9.6), facecolor="#EEF2F6")
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 1)
    ax.set_ylim(n + 2.4, -0.4)
    ax.axis("off")
    ax.text(0.5, 0.45, f"Mean wind speed at {height_m:g} m", ha="center", va="center", fontsize=8.6, fontweight="bold", color=NAVY)
    ax.text(0.5, 1.25, "m/s", ha="center", va="center", fontsize=8, color=MUTED)
    for i, (c, lab) in enumerate(zip(WIND_COLOURS, labels)):
        ax.add_patch(plt.Rectangle((0.08, 1.8 + i), 0.26, 0.86, facecolor=c, edgecolor="#9AA4AE", lw=0.4))
        ax.text(0.40, 1.8 + i + 0.43, lab, ha="left", va="center", fontsize=7.6, color=INK)
    ax.text(0.5, n + 2.05, ORG, ha="center", va="center", fontsize=6.6, color=MUTED)
    return _png(fig, dpi=180)


def sign_png(height_m: float) -> bytes:
    plt.rcParams.update({"font.family": _font()})
    fig = plt.figure(figsize=(2.6, 0.72), facecolor="white")
    fig.text(0.5, 0.5, f"HH {height_m:g} m", ha="center", va="center", fontsize=24, fontweight="bold", color=INK)
    return _png(fig, dpi=120)


def sector_energy(sectors: list, rho: float = 1.225, scale: float = 1.0) -> np.ndarray:
    """Wind energy through one square metre in a year from each sector, kWh: frequency x 0.5 rho A^3 Gamma(1 + 3/k)."""
    out = []
    for s in sectors:
        A, k, f = float(s["A"]) * scale, float(s["k"]), float(s["freq"])
        out.append(f * 0.5 * rho * A ** 3 * gamma(1.0 + 3.0 / k) * 8760.0 / 1000.0 if A > 0 and k > 0 else 0.0)
    return np.array(out)


def energy_rose_png(sectors: list, *, title: str, lines: list, rho: float = 1.225, scale: float = 1.0) -> bytes:
    """Energy by direction, with a block of text under it saying what the rose stands on."""
    e = sector_energy(sectors, rho, scale)
    centres = np.deg2rad([float(s["centre"]) for s in sectors])
    width = 2 * np.pi / max(len(sectors), 1)
    plt.rcParams.update({"font.family": _font()})
    fig = plt.figure(figsize=(5.0, 7.2), facecolor="white")
    fig.text(0.5, 0.965, title, ha="center", va="center", fontsize=11.5, fontweight="bold", color=NAVY)
    fig.text(0.5, 0.935, "Wind energy by direction, kWh per m² a year", ha="center", va="center", fontsize=9, color=MUTED)
    ax = fig.add_axes([0.12, 0.36, 0.76, 0.53], projection="polar")
    ax.set_theta_zero_location("N")
    ax.set_theta_direction(-1)
    ax.bar(centres, e, width=width * 0.92, color=TEAL, edgecolor="white", linewidth=0.8, zorder=3)
    ax.set_xticks(np.deg2rad(np.arange(0, 360, 45)))
    ax.set_xticklabels(["N", "NE", "E", "SE", "S", "SW", "W", "NW"], fontsize=9, color=INK)
    ax.tick_params(axis="y", labelsize=7, colors=MUTED)
    ax.set_rlabel_position(202)
    ax.grid(color="#C9D2D0", linewidth=0.6)
    ax.spines["polar"].set_color("#C9D2D0")
    total = float(e.sum())
    y = 0.292
    fig.text(0.08, y, f"All directions: {total:,.0f} kWh per m² a year", fontsize=8.8, fontweight="bold", color=INK, va="top")
    fig.text(0.08, y - 0.028, f"Mean power density {total / 8.76:,.0f} W/m²", fontsize=7.8, color=INK, va="top")
    y -= 0.03
    for ln in lines:
        for part in textwrap.wrap(str(ln), 84):
            y -= 0.026
            fig.text(0.08, y, part, fontsize=7.4, color=INK, va="top")
        y -= 0.004
    fig.text(0.08, 0.022, "\n".join(textwrap.wrap(NOTICE, 110)), fontsize=5.8, color=MUTED, va="bottom")
    return _png(fig, dpi=170)


def _e(v) -> str:
    return html.escape(str(v))


def _box(grid: Grid, x0: float, x1: float, y0: float, y1: float) -> dict:
    """Edges in metres east and north of the mast, as the latitudes and longitudes Google Earth wants."""
    (s, n), (w, e) = grid.to_latlon(0.0, np.array([y0, y1]))[0], grid.to_latlon(np.array([x0, x1]), 0.0)[1]
    return {"north": float(n), "south": float(s), "east": float(e), "west": float(w)}


def _ground(name: str, href: str, b: dict, order: int = 1, alpha: str = "ff") -> str:
    return (f"<GroundOverlay><name>{_e(name)}</name><color>{alpha}ffffff</color><drawOrder>{order}</drawOrder>"
            f"<Icon><href>{href}</href></Icon><LatLonBox><north>{b['north']:.7f}</north><south>{b['south']:.7f}</south>"
            f"<east>{b['east']:.7f}</east><west>{b['west']:.7f}</west></LatLonBox></GroundOverlay>")


def _table(rows: list) -> str:
    return ("<table>" + "".join(f"<tr><td><b>{_e(a)}</b></td><td>{_e(b)}</td></tr>" for a, b in rows) + "</table>"
            f"<p><i>{_e(NOTICE)}</i></p>")


def write(path, grid: Grid, mean_ws: np.ndarray, *, name: str, height_m: float, mast: dict, sectors: list | None = None,
          rose_lines: list | None = None, rho: float = 1.225, scale: float = 1.0, facts: list | None = None,
          turbines: list | None = None, turbine_note: str = "") -> Path:
    """mast: {"lat", "lon", "label"}; sectors: the wind climate's sectors (centre, freq, A, k) for the energy rose;
    facts: (label, value) pairs shown when the map is clicked; turbines: the layout's positions (id, lat, lon and the
    figures of each)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    h = grid.res / 2
    x0, x1, y0, y1 = grid.x[0] - h, grid.x[-1] + h, grid.y[0] - h, grid.y[-1] + h
    W, H, gap = x1 - x0, y1 - y0, 0.006 * (x1 - x0)
    b = _box(grid, x0, x1, y0, y1)
    files = {"files/wind_map.png": overlay_png(mean_ws), "files/legend.png": legend_png(height_m),
             "files/height_sign.png": sign_png(height_m)}
    legend_box = _box(grid, x1 + gap, x1 + gap + H * 2.3 / 9.6, y0, y1)              # the pictures keep their own proportions
    sign_box = _box(grid, x1 - 0.055 * H * 2.6 / 0.72, x1, y1 + gap, y1 + gap + 0.055 * H)
    kml = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>',
           f"<name>{_e(name)}</name><open>1</open>",
           f"<description><![CDATA[{_table(facts or [])}]]></description>",
           '<Style id="mast"><IconStyle><color>ff00ff00</color><scale>1.3</scale><Icon>'
           "<href>http://maps.google.com/mapfiles/kml/shapes/donut.png</href></Icon></IconStyle>"
           "<LabelStyle><scale>1.1</scale></LabelStyle></Style>",
           '<Style id="turbine"><IconStyle><color>ffffffff</color><scale>1.0</scale><Icon>'
           "<href>http://maps.google.com/mapfiles/kml/shapes/placemark_circle.png</href></Icon></IconStyle>"
           "<LabelStyle><scale>0.9</scale></LabelStyle></Style>",
           '<Style id="area"><LineStyle><color>ff000000</color><width>3</width></LineStyle><PolyStyle><fill>0</fill></PolyStyle></Style>',
           f"<LookAt><longitude>{(b['east'] + b['west']) / 2:.6f}</longitude><latitude>{(b['north'] + b['south']) / 2:.6f}</latitude>"
           f"<altitude>0</altitude><heading>0</heading><tilt>0</tilt><range>{2.6 * (grid.y[-1] - grid.y[0]):.0f}</range></LookAt>",
           "<Folder><name>Wind map</name><open>1</open>",
           _ground(f"Mean wind speed at {height_m:g} m", "files/wind_map.png", b, 1),
           _ground("Key to the wind speed classes", "files/legend.png", legend_box, 2),
           _ground(f"Height {height_m:g} m", "files/height_sign.png", sign_box, 2)]
    if sectors:
        files["files/energy_rose.png"] = energy_rose_png(sectors, title=f"Energy rose, {mast.get('label') or 'met mast'}",
                                                         lines=rose_lines or [], rho=rho, scale=scale)
        kml.append(_ground("Energy rose of the mast", "files/energy_rose.png",
                           _box(grid, x0 - gap - 0.42 * W, x0 - gap, y0, y0 + 0.42 * W * 7.2 / 5.0), 2))
    ring = " ".join(f"{lo:.6f},{la:.6f},0" for lo, la in ((b["west"], b["south"]), (b["east"], b["south"]), (b["east"], b["north"]),
                                                          (b["west"], b["north"]), (b["west"], b["south"])))
    kml.append(f'<Placemark><name>Area mapped</name><styleUrl>#area</styleUrl><Polygon><tessellate>1</tessellate>'
               f"<outerBoundaryIs><LinearRing><coordinates>{ring}</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>")
    kml.append("</Folder>")
    kml.append(f'<Placemark><name>{_e(mast.get("label") or "Met mast")}</name><styleUrl>#mast</styleUrl>'
               f"<description><![CDATA[{_table(facts or [])}]]></description>"
               f"<Point><coordinates>{mast['lon']:.6f},{mast['lat']:.6f},0</coordinates></Point></Placemark>")
    if turbines:
        kml.append(f"<Folder><name>Turbines ({len(turbines)})</name>"
                   + (f"<description>{_e(turbine_note)}</description>" if turbine_note else ""))
        for t in turbines:
            rows = [("Position", f"{t['lat']:.6f}, {t['lon']:.6f}")]
            if t.get("utm_zone"):
                rows.append(("UTM", f"zone {t['utm_zone']}, {t['easting']:.0f} E, {t['northing']:.0f} N"))
            for key, label, fmt in (("ground_elev_m", "Ground", "{:.0f} m"), ("free_ws", "Free wind at hub height", "{:.2f} m/s"),
                                    ("gross_mwh", "Gross energy", "{:,.0f} MWh a year"), ("wake_loss_pct", "Wake loss", "{:.1f} %"),
                                    ("p50_mwh", "Net energy, P50", "{:,.0f} MWh a year"), ("dist_mast_km", "From the mast", "{:.1f} km")):
                if t.get(key) is not None:
                    rows.append((label, fmt.format(t[key])))
            kml.append(f'<Placemark><name>{_e(t.get("id", ""))}</name><styleUrl>#turbine</styleUrl>'
                       f"<description><![CDATA[{_table(rows)}]]></description>"
                       f"<Point><coordinates>{t['lon']:.6f},{t['lat']:.6f},0</coordinates></Point></Placemark>")
        kml.append("</Folder>")
    kml.append(f"<!-- Bohemian Wind v{_e(__version__)} --></Document></kml>")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("doc.kml", "".join(kml))
        for n, data in files.items():
            z.writestr(n, data)
    return path


def mast_facts(name: str, site: dict, clim: dict, longterm: dict | None, height_m: float, engine: str, cell_m: float,
               terrain: str = "") -> tuple:
    """(facts for the click-through table, lines for under the energy rose) from a project's results."""
    u = utm(site["lat"], site["lon"])
    period = f"{str(clim['period'][0])[:10]} to {str(clim['period'][1])[:10]}"
    lt = longterm or {}
    mean = (f"{lt['long_term']:.2f} m/s at {clim['height_m']:g} m, long-term corrected (factor {lt['factor']:.3f})" if lt.get("long_term")
            else f"{(clim.get('momm_ws') or clim['mean_ws']):.2f} m/s at {clim['height_m']:g} m, measured period")
    how = {"linear": "own linear flow model", "atlas": "Global Wind Atlas pattern scaled to the mast",
           "cfd": "CFD screening run (OpenFOAM, steady RANS)"}.get(engine, engine)
    facts = [("Project", name), ("Met mast", f"{site['lat']:.6f}, {site['lon']:.6f}"),
             ("UTM WGS 84", f"zone {u['zone']}{u['hemisphere']}, {u['easting']:.0f} E, {u['northing']:.0f} N"),
             ("Measured", period), ("Mean wind speed at the mast", mean), ("Map height", f"{height_m:g} m above ground"),
             ("Flow model", how), ("Cell size", f"{cell_m:g} m"), ("Software", f"Bohemian Wind v{__version__}")]
    if clim.get("summary"):                       # step 1 read monthly summary tables (intake/summary.py)
        basis = ("monthly summary tables, no ten-minute record checked"
                 + ("" if clim.get("no_direction") else "; wind directions from a reanalysis, not measured"))
        facts.insert(4, ("Basis of the wind data", basis))
    if terrain:
        facts.insert(-1, ("Terrain", terrain))
    lines = [f"Reference point: UTM WGS 84 zone {u['zone']}{u['hemisphere']}, {u['easting']:.0f} E, {u['northing']:.0f} N",
             f"Wind data: {mean}",
             f"Measurement period: {period}" + ("; from monthly summary tables" + ("" if clim.get("no_direction") else
                                                 ", directions from a reanalysis") if clim.get("summary") else ""),
             f"Map: {how}; {cell_m:g} m cells; {height_m:g} m above ground" + (f"; {terrain}" if terrain else "")]
    return facts, lines
