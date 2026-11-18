"""Mast-calibrated wind resource maps (screening).

Method, per mast:
1. Long-term adjust the mast's measured mean with a monthly reanalysis index (NASA POWER,
   MERRA-2 wind speed at 50 m). With all 12 calendar months measured the index compares
   mean-of-monthly-means; with fewer months it is a ratio over the months measured.
2. Carry the long-term mean from the top measurement height to the map height with the
   shear exponent measured on the mast.
3. Sample the Global Wind Atlas (GWA) at the mast at the map height (log-law between the
   GWA layers that bracket it) and divide: one scale factor per mast.
4. Map = GWA at the map height x scale factor, around the mast, over SRTM terrain.

This keeps GWA's spatial pattern and anchors its level to the mast. It is not a flow model:
far from the mast, and in complex terrain, the pattern is only as good as GWA's.

Needs: numpy, pandas, matplotlib, rasterio. Inputs (all public):
- GWA 4.0 country GeoTIFFs, wind speed at 50, 100 and 150 m, e.g.
  https://globalwindatlas.info/api/gis/country/KEN/wind-speed/100  (saved as KEN_wind-speed_100m.tif)
- SRTM 1-arc-second tiles from Terrain Tiles on AWS, e.g.
  https://s3.amazonaws.com/elevation-tiles-prod/skadi/N01/N01E035.hgt.gz  (unzipped to N01E035.hgt)
- NASA POWER monthly MERRA-2 wind speed at 50 m, e.g.
  https://power.larc.nasa.gov/api/temporal/monthly/point?parameters=WS50M&community=RE&longitude=35.6789&latitude=1.2345&start=2001&end=2025&format=JSON
- Mast statistics from a Bohemian Wind QC run (summary.xlsx: Monthly WS, Wind rose, shear).

Example:
    mast = Mast(name="Example", lat=1.2345, lon=35.6789, elevation=1900, top_height=80.0,
                monthly={"2023-08": 6.1, ...}, shear=0.12, period="Jan 2023 - Dec 2023", rose=[[0, 4.0, 5.0], ...])
    build_site(mast, Path("gwa"), Path("srtm"), Path("power_example.json"), Path("out"), map_height=100)
"""
from __future__ import annotations

import json
import math
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import rasterio  # noqa: E402
import rasterio.enums  # noqa: E402
from matplotlib.colors import BoundaryNorm, LightSource, ListedColormap  # noqa: E402
from rasterio.windows import from_bounds  # noqa: E402

# one-hue sequential ramp (light to dark), as in the QC report
SEQ = ["#e6effb", "#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b", "#081f40"]
INK, INK2, MUTED, SURFACE, ACCENT = "#0b0b0b", "#52514e", "#8a8984", "#fcfcfb", "#eb6834"

GWA_CREDIT = ("Wind atlas: data obtained from the Global Wind Atlas 4.0, a free, web-based application developed, owned and "
              "operated by the Technical University of Denmark (DTU). The Global Wind Atlas 4.0 is released in partnership "
              "with the World Bank Group, utilizing data provided by Vortex, using funding provided by the Energy Sector "
              "Management Assistance Program (ESMAP). globalwindatlas.info; CC BY 4.0; Davis et al. (2023), Bull. Amer. "
              "Meteor. Soc., doi:10.1175/BAMS-D-21-0075.1. Changed: scaled to mast measurements by Bohemian Renewables.")
GWA_CHANGED = " Changed: scaled to mast measurements by Bohemian Renewables."
TERRAIN_CREDIT = "Terrain: SRTM data courtesy of the U.S. Geological Survey, via Terrain Tiles on AWS (Mapzen/Tilezen)."
POWER_CREDIT = "Long-term index: NASA Langley Research Center POWER Project (MERRA-2 monthly wind speed at 50 m)."
DISCLAIMER = ("[COMPLIANCE] Screening map prepared by Bohemian Renewables with open-source tools and public data. "
              "It is not a flow model, not an accredited or bankable assessment, and must not be used for financing decisions.")


