"""Where turbines may stand: exclusion zones, buffers, setbacks and terrain limits on the site grid.

Inputs are plain map files a developer already has:
- exclusion zones: polygons (protected areas, wetlands, land not available) and lines (roads, power lines);
- homes and other receptors: points;
- an optional site boundary: polygons the turbines must stay inside.
GeoJSON, KML and KMZ are read; points can also come from a CSV with lat and lon columns.

Each rule removes cells from the buildable area and the report says how much, so the effect of every buffer is
visible. Turbine positions are cell centres.
"""
from __future__ import annotations

import csv
import io
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from matplotlib.path import Path as MplPath

from ..terrain import Grid


class ConstraintError(RuntimeError):
    pass


@dataclass
class Feature:
    kind: str                     # "polygon", "line" or "point"
    coords: list                  # polygon: list of rings [(lat, lon), ...] (first is the outline, the rest holes);
    #                               line: [(lat, lon), ...]; point: [(lat, lon)]
    name: str = ""
    props: dict = field(default_factory=dict)


# ---------- reading ----------

def _geojson_geom(g: dict, name: str, props: dict, out: list) -> None:
    t, c = g.get("type"), g.get("coordinates")
    ll = lambda pts: [(float(p[1]), float(p[0])) for p in pts]  # noqa: E731  (GeoJSON is lon, lat)
    if t == "Point":
        out.append(Feature("point", ll([c]), name, props))
    elif t == "MultiPoint":
        out.extend(Feature("point", ll([p]), name, props) for p in c)
    elif t == "LineString":
        out.append(Feature("line", ll(c), name, props))
    elif t == "MultiLineString":
        out.extend(Feature("line", ll(part), name, props) for part in c)
    elif t == "Polygon":
        out.append(Feature("polygon", [ll(r) for r in c], name, props))
    elif t == "MultiPolygon":
        out.extend(Feature("polygon", [ll(r) for r in poly], name, props) for poly in c)
    elif t == "GeometryCollection":
        for sub in g.get("geometries", []):
            _geojson_geom(sub, name, props, out)


def _read_geojson(text: str) -> list:
    d = json.loads(text)
    out: list = []
    feats = d.get("features", [d]) if d.get("type") in ("FeatureCollection", "Feature") else [{"geometry": d}]
    if d.get("type") == "Feature":
        feats = [d]
    for f in feats:
        g = f.get("geometry") or {}
        props = f.get("properties") or {}
        name = str(props.get("name") or props.get("Name") or props.get("id") or "")
        _geojson_geom(g, name, props, out)
    return out


def _kml_coords(text: str) -> list:
    pts = []
    for tok in (text or "").split():
        p = tok.split(",")
        if len(p) >= 2:
            pts.append((float(p[1]), float(p[0])))            # KML is lon, lat[, alt]
    return pts


def _read_kml(text: str) -> list:
    text = re.sub(r'\sxmlns(:\w+)?="[^"]+"', "", text)         # drop namespaces: Google Earth files vary
    text = re.sub(r"<(/?)\w+:", r"<\1", text)
    root = ET.fromstring(text)
    out = []
    for pm in root.iter("Placemark"):
        name = (pm.findtext("name") or "").strip()
        for pt in pm.iter("Point"):
            c = _kml_coords(pt.findtext("coordinates"))
            if c:
                out.append(Feature("point", c[:1], name))
        for ln in pm.iter("LineString"):
            c = _kml_coords(ln.findtext("coordinates"))
            if len(c) >= 2:
                out.append(Feature("line", c, name))
        for pg in pm.iter("Polygon"):
            rings = []
            outer = pg.find("outerBoundaryIs")
            if outer is not None:
                rings.append(_kml_coords(outer.findtext(".//coordinates")))
            for inner in pg.findall("innerBoundaryIs"):
                rings.append(_kml_coords(inner.findtext(".//coordinates")))
            if rings and len(rings[0]) >= 3:
                out.append(Feature("polygon", rings, name))
    return out


def _read_csv_points(text: str) -> list:
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        return []
    keys = {k.strip().lower(): k for k in rows[0]}
    la = next((keys[k] for k in ("lat", "latitude", "y") if k in keys), None)
    lo = next((keys[k] for k in ("lon", "long", "lng", "longitude", "x") if k in keys), None)
    nm = next((keys[k] for k in ("name", "id", "label") if k in keys), None)
    if la is None or lo is None:
        raise ConstraintError("The CSV needs columns named lat and lon (decimal degrees).")
    out = []
    for i, r in enumerate(rows):
        try:
            out.append(Feature("point", [(float(r[la]), float(r[lo]))], (r.get(nm) or f"R{i + 1}") if nm else f"R{i + 1}"))
        except (TypeError, ValueError):
            continue
    return out


