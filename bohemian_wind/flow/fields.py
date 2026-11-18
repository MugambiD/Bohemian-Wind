"""Wind fields on the site grid: how the wind at every cell compares with the wind at the mast.

Two engines give the ratio "wind speed here / wind speed at the mast" at one height above ground:

linear   Bohemian linear flow model (bohemian_wind.flow.linear): terrain speed-up per wind direction sector.
         Needs only terrain. Terrain only: no roughness changes; not valid on steep ground.
atlas    Global Wind Atlas pattern: the atlas value at each cell over the atlas value at the mast (the same ratio
         for every direction). Needs GWA GeoTIFFs and the rasterio package. It keeps the atlas's own pattern,
         which was itself made with a linear model.

The mast's sector wind climate (long-term corrected, carried to the height with the measured shear) times these
ratios gives the wind at every cell. Both are screening tools.
[COMPLIANCE] Not an accredited or bankable flow model; complex-terrain flow modelling belongs with an independent
assessor.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .. import climate as _climate
from ..terrain import Grid
from . import linear

ENGINES = ("linear", "atlas")
RATIO_LIMITS = (0.4, 1.8)        # ratios outside this are not believable from either engine; they are clipped and counted
STEEP_LIMIT_PCT = 30.0           # slope above which linear theory over-predicts (17 degrees)
GWA_CREDIT = ("Wind atlas: data obtained from the Global Wind Atlas 4.0, a free, web-based application developed, owned "
              "and operated by the Technical University of Denmark (DTU). The Global Wind Atlas 4.0 is released in "
              "partnership with the World Bank Group, utilizing data provided by Vortex, using funding provided by the "
              "Energy Sector Management Assistance Program (ESMAP). globalwindatlas.info; CC BY 4.0; Davis et al. "
              "(2023), Bull. Amer. Meteor. Soc., doi:10.1175/BAMS-D-21-0075.1. Changed: scaled to mast measurements by "
              "the user of this software.")


class FlowError(RuntimeError):
    pass


@dataclass
class WindField:
    grid: Grid
    height_m: float
    centres: np.ndarray           # sector centres, degrees
    ratio: np.ndarray             # [sector, row, col]: wind speed relative to the mast, same height
    mean_ws: np.ndarray           # [row, col]: mean wind speed at height_m
    mast_mean_ws: float
    engine: str
    notes: list = field(default_factory=list)
    steep_share: float = 0.0      # share of cells steeper than 30 %
    clipped_share: float = 0.0

    def ratio_at(self, x, y) -> np.ndarray:
        """[sector, point] ratios at points (nearest cell)."""
        r, c = self.grid.index(x, y)
        return self.ratio[:, r, c]

    def save(self, path) -> None:
        np.savez_compressed(path, x=self.grid.x, y=self.grid.y, elev=self.grid.elev.astype("float32"),
                            lat0=self.grid.lat0, lon0=self.grid.lon0, source=self.grid.source,
                            height_m=self.height_m, centres=self.centres, ratio=self.ratio.astype("float32"),
                            mean_ws=self.mean_ws.astype("float32"), mast_mean_ws=self.mast_mean_ws, engine=self.engine,
                            notes=np.array(self.notes, dtype=str), steep_share=self.steep_share,
                            clipped_share=self.clipped_share)

    @classmethod
    def load(cls, path) -> "WindField":
        d = np.load(path, allow_pickle=False)
        g = Grid(x=d["x"], y=d["y"], elev=d["elev"].astype(float), lat0=float(d["lat0"]), lon0=float(d["lon0"]),
                 source=str(d["source"]))
        return cls(grid=g, height_m=float(d["height_m"]), centres=d["centres"], ratio=d["ratio"].astype(float),
                   mean_ws=d["mean_ws"].astype(float), mast_mean_ws=float(d["mast_mean_ws"]), engine=str(d["engine"]),
                   notes=[str(n) for n in d["notes"]], steep_share=float(d["steep_share"]),
                   clipped_share=float(d["clipped_share"]))


def linear_ratios(grid: Grid, centres, height_m: float, z0_m: float, mast_xy=(0.0, 0.0)) -> np.ndarray:
    """One run of the linear flow model per sector; each run divided by its value at the mast."""
    r0, c0 = grid.index(*mast_xy)
    out = np.empty((len(centres),) + grid.shape)
    for i, d in enumerate(centres):
        s = 1.0 + linear.speedup(grid.elev, grid.res, float(d), height_m, z0_m).speedup
        out[i] = s / s[r0, c0]
    return out


def find_gwa_layers(folder) -> dict:
    """{height in m: path} of the Global Wind Atlas wind-speed GeoTIFFs in a folder (names like
    KEN_wind-speed_100m.tif)."""
    out = {}
    for p in sorted(Path(folder).glob("*.tif")) + sorted(Path(folder).glob("*.tiff")):
        m = re.search(r"wind[-_ ]?speed[-_ ]?(\d+)\s*m", p.name, re.I)
        if m:
            out[float(m.group(1))] = p
    return out


def _gwa_layer(path, lat, lon) -> np.ndarray:
    """Atlas values at the atlas grid points nearest to each (lat, lon). The atlas computes a wind climate at
    points 250 m apart; interpolating between them on a hill mixes in points down the slope."""
    try:
        import rasterio  # noqa: PLC0415
        from rasterio.windows import from_bounds  # noqa: PLC0415
    except ImportError as e:  # pragma: no cover
        raise FlowError("The atlas engine needs the 'rasterio' package (pip install rasterio).") from e
    pad = 0.01
    with rasterio.open(path) as src:
        if src.crs is not None and src.crs.to_epsg() not in (4326, None):
            raise FlowError(f"{Path(path).name} is not in latitude/longitude (EPSG:4326).")
        win = from_bounds(lon.min() - pad, lat.min() - pad, lon.max() + pad, lat.max() + pad, transform=src.transform)
        win = win.round_offsets().round_lengths()
        data = src.read(1, window=win, boundless=True, fill_value=np.nan).astype(float)
        tf = src.window_transform(win)
        nodata = src.nodata
    if nodata is not None:
        data[data == nodata] = np.nan
    data[data <= 0] = np.nan
    col = np.clip(np.floor((lon - tf.c) / tf.a).astype(int), 0, data.shape[1] - 1)
    row = np.clip(np.floor((lat - tf.f) / tf.e).astype(int), 0, data.shape[0] - 1)
    return data[row, col]


def atlas_ratio(grid: Grid, layers: dict, height_m: float, mast_xy=(0.0, 0.0)) -> np.ndarray:
    """[row, col] ratio of the atlas at each cell to the atlas at the mast, at height_m (power law between the two
    atlas layers that bracket the height, as the atlas itself is layered)."""
    if not layers:
        raise FlowError("No Global Wind Atlas wind-speed GeoTIFFs found (file names like KEN_wind-speed_100m.tif).")
    X, Y = grid.mesh()
    lat, lon = grid.to_latlon(X, Y)
    hs = sorted(layers)
    lo = max([h for h in hs if h <= height_m], default=hs[0])
    hi = min([h for h in hs if h >= height_m], default=hs[-1])
    a = _gwa_layer(layers[lo], lat, lon)
    if lo != hi:
        b = _gwa_layer(layers[hi], lat, lon)
        a = a * (height_m / lo) ** (np.log(b / a) / math.log(hi / lo))
    if not np.isfinite(a).any():
        raise FlowError("The Global Wind Atlas files hold no data for this area.")
    r0, c0 = grid.index(*mast_xy)
    if not np.isfinite(a[r0, c0]):
        raise FlowError("The Global Wind Atlas has no value at the mast position.")
    return a / a[r0, c0]


def wind_field(grid: Grid, clim: dict, height_m: float, engine: str = "linear", z0_m: float = 0.05,
               lt_factor: float = 1.0, alpha: float | None = None, gwa_dir=None, mast_xy=(0.0, 0.0),
               keep_half_m: float | None = None, rotate_deg: float = 0.0) -> WindField:
    """Wind at every cell of the grid at height_m, from the mast's wind climate.

    keep_half_m: the result is cropped to this half-width. Run the engine on a grid about a quarter wider than
    the area wanted: the linear model fades the terrain out near the edges of its domain."""
    if engine not in ENGINES:
        raise FlowError(f"Unknown engine '{engine}'. Choose one of: {', '.join(ENGINES)}.")
    centres, f, A, k = _climate.at_height(clim, height_m, alpha=alpha, factor=lt_factor)
    if rotate_deg:
        centres = (centres + rotate_deg) % 360.0
    notes = []
    if engine == "linear":
        ratio = linear_ratios(grid, centres, height_m, z0_m, mast_xy)
        notes.append(f"Bohemian linear flow model v{linear.__version__}: terrain only, roughness length {z0_m:g} m "
                     "everywhere. No roughness changes, obstacles, stability or flow separation.")
    else:
        layers = find_gwa_layers(gwa_dir) if gwa_dir else {}
        ratio2d = atlas_ratio(grid, layers, height_m, mast_xy)
        ratio2d = np.where(np.isfinite(ratio2d), ratio2d, 1.0)
        ratio = np.broadcast_to(ratio2d, (len(centres),) + grid.shape).copy()
        notes.append("Global Wind Atlas pattern scaled to the mast: the same ratio for every wind direction. "
                     "It is not a flow model; far from the mast and in complex terrain it is only as good as the atlas.")
        notes.append(GWA_CREDIT)
    clipped = (ratio < RATIO_LIMITS[0]) | (ratio > RATIO_LIMITS[1])
    clipped_share = float(clipped.mean())
    ratio = np.clip(ratio, *RATIO_LIMITS)
    if keep_half_m is not None:
        ry, rx = grid.crop_slices(keep_half_m)
        grid, ratio = grid.crop(keep_half_m), ratio[:, ry, rx]
    steep = float((grid.slope_pct > STEEP_LIMIT_PCT).mean())
    if steep > 0.02:
        notes.append(f"{100 * steep:.0f} % of the area is steeper than {STEEP_LIMIT_PCT:.0f} % (17 degrees). Linear flow "
                     "models over-predict speed-ups on steep ground: treat the windiest cells with care.")
    if clipped_share > 0.001:
        notes.append(f"{100 * clipped_share:.1f} % of the ratios fell outside {RATIO_LIMITS[0]:g}-{RATIO_LIMITS[1]:g} "
                     "and were limited to that range.")
    sector_means = f * _climate.weibull_mean(A, k)
    mean_ws = np.tensordot(sector_means, ratio, axes=(0, 0))
    return WindField(grid=grid, height_m=float(height_m), centres=centres, ratio=ratio, mean_ws=mean_ws,
                     mast_mean_ws=float(sector_means.sum()), engine=engine, notes=notes, steep_share=steep,
                     clipped_share=clipped_share)