@dataclass
class Mast:
    name: str
    lat: float
    lon: float
    elevation: float
    top_height: float                 # height of the measured mean, m
    monthly: dict                     # "YYYY-MM" -> mean wind speed at top_height
    shear: float                      # measured power-law exponent used to carry the mean to the map height
    period: str
    rose: list = field(default_factory=list)   # [sector_centre, freq_%, mean_ws]; empty = withheld
    rose_note: str = ""
    notes: list = field(default_factory=list)
    daily: dict = field(default_factory=dict)   # "YYYY-MM-DD" -> mean; used when fewer than 12 months
    power_daily: str = ""                       # NASA POWER daily JSON for the same days
    terrain_note: str = ""
    shear_note: str = ""                        # where the exponent comes from, e.g. "60-80 m pair"
    shear_test: str = ""                        # how well it predicted held-out data
    shear_heights: tuple = ()                   # (low, high) of the measured exponent; compares the atlas over the same pair
    label: str = ""                             # short name for the map label; default "<name> mast"


# ---------- long-term index ----------

def power_monthly(path) -> pd.Series:
    v = json.loads(Path(path).read_text())["properties"]["parameter"]["WS50M"]
    s = pd.Series({pd.Period(f"{k[:4]}-{k[4:]}", "M"): x for k, x in v.items() if k[4:] != "13"})
    return s[s > -900].sort_index()


def long_term(mast: Mast, ref: pd.Series, lt_years=(2005, 2024)) -> dict:
    meas = pd.Series({pd.Period(k, "M"): v for k, v in mast.monthly.items() if v is not None}).sort_index()
    conc = ref.reindex(meas.index)
    lt = ref[f"{lt_years[0]}-01":f"{lt_years[1]}-12"]
    r = float(np.corrcoef(meas, conc)[0, 1]) if len(meas) >= 3 else float("nan")
    cal = sorted(set(meas.index.month))
    if len(cal) == 12:
        momm = lambda s: s.groupby(s.index.month).mean().mean()  # noqa: E731
        measured, ref_period, ref_lt = momm(meas), momm(conc), momm(lt)
        method = "mean of monthly means, all 12 calendar months measured"
    elif mast.daily and mast.power_daily:
        p = json.loads(Path(mast.power_daily).read_text())["properties"]["parameter"]["WS50M"]
        refd = pd.Series(p, dtype=float)
        refd.index = pd.to_datetime(refd.index)
        dm = pd.Series(mast.daily, dtype=float)
        dm.index = pd.to_datetime(dm.index)
        j = pd.concat([dm.rename("m"), refd[refd > -900].rename("r")], axis=1, join="inner").dropna()
        measured, ref_period = float(j["m"].mean()), float(j["r"].mean())
        ref_lt = float(lt.groupby(lt.index.month).mean().mean())
        r = float(np.corrcoef(j["m"], j["r"])[0, 1])
        method = (f"only {len(j)} days in {len(cal)} calendar months measured: ratio of the long-term annual reference to "
                  "the reference over the same days; assumes the mast-to-reference ratio holds in the months not measured")
    else:
        measured, ref_period = float(meas.mean()), float(conc.mean())
        ref_lt = float(lt.groupby(lt.index.month).mean().mean())
        method = (f"only {len(cal)} calendar months measured: ratio of the long-term annual reference to the reference "
                  "over the months measured; assumes the mast-to-reference ratio holds in the months not measured")
    factor = ref_lt / ref_period
    return {"measured": float(measured), "factor": float(factor), "lt": float(measured * factor), "r": r,
            "months": len(meas), "calendar_months": len(cal), "method": method, "days": len(mast.daily),
            "ref_period": float(ref_period), "ref_lt": float(ref_lt), "lt_years": lt_years}


# ---------- Global Wind Atlas ----------

def _window(path, bounds):
    with rasterio.open(path) as src:
        win = from_bounds(*bounds, transform=src.transform).round_offsets().round_lengths()
        data = src.read(1, window=win).astype("float64")
        transform = src.window_transform(win)
    return data, transform