def read_features(path) -> list:
    """Points, lines and polygons from a GeoJSON, KML, KMZ or (points only) CSV file, in latitude and longitude."""
    p = Path(path)
    if not p.is_file():
        raise ConstraintError(f"File not found: {p}")
    ext = p.suffix.lower()
    try:
        if ext == ".kmz":
            with zipfile.ZipFile(p) as z:
                kml = next((n for n in z.namelist() if n.lower().endswith(".kml")), None)
                if kml is None:
                    raise ConstraintError(f"{p.name} holds no KML file.")
                return _read_kml(z.read(kml).decode("utf-8", errors="replace"))
        text = p.read_text(encoding="utf-8-sig", errors="replace")
        if ext == ".kml":
            return _read_kml(text)
        if ext == ".csv":
            return _read_csv_points(text)
        if ext in (".geojson", ".json"):
            return _read_geojson(text)
    except (ValueError, ET.ParseError, zipfile.BadZipFile, KeyError) as e:
        raise ConstraintError(f"Could not read {p.name}: {e}") from e
    raise ConstraintError(f"{p.name}: file type not read. Use GeoJSON, KML, KMZ, or a CSV of points.")


def points_of(features: list, grid: Grid, prefix: str = "R") -> list:
    """Receptors (homes, schools) from point features, with local coordinates. Unnamed points are numbered."""
    out = []
    for f in features:
        if f.kind != "point":
            continue
        lat, lon = f.coords[0]
        x, y = grid.to_xy(lat, lon)
        out.append({"name": f.name or f"{prefix}{len(out) + 1:03d}", "lat": lat, "lon": lon, "x": float(x), "y": float(y),
                    "unnamed": not f.name})
    return out


# ---------- masks on the grid ----------

def _window(grid: Grid, x0, x1, y0, y1) -> tuple:
    c0 = int(np.clip(np.floor((x0 - grid.x[0]) / grid.res), 0, len(grid.x)))
    c1 = int(np.clip(np.ceil((x1 - grid.x[0]) / grid.res) + 1, 0, len(grid.x)))
    r0 = int(np.clip(np.floor((y0 - grid.y[0]) / grid.res), 0, len(grid.y)))
    r1 = int(np.clip(np.ceil((y1 - grid.y[0]) / grid.res) + 1, 0, len(grid.y)))
    return r0, r1, c0, c1


def polygon_mask(grid: Grid, feature: Feature) -> np.ndarray:
    """Cells whose centre lies inside a polygon (holes excluded)."""
    mask = np.zeros(grid.shape, bool)
    rings = [np.column_stack(grid.to_xy([p[0] for p in r], [p[1] for p in r])) for r in feature.coords if len(r) >= 3]
    if not rings:
        return mask
    outer = rings[0]
    r0, r1, c0, c1 = _window(grid, outer[:, 0].min(), outer[:, 0].max(), outer[:, 1].min(), outer[:, 1].max())
    if r1 <= r0 or c1 <= c0:
        return mask
    X, Y = np.meshgrid(grid.x[c0:c1], grid.y[r0:r1])
    pts = np.column_stack([X.ravel(), Y.ravel()])
    inside = MplPath(outer).contains_points(pts)
    for hole in rings[1:]:
        inside &= ~MplPath(hole).contains_points(pts)
    mask[r0:r1, c0:c1] = inside.reshape(X.shape)
    return mask


def near_points(grid: Grid, xy: np.ndarray, radius_m: float) -> np.ndarray:
    """Cells within radius_m of any point."""
    mask = np.zeros(grid.shape, bool)
    for x, y in np.atleast_2d(xy):
        r0, r1, c0, c1 = _window(grid, x - radius_m, x + radius_m, y - radius_m, y + radius_m)
        if r1 > r0 and c1 > c0:
            X, Y = np.meshgrid(grid.x[c0:c1], grid.y[r0:r1])
            mask[r0:r1, c0:c1] |= (X - x) ** 2 + (Y - y) ** 2 <= radius_m ** 2
    return mask


def near_line(grid: Grid, feature: Feature, radius_m: float) -> np.ndarray:
    """Cells within radius_m of a line."""
    mask = np.zeros(grid.shape, bool)
    xy = np.column_stack(grid.to_xy([p[0] for p in feature.coords], [p[1] for p in feature.coords]))
    for (ax, ay), (bx, by) in zip(xy[:-1], xy[1:]):
        r0, r1, c0, c1 = _window(grid, min(ax, bx) - radius_m, max(ax, bx) + radius_m,
                                 min(ay, by) - radius_m, max(ay, by) + radius_m)
        if r1 <= r0 or c1 <= c0:
            continue
        X, Y = np.meshgrid(grid.x[c0:c1], grid.y[r0:r1])
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy
        t = np.clip(((X - ax) * dx + (Y - ay) * dy) / L2, 0, 1) if L2 > 0 else 0.0
        mask[r0:r1, c0:c1] |= (X - (ax + t * dx)) ** 2 + (Y - (ay + t * dy)) ** 2 <= radius_m ** 2
    return mask


