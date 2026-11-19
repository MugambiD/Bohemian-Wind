"""Terrain on a local metric grid around the mast.

Everything after the mast analysis (flow model, layout, noise, shadow flicker) works on one grid: x metres east
and y metres north of an origin (the mast), rows running south to north. Over the 10-20 km of a wind farm site a
flat local grid is accurate to about one part in a thousand, and it keeps every distance in metres.

Terrain comes from SRTM 1-arc-second tiles (public; the names look like S02E036.hgt), or from any array.
"""
from __future__ import annotations

import gzip
import math
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

WGS84_A, WGS84_F = 6378137.0, 1 / 298.257223563
SRTM_URL = "https://s3.amazonaws.com/elevation-tiles-prod/skadi/{band}/{name}.hgt.gz"
TERRAIN_CREDIT = "Terrain: SRTM data courtesy of the U.S. Geological Survey, via Terrain Tiles on AWS (Mapzen/Tilezen)."
VOID = -32768


class TerrainError(RuntimeError):
    pass


def metres_per_degree(lat: float) -> tuple:
    """(metres per degree of longitude, metres per degree of latitude) on the WGS84 ellipsoid at this latitude."""
    e2 = WGS84_F * (2 - WGS84_F)
    s = math.sin(math.radians(lat))
    w = math.sqrt(1 - e2 * s * s)
    m_lat = WGS84_A * (1 - e2) / w ** 3 * math.pi / 180.0
    m_lon = WGS84_A / w * math.cos(math.radians(lat)) * math.pi / 180.0
    return m_lon, m_lat


@dataclass
class Grid:
    """elev[row, col] in metres; row 0 is the southern edge. x and y are cell-centre coordinates in metres."""
    x: np.ndarray
    y: np.ndarray
    elev: np.ndarray
    lat0: float
    lon0: float
    source: str = ""
    voids_filled: int = 0
    _slope: np.ndarray | None = field(default=None, repr=False)

    @property
    def res(self) -> float:
        return float(self.x[1] - self.x[0])

    @property
    def shape(self) -> tuple:
        return self.elev.shape

    def to_xy(self, lat, lon) -> tuple:
        m_lon, m_lat = metres_per_degree(self.lat0)
        return (np.asarray(lon, dtype=float) - self.lon0) * m_lon, (np.asarray(lat, dtype=float) - self.lat0) * m_lat

    def to_latlon(self, x, y) -> tuple:
        m_lon, m_lat = metres_per_degree(self.lat0)
        return self.lat0 + np.asarray(y, dtype=float) / m_lat, self.lon0 + np.asarray(x, dtype=float) / m_lon

    def index(self, x, y) -> tuple:
        """Nearest (row, col) of points, clipped to the grid."""
        col = np.clip(np.rint((np.asarray(x, dtype=float) - self.x[0]) / self.res).astype(int), 0, len(self.x) - 1)
        row = np.clip(np.rint((np.asarray(y, dtype=float) - self.y[0]) / self.res).astype(int), 0, len(self.y) - 1)
        return row, col

    def inside(self, x, y):
        h = self.res / 2
        x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
        return (x >= self.x[0] - h) & (x <= self.x[-1] + h) & (y >= self.y[0] - h) & (y <= self.y[-1] + h)

    def sample(self, x, y, values: np.ndarray | None = None):
        """Bilinear value of the terrain (or another grid of the same shape) at points."""
        v = self.elev if values is None else values
        fx = np.clip((np.asarray(x, dtype=float) - self.x[0]) / self.res, 0, len(self.x) - 1)
        fy = np.clip((np.asarray(y, dtype=float) - self.y[0]) / self.res, 0, len(self.y) - 1)
        c0, r0 = np.floor(fx).astype(int), np.floor(fy).astype(int)
        c1, r1 = np.minimum(c0 + 1, len(self.x) - 1), np.minimum(r0 + 1, len(self.y) - 1)
        tx, ty = fx - c0, fy - r0
        return (v[r0, c0] * (1 - tx) * (1 - ty) + v[r0, c1] * tx * (1 - ty)
                + v[r1, c0] * (1 - tx) * ty + v[r1, c1] * tx * ty)

    @property
    def slope_pct(self) -> np.ndarray:
        if self._slope is None:
            gy, gx = np.gradient(self.elev, self.res, self.res)
            self._slope = 100.0 * np.hypot(gx, gy)
        return self._slope

    def slope_at(self, x, y):
        """Slope (per cent) of the ground at points that need not sit on a cell: differences half a cell either
        side, read between the cells. On a cell centre this is the cell's own slope. Interpolating the cells' slopes
        instead gives a ridge crest that runs between two cells the slope of its flanks (seen on a real ridge,
        where it made gentle crest positions look several times steeper than they are)."""
        h = self.res / 2.0
        x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
        gx = (self.sample(x + h, y) - self.sample(x - h, y)) / (2 * h)
        gy = (self.sample(x, y + h) - self.sample(x, y - h)) / (2 * h)
        return 100.0 * np.hypot(gx, gy)

    def steep_share_round(self, x, y, radius_m: float = 3500.0, limit_pct: float = 30.0) -> np.ndarray:
        """Per cent of the ground within radius_m of each point that is steeper than limit_pct, counting the cells of
        the grid that fall within the circle (a point near the edge is judged on the part of the circle mapped). A
        screening measure of how complex the terrain round a mast or a turbine is."""
        xs, ys = np.atleast_1d(np.asarray(x, dtype=float)), np.atleast_1d(np.asarray(y, dtype=float))
        steep = self.slope_pct > limit_pct
        out = np.full(xs.shape, np.nan)
        for i, (px, py) in enumerate(zip(xs, ys)):
            cols = np.flatnonzero(np.abs(self.x - px) <= radius_m)
            rows = np.flatnonzero(np.abs(self.y - py) <= radius_m)
            if not len(cols) or not len(rows):
                continue
            dx = self.x[cols][None, :] - px
            dy = self.y[rows][:, None] - py
            disk = dx * dx + dy * dy <= radius_m * radius_m
            if disk.any():
                out[i] = 100.0 * float(steep[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1][disk].mean())
        return out

    def mesh(self) -> tuple:
        return np.meshgrid(self.x, self.y)

    def crop_slices(self, half_m: float) -> tuple:
        """Row and column slices of the cells within half_m of the origin (the part to keep after a model run:
        flow models are not trusted near the edge of their domain)."""
        keep_x = np.flatnonzero(np.abs(self.x) <= half_m + 1e-6)
        keep_y = np.flatnonzero(np.abs(self.y) <= half_m + 1e-6)
        return slice(keep_y[0], keep_y[-1] + 1), slice(keep_x[0], keep_x[-1] + 1)

    def crop(self, half_m: float) -> "Grid":
        ry, rx = self.crop_slices(half_m)
        return Grid(x=self.x[rx].copy(), y=self.y[ry].copy(), elev=self.elev[ry, rx].copy(), lat0=self.lat0,
                    lon0=self.lon0, source=self.source, voids_filled=self.voids_filled)

    def save(self, path) -> None:
        np.savez_compressed(path, x=self.x, y=self.y, elev=self.elev.astype("float32"), lat0=self.lat0, lon0=self.lon0,
                            source=self.source, voids_filled=self.voids_filled)

    @classmethod
    def load(cls, path) -> "Grid":
        d = np.load(path, allow_pickle=False)
        return cls(x=d["x"], y=d["y"], elev=d["elev"].astype(float), lat0=float(d["lat0"]), lon0=float(d["lon0"]),
                   source=str(d["source"]), voids_filled=int(d["voids_filled"]))