def gwa_at_height(layers: dict, h: float) -> np.ndarray:
    hs = sorted(layers)
    lo = max([x for x in hs if x <= h], default=hs[0])
    hi = min([x for x in hs if x >= h], default=hs[-1])
    if lo == hi:
        return layers[lo]
    alpha = np.log(layers[hi] / layers[lo]) / math.log(hi / lo)
    return layers[lo] * (h / lo) ** alpha


def nearest(arr, transform, lon, lat):
    """Value at the GWA grid point nearest the mast, with that point's position and distance (m).

    GWA computes a wind climate at points 250 m apart. On a hilltop, interpolating between
    points mixes in points down the slope, so the nearest point is used; its terrain height
    is reported next to the mast's so the match can be judged."""
    col, row = ~transform * (lon, lat)
    c, r = int(col), int(row)
    x, y = transform * (c + 0.5, r + 0.5)
    dist = math.hypot((x - lon) * 111320 * math.cos(math.radians(lat)), (y - lat) * 111320)
    return float(arr[r, c]), x, y, dist


# ---------- terrain ----------

def srtm_window(folder, bounds):
    """Mosaic SRTM 1-arc-second .hgt tiles over bounds (lon0, lat0, lon1, lat1)."""
    lon0, lat0, lon1, lat1 = bounds
    n = 3601
    rows = []
    for lat in range(int(math.floor(lat1)), int(math.floor(lat0)) - 1, -1):
        row = []
        for lon in range(int(math.floor(lon0)), int(math.floor(lon1)) + 1):
            name = f"{'N' if lat >= 0 else 'S'}{abs(lat):02d}{'E' if lon >= 0 else 'W'}{abs(lon):03d}.hgt"
            a = np.fromfile(Path(folder) / name, ">i2").reshape(n, n).astype("float64")
            row.append(a[:-1, :-1])
        rows.append(np.hstack(row))
    mosaic = np.vstack(rows)
    mosaic[mosaic < -1000] = np.nan
    top, left, res = math.floor(lat1) + 1, math.floor(lon0), 1.0 / (n - 1)
    r0, r1 = int((top - lat1) / res), int(math.ceil((top - lat0) / res))
    c0, c1 = int((lon0 - left) / res), int(math.ceil((lon1 - left) / res))
    return mosaic[r0:r1, c0:c1], (left + c0 * res, top - r0 * res, res)


# ---------- map ----------

def _classes(values, n=8, steps=(0.2, 0.25, 0.5)):
    """n equal classes ending at the 99.5th percentile, with the finest step (0.2-0.5 m/s) that covers
    the 2nd-99.5th percentile range; lower and higher values get the end colours."""
    lo, hi = np.nanpercentile(values, [2, 99.5])
    step = next((s for s in steps if n * s >= hi - lo), steps[-1])
    top = math.ceil(hi / step - 1e-9) * step
    return np.round(top - step * np.arange(n, -1, -1), 3)


def class_cmap(edges, palette: list | None = None):
    """Colours for len(edges) - 1 classes plus under and over. palette: [under, class 1 .. class n, over]
    (a validated one-hue ramp, light to dark); None keeps the default blue ramp."""
    n = len(edges) - 1
    if palette is None:
        cmap = ListedColormap(SEQ[1: len(edges)])
        cmap.set_under(SEQ[0])
        cmap.set_over("#050f22")
    else:
        if len(palette) < n + 2:
            raise ValueError(f"palette needs {n + 2} colours (under, {n} classes, over); got {len(palette)}")
        cmap = ListedColormap(palette[1: n + 1])
        cmap.set_under(palette[0])
        cmap.set_over(palette[-1])
    return cmap