def grow(mask: np.ndarray, res: float, radius_m: float) -> np.ndarray:
    """Buffer: every cell within radius_m of a masked cell (disc dilation by Fourier convolution)."""
    if radius_m <= 0 or not mask.any():
        return mask.copy()
    n = int(np.ceil(radius_m / res))
    yy, xx = np.mgrid[-n:n + 1, -n:n + 1]
    disc = ((xx * res) ** 2 + (yy * res) ** 2 <= radius_m ** 2).astype(float)
    shape = (mask.shape[0] + 2 * n, mask.shape[1] + 2 * n)
    conv = np.fft.irfft2(np.fft.rfft2(mask.astype(float), shape) * np.fft.rfft2(disc, shape), shape)
    return conv[n:n + mask.shape[0], n:n + mask.shape[1]] > 0.5


# ---------- the buildable area ----------

@dataclass
class Rules:
    radius_km: float = 10.0                # search radius around the mast
    slope_max_pct: float = 15.0            # crane pads and roads; steeper ground is left out
    elevation_max_m: float | None = None   # e.g. a turbine's altitude limit
    min_mean_ws: float | None = None       # leave out cells below this mean wind speed
    exclusion_buffer_m: float = 0.0        # added round every exclusion polygon
    line_buffer_m: float = 250.0           # each side of roads and power lines
    receptor_setback_m: float = 500.0      # from homes and other receptors
    inside_boundary: bool = True           # with a boundary file: turbines stay inside it


def buildable(grid: Grid, rules: Rules, exclusions: list | None = None, receptors: list | None = None,
              boundary: list | None = None, mean_ws: np.ndarray | None = None, mast_xy=(0.0, 0.0)) -> tuple:
    """(mask of buildable cells, report). exclusions and boundary: lists of Feature; receptors: from points_of."""
    X, Y = grid.mesh()
    cell_km2 = grid.res ** 2 / 1e6
    in_radius = np.hypot(X - mast_xy[0], Y - mast_xy[1]) <= rules.radius_km * 1000.0
    ok = in_radius.copy()
    steps = [{"rule": f"Within {rules.radius_km:g} km of the mast", "removed_km2": 0.0,
              "left_km2": float(ok.sum() * cell_km2)}]

    def apply(label: str, bad: np.ndarray) -> None:
        nonlocal ok
        removed = int((ok & bad).sum())
        ok &= ~bad
        steps.append({"rule": label, "removed_km2": round(removed * cell_km2, 3), "left_km2": round(float(ok.sum() * cell_km2), 3)})

    if boundary and rules.inside_boundary:
        inside = np.zeros(grid.shape, bool)
        for f in boundary:
            if f.kind == "polygon":
                inside |= polygon_mask(grid, f)
        apply("Outside the site boundary", ~inside)
    apply(f"Slope above {rules.slope_max_pct:g} %", grid.slope_pct > rules.slope_max_pct)
    if rules.elevation_max_m is not None:
        apply(f"Ground above {rules.elevation_max_m:g} m", grid.elev > rules.elevation_max_m)
    if rules.min_mean_ws is not None and mean_ws is not None:
        apply(f"Mean wind speed below {rules.min_mean_ws:g} m/s", mean_ws < rules.min_mean_ws)
    zone = np.zeros(grid.shape, bool)
    lines = np.zeros(grid.shape, bool)
    n_poly = n_line = 0
    for f in exclusions or []:
        if f.kind == "polygon":
            zone |= polygon_mask(grid, f)
            if rules.exclusion_buffer_m > 0:      # exact distance from each cell centre to the zone's outline
                for ring in f.coords:
                    if len(ring) >= 2:
                        zone |= near_line(grid, Feature("line", list(ring)), rules.exclusion_buffer_m)
            n_poly += 1
        elif f.kind == "line":
            lines |= near_line(grid, f, rules.line_buffer_m)
            n_line += 1
    if n_poly:
        apply(f"Exclusion zones ({n_poly})" + (f" with a {rules.exclusion_buffer_m:g} m buffer" if rules.exclusion_buffer_m else ""),
              zone)
    if n_line:
        apply(f"Within {rules.line_buffer_m:g} m of roads and lines ({n_line})", lines)
    if receptors:
        xy = np.array([[r["x"], r["y"]] for r in receptors])
        apply(f"Within {rules.receptor_setback_m:g} m of homes and other receptors ({len(receptors)})",
              near_points(grid, xy, rules.receptor_setback_m))
    report = {"steps": steps, "buildable_km2": round(float(ok.sum() * cell_km2), 3), "cells": int(ok.sum()),
              "exclusion_mask_share": float((zone | lines).mean())}
    return ok, report