def local_axes(half_km: float, res: float) -> np.ndarray:
    n = int(round(half_km * 1000.0 / res))
    return np.arange(-n, n + 1, dtype=float) * res


def from_function(fn, lat0: float, lon0: float, half_km: float = 10.0, res: float = 100.0, source: str = "") -> Grid:
    """Terrain from a function elev = fn(x, y) in local metres (tests, demo)."""
    ax = local_axes(half_km, res)
    X, Y = np.meshgrid(ax, ax)
    return Grid(x=ax, y=ax.copy(), elev=np.asarray(fn(X, Y), dtype=float), lat0=lat0, lon0=lon0, source=source)


# ---------- SRTM ----------

def tile_name(lat: float, lon: float) -> str:
    """Name of the 1 x 1 degree tile holding a point; tiles are named by their south-west corner."""
    la, lo = math.floor(lat), math.floor(lon)
    return f"{'N' if la >= 0 else 'S'}{abs(la):02d}{'E' if lo >= 0 else 'W'}{abs(lo):03d}"


def tile_corner(name: str) -> tuple:
    la = int(name[1:3]) * (1 if name[0] == "N" else -1)
    lo = int(name[4:7]) * (1 if name[3] == "E" else -1)
    return la, lo


def needed_tiles(lat0: float, lon0: float, half_km: float) -> list:
    m_lon, m_lat = metres_per_degree(lat0)
    dlat, dlon = half_km * 1000.0 / m_lat, half_km * 1000.0 / m_lon
    names = {tile_name(la, lo) for la in (lat0 - dlat, lat0 + dlat) for lo in (lon0 - dlon, lon0 + dlon)}
    return sorted(names)


def tile_url(name: str) -> str:
    return SRTM_URL.format(band=name[:3], name=name)


def fetch_tiles(names, dest_dir, timeout: float = 120.0, log=print) -> list:
    """Downloads missing tiles (about 10-25 MB each) into dest_dir. Needs an internet connection."""
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    got = []
    for name in names:
        path = dest / f"{name}.hgt"
        if not path.is_file():
            url = tile_url(name)
            log(f"Downloading terrain tile {name} ...")
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "bohemian-wind"})
                with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (fixed https host)
                    raw = gzip.decompress(r.read())
            except (urllib.error.URLError, OSError) as e:
                raise TerrainError(f"Could not download terrain tile {name} ({e}). Check the internet connection, or "
                                   f"download {url} in a browser, unzip it and put {name}.hgt in {dest}.") from e
            path.write_bytes(raw)
        got.append(path)
    return got