def draw_site_map(mast: Mast, ws: np.ndarray, ws_transform, dem: np.ndarray, dem_geo, out_png: Path,
                  map_height: float, info: list, title: str, subtitle: str, half_km: float,
                  extra_note: str = "", mast_value: float | None = None, palette: list | None = None,
                  edges=None, marker: str = ACCENT):
    left, top, res = dem_geo
    ny, nx = dem.shape
    extent = (left, left + nx * res, top - ny * res, top)
    # GWA classes resampled onto the terrain grid (nearest), then shaded by the terrain
    edges = np.asarray(edges, dtype=float) if edges is not None else _classes(ws)
    cmap = class_cmap(edges, palette)
    norm = BoundaryNorm(edges, cmap.N)
    lons = left + (np.arange(nx) + 0.5) * res
    lats = top - (np.arange(ny) + 0.5) * res
    cols = np.clip(((lons - ws_transform.c) / ws_transform.a).astype(int), 0, ws.shape[1] - 1)
    rows = np.clip(((lats - ws_transform.f) / ws_transform.e).astype(int), 0, ws.shape[0] - 1)
    wsg = ws[np.ix_(rows, cols)]
    rgb = cmap(norm(wsg))[..., :3]
    ls = LightSource(azdeg=315, altdeg=40)
    km = 111.32
    shade = ls.hillshade(np.nan_to_num(dem, nan=np.nanmean(dem)), vert_exag=1.5,
                         dx=res * km * 1000 * math.cos(math.radians(mast.lat)), dy=res * km * 1000)
    rgb = rgb * (0.62 + 0.38 * shade[..., None])

    fig = plt.figure(figsize=(11.0, 8.6), facecolor=SURFACE)
    ax = fig.add_axes([0.055, 0.185, 0.58, 0.675])  # room below for up to 9 footer lines
    ax.imshow(rgb, extent=extent, origin="upper", interpolation="nearest")
    cs = ax.contour(lons, lats, dem, levels=np.arange(0, 5000, 100), colors=INK, linewidths=0.35, alpha=0.35)
    major = [lv for lv in cs.levels if lv % 500 == 0]
    if major:
        ax.clabel(cs, levels=major, fontsize=6.5, fmt="%d m", colors=INK2, inline_spacing=2)
    ax.plot(mast.lon, mast.lat, marker="^", markersize=11, color=marker, markeredgecolor="white", markeredgewidth=1.4, zorder=5)
    label = (mast.label or f"{mast.name} mast") + (f" · {mast_value:.1f} m/s" if mast_value is not None else "")
    ax.annotate(label, (mast.lon, mast.lat), xytext=(9, -4), textcoords="offset points",
                fontsize=9, fontweight="bold", color=INK, zorder=6,
                bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="none", alpha=0.8))
    ax.set_xlim(extent[0], extent[1])
    ax.set_ylim(extent[2], extent[3])
    ax.set_aspect(1.0 / math.cos(math.radians(mast.lat)))
    ax.tick_params(labelsize=7.5, colors=INK2)
    ax.set_xlabel("Longitude, °E", fontsize=8, color=INK2)
    ax.set_ylabel("Latitude, °N" if mast.lat >= 0 else "Latitude, ° (negative = south)", fontsize=8, color=INK2)
    for s in ax.spines.values():
        s.set_color(MUTED)
    # scale bar and north arrow
    bar_km = 5 if half_km >= 10 else 2
    x0 = extent[0] + 0.05 * (extent[1] - extent[0])
    y0 = extent[2] + 0.05 * (extent[3] - extent[2])
    dlon = bar_km / (km * math.cos(math.radians(mast.lat)))
    ax.add_patch(plt.Rectangle((x0, y0), dlon, (extent[3] - extent[2]) * 0.008, color=INK, zorder=7))
    ax.add_patch(plt.Rectangle((x0, y0), dlon / 2, (extent[3] - extent[2]) * 0.008, color="white", ec=INK, lw=0.6, zorder=8))
    ax.text(x0 + dlon / 2, y0 + (extent[3] - extent[2]) * 0.018, f"{bar_km} km", ha="center", va="bottom", fontsize=7.5,
            color=INK, zorder=8, bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.75))
    ax.annotate("N", xy=(0.95, 0.95), xytext=(0.95, 0.86), xycoords="axes fraction", ha="center", va="center",
                fontsize=10, fontweight="bold", color=INK, arrowprops=dict(arrowstyle="-|>", color=INK, lw=1.2))

    # colour key (left of the right-hand column)
    fig.text(0.675, 0.85, f"Mean wind speed\nat {map_height:g} m, m/s", fontsize=8.5, fontweight="bold", color=INK, va="bottom")
    cax = fig.add_axes([0.675, 0.60, 0.022, 0.23])
    cb = fig.colorbar(plt.cm.ScalarMappable(norm=norm, cmap=cmap), cax=cax, ticks=edges, extend="both",
                      extendfrac=0.08)
    cb.ax.tick_params(labelsize=7.5, colors=INK2)
    cb.outline.set_edgecolor(MUTED)

    # wind rose (right of the colour key)
    if mast.rose:
        fig.text(0.80, 0.85, "Direction frequency\nat the mast, %", fontsize=8.5, fontweight="bold", color=INK, va="bottom")
        rax = fig.add_axes([0.795, 0.615, 0.175, 0.21], projection="polar")
        rose = np.array(mast.rose, dtype=float)
        rax.set_theta_zero_location("N")
        rax.set_theta_direction(-1)
        rax.bar(np.radians(rose[:, 0]), rose[:, 1], width=2 * np.pi / len(rose) * 0.85,
                color=(palette[6] if palette else SEQ[5]), edgecolor=SURFACE, lw=0.8)
        rax.set_xticks(np.radians([0, 90, 180, 270]), ["N", "E", "S", "W"])
        ring = 10 if rose[:, 1].max() <= 60 else 20
        rax.set_yticks(np.arange(ring, rose[:, 1].max() + ring, ring))
        rax.set_rlabel_position(200)
        rax.tick_params(labelsize=6.5, colors=INK2, pad=-1)
        rax.grid(color="#d6d5d0", lw=0.6)
        if mast.rose_note:
            fig.text(0.80, 0.585, textwrap.fill(mast.rose_note, 40), fontsize=6.8, color=INK2, va="top")
    elif mast.rose_note:
        fig.text(0.80, 0.85, "Direction at the mast", fontsize=8.5, fontweight="bold", color=INK, va="bottom")
        fig.text(0.805, 0.815, textwrap.fill(mast.rose_note, 36), fontsize=7.0, color=INK2, va="top",
                 bbox=dict(boxstyle="round,pad=0.5", fc="#fff6dd", ec="#eda100", lw=0.8))

    # key numbers
    y = 0.44
    fig.text(0.675, y + 0.03, "How the map is anchored to the mast", fontsize=8.5, fontweight="bold", color=INK)
    for label, value in info:
        fig.text(0.675, y, label, fontsize=7.6, color=INK2, va="top")
        fig.text(0.975, y, value, fontsize=7.6, color=INK, va="top", ha="right", fontweight="bold")
        y -= 0.032
    if extra_note:
        fig.text(0.675, y - 0.005, textwrap.fill(extra_note, 70), fontsize=6.8, color=INK2, va="top")

    fig.text(0.055, 0.945, title, fontsize=14, fontweight="bold", color=INK)
    fig.text(0.055, 0.92, textwrap.fill(subtitle, 150), fontsize=9, color=INK2, va="top")
    foot = "\n".join(textwrap.fill(t, 215) for t in [DISCLAIMER, GWA_CREDIT, TERRAIN_CREDIT + " " + POWER_CREDIT] + mast.notes)
    fig.text(0.055, 0.02, foot, fontsize=6.3, color=INK2, va="bottom")
    fig.savefig(out_png, dpi=200, facecolor=SURFACE)
    fig.savefig(out_png.with_suffix(".pdf"), facecolor=SURFACE)
    plt.close(fig)


def write_geotiff(path, arr, transform, description):
    with rasterio.open(path, "w", driver="GTiff", height=arr.shape[0], width=arr.shape[1], count=1, dtype="float32",
                      crs="EPSG:4326", transform=transform, nodata=np.nan, compress="deflate") as dst:
        dst.write(arr.astype("float32"), 1)
        dst.set_band_description(1, description)
        dst.update_tags(SOURCE=GWA_CREDIT, METHOD="GWA 4.0 wind speed scaled by one factor per mast (see map)",
                        DISCLAIMER=DISCLAIMER)


def write_geojson(path, features: list) -> None:
    """Point features (WGS84 longitude, latitude) for QGIS and other GIS tools."""
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(f["lon"], 7), round(f["lat"], 7)]},
         "properties": {k: v for k, v in f.items() if k not in ("lon", "lat")}} for f in features]}
    Path(path).write_text(json.dumps(fc, indent=1, ensure_ascii=False), encoding="utf-8")


def mast_feature(mast: Mast, res: dict, map_height: float = 100.0) -> dict:
    lt = res["lt"]
    return {"lon": mast.lon, "lat": mast.lat, "name": f"{mast.name} mast", "period": mast.period,
            "elevation_m": mast.elevation, "measured_height_m": mast.top_height,
            "measured_mean_ms": round(lt["measured"], 3), "long_term_factor": round(lt["factor"], 4),
            "long_term_mean_ms": round(lt["lt"], 3), "shear_exponent": mast.shear, "shear_source": mast.shear_note,
            "shear_test": mast.shear_test,
            f"estimate_{map_height:g}m_ms": round(res["hub_estimate"], 3),
            f"atlas_{map_height:g}m_unadjusted_ms": round(res["gwa_at_mast_map"], 3),
            "atlas_scale_factor": round(res["scale"], 4), "map_type": "mast-adjusted atlas, long-term",
            "atlas_credit": GWA_CREDIT, "disclaimer": DISCLAIMER}