def read_hgt(path) -> np.ndarray:
    """One tile as float metres, rows north to south (as stored), voids as NaN. 3601 or 1201 samples a side."""
    raw = np.fromfile(path, dtype=">i2")
    n = int(round(math.sqrt(raw.size)))
    if n * n != raw.size or n not in (3601, 1201):
        raise TerrainError(f"{Path(path).name} is not an SRTM .hgt tile (unexpected size).")
    a = raw.reshape(n, n).astype(float)
    a[a == VOID] = np.nan
    return a


def from_srtm(tile_dir, lat0: float, lon0: float, half_km: float = 10.0, res: float = 100.0) -> Grid:
    """Local grid sampled (bilinear) from the SRTM tiles in tile_dir. Missing tiles are named in the error."""
    ax = local_axes(half_km, res)
    X, Y = np.meshgrid(ax, ax)
    m_lon, m_lat = metres_per_degree(lat0)
    lat, lon = lat0 + Y / m_lat, lon0 + X / m_lon
    la_i, lo_i = np.floor(lat).astype(int), np.floor(lon).astype(int)
    elev = np.full(X.shape, np.nan)
    missing = []
    for la, lo in sorted(set(zip(la_i.ravel().tolist(), lo_i.ravel().tolist()))):
        name = tile_name(la + 0.5, lo + 0.5)
        path = Path(tile_dir) / f"{name}.hgt"
        if not path.is_file():
            missing.append(name)
            continue
        t = read_hgt(path)
        n = t.shape[0] - 1
        m = (la_i == la) & (lo_i == lo)
        fr = (la + 1 - lat[m]) * n          # row from the northern edge
        fc = (lon[m] - lo) * n
        r0, c0 = np.clip(np.floor(fr).astype(int), 0, n - 1), np.clip(np.floor(fc).astype(int), 0, n - 1)
        tr, tc = fr - r0, fc - c0
        elev[m] = (t[r0, c0] * (1 - tr) * (1 - tc) + t[r0, c0 + 1] * (1 - tr) * tc
                   + t[r0 + 1, c0] * tr * (1 - tc) + t[r0 + 1, c0 + 1] * tr * tc)
    if missing:
        raise TerrainError("Terrain tiles missing: " + ", ".join(missing) + f". Put the .hgt files in {tile_dir} "
                           "(the app can download them), then run again.")
    voids = int(np.isnan(elev).sum())
    if voids == elev.size:
        raise TerrainError("The terrain tiles hold no elevation data for this area.")
    if voids:
        elev = _fill(elev)
    return Grid(x=ax, y=ax.copy(), elev=elev, lat0=lat0, lon0=lon0, voids_filled=voids,
                source="SRTM 1 arc-second: " + ", ".join(needed_tiles(lat0, lon0, half_km)))


def _fill(a: np.ndarray) -> np.ndarray:
    """Fills voids from their valid neighbours, working inwards; whatever is left takes the mean."""
    a = a.copy()
    for _ in range(50):
        nan = np.isnan(a)
        if not nan.any():
            break
        p = np.pad(a, 1, constant_values=np.nan)
        stack = np.stack([p[:-2, 1:-1], p[2:, 1:-1], p[1:-1, :-2], p[1:-1, 2:]])
        cnt = np.isfinite(stack).sum(axis=0)
        mean = np.where(cnt > 0, np.nansum(stack, axis=0) / np.maximum(cnt, 1), np.nan)
        a[nan] = mean[nan]
    a[np.isnan(a)] = np.nanmean(a)
    return a


# ---------- UTM (for coordinate tables in reports) ----------

def utm(lat: float, lon: float) -> dict:
    """WGS84 latitude and longitude to UTM (Krueger series, good to a millimetre within a zone)."""
    zone = int(math.floor((lon + 180.0) / 6.0)) % 60 + 1
    lon_c = math.radians(zone * 6 - 183)
    n = WGS84_F / (2 - WGS84_F)
    A = WGS84_A / (1 + n) * (1 + n ** 2 / 4 + n ** 4 / 64)
    alpha = (n / 2 - 2 * n ** 2 / 3 + 5 * n ** 3 / 16, 13 * n ** 2 / 48 - 3 * n ** 3 / 5, 61 * n ** 3 / 240)
    phi, lam = math.radians(lat), math.radians(lon) - lon_c
    c = 2 * math.sqrt(n) / (1 + n)
    t = math.sinh(math.atanh(math.sin(phi)) - c * math.atanh(c * math.sin(phi)))
    xi, eta = math.atan2(t, math.cos(lam)), math.atanh(math.sin(lam) / math.sqrt(1 + t * t))
    e = eta + sum(a * math.cos(2 * j * xi) * math.sinh(2 * j * eta) for j, a in enumerate(alpha, 1))
    nn = xi + sum(a * math.sin(2 * j * xi) * math.cosh(2 * j * eta) for j, a in enumerate(alpha, 1))
    south = lat < 0
    return {"zone": zone, "hemisphere": "S" if south else "N", "easting": 500000.0 + 0.9996 * A * e,
            "northing": (10000000.0 if south else 0.0) + 0.9996 * A * nn,
            "epsg": (32700 if south else 32600) + zone}