def build_site(mast: Mast, gwa_dir: Path, srtm_dir: Path, power_json: Path, out_dir: Path,
               map_height: float = 100.0, half_km: float = 15.0, country="KEN", ref: pd.Series | None = None,
               palette: list | None = None, edges=None, marker: str = ACCENT) -> dict:
    """ref: monthly reference series (PeriodIndex) to use instead of reading power_json, e.g. POWER monthly
    values extended with monthly means of POWER daily values for the current year."""
    out_dir.mkdir(parents=True, exist_ok=True)
    km = 111.32
    dlat, dlon = half_km / km, half_km / (km * math.cos(math.radians(mast.lat)))
    bounds = (mast.lon - dlon, mast.lat - dlat, mast.lon + dlon, mast.lat + dlat)
    layers, transform = {}, None
    for h in (50, 100, 150):
        layers[h], transform = _window(gwa_dir / f"{country}_wind-speed_{h}m.tif", bounds)
    gwa_map = gwa_at_height(layers, map_height)
    gwa_top = gwa_at_height(layers, mast.top_height)
    at_map, gx, gy, gdist = nearest(gwa_map, transform, mast.lon, mast.lat)
    at_top = nearest(gwa_top, transform, mast.lon, mast.lat)[0]
    at_mast_height = abs(map_height - mast.top_height) < 1e-9  # measured at the map height: no shear step
    if mast.shear_heights:
        h1, h2 = mast.shear_heights
        g1 = nearest(gwa_at_height(layers, h1), transform, mast.lon, mast.lat)[0]
        g2 = nearest(gwa_at_height(layers, h2), transform, mast.lon, mast.lat)[0]
        gwa_alpha = math.log(g2 / g1) / math.log(h2 / h1)
    else:
        gwa_alpha = math.log(at_map / at_top) / math.log(map_height / mast.top_height) if not at_mast_height else float("nan")
    lt = long_term(mast, ref if ref is not None else power_monthly(power_json))
    hub = lt["lt"] * (map_height / mast.top_height) ** mast.shear
    scale = hub / at_map
    ws = gwa_map * scale
    dem, dem_geo = srtm_window(srtm_dir, bounds)
    dl, dt, dr = dem_geo
    grid_z = float(dem[int((dt - gy) / dr), int((gx - dl) / dr)])
    mast_z = float(dem[int((dt - mast.lat) / dr), int((mast.lon - dl) / dr)])
    stem = out_dir / f"{mast.name.split(' (')[0].replace(' ', '_')}_wind_speed_{map_height:g}m"
    write_geotiff(stem.with_suffix(".tif"), ws, transform, f"Mean wind speed at {map_height:g} m, m/s (mast-calibrated)")
    inside = ws[np.isfinite(ws)]
    measured_label = ("Measured, {h:g} m (mean of monthly means)" if lt["calendar_months"] == 12
                      else "Measured, {h:g} m ({d} days, {n} months)").format(h=mast.top_height, n=lt["calendar_months"],
                                                                            d=lt["days"])
    info = [
        (measured_label, f"{lt['measured']:.2f} m/s"),
        (f"Long-term index, MERRA-2 {lt['lt_years'][0]}–{str(lt['lt_years'][1])[2:]} (r {lt['r']:.2f})", f"× {lt['factor']:.3f}"),
        (f"Long-term estimate, {mast.top_height:g} m", f"{lt['lt']:.2f} m/s"),
        ((f"At {map_height:g} m: measured there, no shear step" if at_mast_height else
          f"At {map_height:g} m, shear {mast.shear:.3f}" + (f" ({mast.shear_note})" if mast.shear_note else "")),
         f"{hub:.2f} m/s"),
        (f"Atlas, nearest grid point, {map_height:g} m, unadjusted", f"{at_map:.2f} m/s"),
        ("Scale factor on the atlas", f"× {scale:.3f}"),
        ("Map range, 1st–99th percentile", f"{np.nanpercentile(inside, 1):.1f}–{np.nanpercentile(inside, 99):.1f} m/s"),
    ]
    pair = f" ({mast.shear_heights[0]:g}–{mast.shear_heights[1]:g} m)" if mast.shear_heights else ""
    extra = (f"Nearest atlas grid point: {gdist:.0f} m from the mast, terrain {grid_z:,.0f} m (mast {mast_z:,.0f} m). "
             f"Atlas shear there{pair}: {gwa_alpha:+.3f}; measured: {mast.shear:+.3f}."
             + (f" Shear test: {mast.shear_test}." if mast.shear_test else ""))
    title = f"{mast.name}: long-term mean wind speed at {map_height:g} m"
    subtitle = (f"Map type: mast-adjusted atlas. The Global Wind Atlas 4.0 pattern, scaled by one factor to the long-term "
                f"mean of the {mast.name} mast ({mast.period}); the unadjusted atlas reads {at_map:.2f} m/s at the mast. "
                f"Mast at {abs(mast.lat):.4f}°{'N' if mast.lat >= 0 else 'S'}, {mast.lon:.4f}°E, "
                f"{mast.elevation:,.0f} m{mast.terrain_note}.")
    draw_site_map(mast, ws, transform, dem, dem_geo, stem.with_suffix(".png"), map_height, info, title, subtitle, half_km,
                  extra_note=extra, mast_value=hub, palette=palette, edges=edges, marker=marker)
    res = {"lt": lt, "hub_estimate": hub, "gwa_at_mast_map": at_map, "scale": scale}
    write_geojson(Path(f"{stem}_mast.geojson"), [mast_feature(mast, res, map_height)])
    return {"site": mast.name, "lt": lt, "hub_estimate": hub, "gwa_at_mast_map": at_map, "gwa_at_mast_top": at_top,
            "gwa_shear_at_mast": gwa_alpha, "scale": scale, "grid_point_distance_m": gdist,
            "grid_point_srtm_m": grid_z, "mast_srtm_m": mast_z, "map_p1": float(np.nanpercentile(inside, 1)),
            "map_p99": float(np.nanpercentile(inside, 99)), "map_max": float(np.nanmax(inside)),
            "png": str(stem.with_suffix(".png")), "pdf": str(stem.with_suffix(".pdf")), "tif": str(stem.with_suffix(".tif")),
            "geojson": f"{stem}_mast.geojson"}


def write_manifest(data_dir: Path, sources: dict, path: Path) -> list:
    """SHA-256, size and source of every input file, so the maps can be rebuilt and checked offline.
    sources: relative path -> (source URL or description, terms, retrieved)."""
    import csv
    import hashlib
    rows = []
    for rel, (src, terms, when) in sources.items():
        f = Path(data_dir) / rel
        h = hashlib.sha256(f.read_bytes()).hexdigest()
        rows.append({"file": rel, "bytes": f.stat().st_size, "sha256": h, "source": src, "terms": terms, "retrieved": when})
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return rows


def check_manifest(data_dir: Path, manifest: Path) -> list:
    """Files whose SHA-256 differs from the manifest, or that are missing."""
    import csv
    import hashlib
    bad = []
    with open(manifest, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            f = Path(data_dir) / row["file"]
            if not f.exists() or hashlib.sha256(f.read_bytes()).hexdigest() != row["sha256"]:
                bad.append(row["file"])
    return bad


def draw_overview(results: list, gwa_100: Path, out_png: Path, title: str, step: int = 4, palette: list | None = None,
                  marker: str = ACCENT):
    """Country view: unscaled GWA at 100 m with each mast and its scale factor."""
    with rasterio.open(gwa_100) as src:
        arr = src.read(1, out_shape=(src.height // step, src.width // step), resampling=rasterio.enums.Resampling.average)
        b = src.bounds
    edges = np.arange(3.0, 11.0, 1.0)
    cmap = class_cmap(edges, palette)
    cmap.set_bad(SURFACE)
    norm = BoundaryNorm(edges, cmap.N)
    fig = plt.figure(figsize=(9.5, 9.6), facecolor=SURFACE)
    ax = fig.add_axes([0.07, 0.13, 0.74, 0.74])
    ax.imshow(np.ma.masked_invalid(arr), extent=(b.left, b.right, b.bottom, b.top), origin="upper", cmap=cmap, norm=norm,
              interpolation="nearest")
    ax.set_aspect(1.0)
    ax.tick_params(labelsize=8, colors=INK2)
    ax.set_xlabel("Longitude, °E", fontsize=8.5, color=INK2)
    ax.set_ylabel("Latitude, ° (negative = south)", fontsize=8.5, color=INK2)
    for sp in ax.spines.values():
        sp.set_color(MUTED)
    for r in results:
        ax.plot(r["lon"], r["lat"], marker="^", markersize=12, color=marker, markeredgecolor="white", markeredgewidth=1.5, zorder=5)
        txt = (f"{r['site']}\nmast: {r['hub_estimate']:.1f} m/s at 100 m, long-term\n"
               f"atlas: {r['gwa_at_mast_map']:.1f} m/s (scale × {r['scale']:.2f})")
        ax.annotate(txt, (r["lon"], r["lat"]), xytext=r.get("label_offset", (10, 6)), textcoords="offset points", fontsize=8,
                    color=INK, zorder=6, bbox=dict(boxstyle="round,pad=0.3", fc="white", ec=MUTED, lw=0.6, alpha=0.92),
                    arrowprops=dict(arrowstyle="-", color=INK2, lw=0.8, shrinkA=0, shrinkB=7))
    cax = fig.add_axes([0.84, 0.40, 0.025, 0.30])
    cb = fig.colorbar(plt.cm.ScalarMappable(norm=norm, cmap=cmap), cax=cax, ticks=edges, extend="both", extendfrac=0.08)
    cb.ax.tick_params(labelsize=8, colors=INK2)
    cb.outline.set_edgecolor(MUTED)
    fig.text(0.84, 0.72, "Mean wind speed\nat 100 m, m/s\n(atlas, not scaled)", fontsize=8.5, fontweight="bold", color=INK, va="bottom")
    fig.text(0.07, 0.945, title, fontsize=14, fontweight="bold", color=INK)
    fig.text(0.07, 0.92, textwrap.fill("Map type: atlas, unadjusted. Each mast's long-term estimate at 100 m against the atlas at the "
                                       "same point. The site maps scale the atlas by that factor around each mast; far from a mast the "
                                       "atlas stands unscaled.", 120),
             fontsize=9, color=INK2, va="top")
    foot = "\n".join(textwrap.fill(t, 160) for t in [DISCLAIMER, GWA_CREDIT.replace(GWA_CHANGED, ""),
                                                    "[INTERNAL] Mast results: written consent needed before external use."])
    fig.text(0.07, 0.025, foot, fontsize=6.6, color=INK2, va="bottom")
    fig.savefig(out_png, dpi=200, facecolor=SURFACE)
    fig.savefig(out_png.with_suffix(".pdf"), facecolor=SURFACE)
    plt.close(fig)
