"""Buildings round a site, from open data.

Homes decide where turbines may stand and where noise and shadow flicker are checked, so a site whose buildings are
missing from the map looks emptier than it is. No open source is complete. OpenStreetMap is traced by volunteers:
densely in one valley, hardly at all in the next. Microsoft's building footprints are detected by machine in
satellite and aerial imagery: they find settlements nobody has traced and miss small structures. On a real site the
ground that OpenStreetMap showed as free of buildings held thousands of them in Microsoft's data, while Microsoft
found only two thirds of the structures where OpenStreetMap had been traced carefully.

So this module downloads both for the area round the site, merges them, and says where they disagree. The user's own
file of homes then goes on top (see combine): what the developer has marked by hand wins.

Sources and their terms
- OpenStreetMap, through the Overpass API: (c) OpenStreetMap contributors, Open Database License 1.0.
- Microsoft Global ML Building Footprints: Microsoft, Community Data License Agreement - Permissive 2.0.
Both ask that their notice travels with the data: ATTRIBUTION holds the lines, and the permitting pack prints them.

Limits. Both sources list buildings of every kind, not homes. The footprints are machine detections in imagery of
mixed dates and are not checked on the ground. Neither is a survey.
"""
from __future__ import annotations

import csv
import gzip
import http.client
import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from datetime import date
from pathlib import Path

import numpy as np
from matplotlib.path import Path as MplPath

from .. import __version__
from ..terrain import metres_per_degree


class BuildingsError(RuntimeError):
    """A problem the user can act on; the message says how."""


# public Overpass servers, tried in turn: each is busy at times and answers with an error or not at all
OVERPASS_URLS = ("https://overpass-api.de/api/interpreter", "https://overpass.private.coffee/api/interpreter",
                 "https://overpass.kumi.systems/api/interpreter")
# Microsoft moves the list of its files with every release and announces the address in the repository's README
MS_README = "https://raw.githubusercontent.com/microsoft/GlobalMLBuildingFootprints/main/README.md"
MS_MANIFESTS = ("https://bfppub.blob.core.windows.net/%24web/2026-11-12/dataset-links.csv",
                "https://minedbuildings.z5.web.core.windows.net/global-buildings/dataset-links.csv")
MS_ZOOM = 9                  # the footprints come in files of one Bing tile of level 9 each (about 78 km at the equator)
MANIFEST_DAYS = 30           # the list of files is fetched again after this many days
MERGE_M = 30.0               # a footprint this close to an OpenStreetMap building, or to a mark of the user's, is the same building
MARGIN_KM = 2.0              # fetched beyond the search radius: homes just outside it still hear the turbines
MAX_HALF_KM = 32.0
CELLS = 8                    # the two sources are compared on a grid of this many cells each way
MIN_CELL = 20                # buildings known from one source only before a cell counts as a gap in the other
CHUNK = 1 << 18
MB = 1 << 20                 # the list of Microsoft's files gives sizes in these, and so does the progress
OSM_PART_KM = 6.0            # OpenStreetMap is asked in parts of at most this size: a busy server answers a small question
OSM_PAUSE_S = 1.0            # between two questions to a public server
NOT_A_HOME = re.compile(r"^\s*(not a home|remove)\b", re.IGNORECASE)
ATTRIBUTION = {
    "osm": "OpenStreetMap: © OpenStreetMap contributors, Open Database License 1.0 (openstreetmap.org/copyright).",
    "microsoft": "Microsoft Global ML Building Footprints: Microsoft, Community Data License Agreement – Permissive 2.0 "
                 "(cdla.dev/permissive-2-0).",
}
NAMES = {"osm": "OpenStreetMap", "microsoft": "Microsoft's footprints"}
FILE = "buildings.csv"
OSM_COPY = "buildings_osm.json"   # the OpenStreetMap answer, kept beside FILE for the rest of the day


def default_cache() -> Path:
    """Where downloaded files are kept between projects: a site's files can run to a hundred megabytes."""
    root = os.environ.get("BOHEMIAN_WIND_CACHE")
    return (Path(root) if root else Path.home() / ".bohemian-wind" / "cache") / "buildings"


# ---------- Bing tiles ----------

def tile_xy(lat: float, lon: float, zoom: int = MS_ZOOM) -> tuple:
    n = 1 << zoom
    lat = min(max(lat, -85.05112878), 85.05112878)
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
    return min(max(x, 0), n - 1), min(max(y, 0), n - 1)


def quadkey(x: int, y: int, zoom: int = MS_ZOOM) -> str:
    return "".join(str(((x >> i) & 1) + 2 * ((y >> i) & 1)) for i in range(zoom - 1, -1, -1))


def quadkeys(south: float, west: float, north: float, east: float, zoom: int = MS_ZOOM) -> list:
    """Every tile the box touches, north-west first."""
    x0, y1 = tile_xy(south, west, zoom)
    x1, y0 = tile_xy(north, east, zoom)
    return [quadkey(x, y, zoom) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]


# ---------- downloads ----------

def _open(url: str, *, data: bytes | None = None, headers: dict | None = None, timeout: float = 60.0):
    """The one place that touches the network (the tests replace it)."""
    req = urllib.request.Request(url, data=data, headers={"User-Agent": f"bohemian-wind/{__version__}", **(headers or {})})
    return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 (fixed https hosts)


def _short(e) -> str:
    return re.sub(r"\s+", " ", str(e)).strip()[:140] or type(e).__name__


def _int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def download(url: str, dest, *, log=print, label: str = "", timeout: float = 60.0) -> Path:
    """Fetches url into dest unless it is there already. A download that stops keeps what arrived (dest + '.part') and
    the next call carries on from there, so a large file survives a poor connection."""
    dest = Path(dest)
    if dest.is_file():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.is_file() else 0
    what = label or url
    whole = None
    try:
        try:
            r = _open(url, headers={"Range": f"bytes={have}-"} if have else None, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code == 416 and have:           # nothing left to send: what arrived earlier is the whole file
                part.replace(dest)
                return dest
            raise
        with r:
            if not (have and getattr(r, "status", 200) == 206):
                have = 0                         # no part file, or a server that sends the whole file again
            left = _int(r.headers.get("Content-Length"))
            whole = have + left if left is not None else None
            got, told = have, have
            step = max((whole or 0) // 8, 20_000_000)
            if have:
                log(f"  {what}: carrying on from {have / MB:.0f} MB ...")
            with open(part, "ab" if have else "wb") as fh:
                while True:
                    block = r.read(CHUNK)
                    if not block:
                        break
                    fh.write(block)
                    got += len(block)
                    if got - told >= step:
                        told = got
                        log(f"  {what}: {got / MB:.0f}" + (f" of {whole / MB:.0f} MB" if whole else " MB"))
    except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
        kept = part.stat().st_size if part.is_file() else 0
        raise BuildingsError(f"The download of {what} stopped ({_short(e)}). "
                             + (f"The first {kept / MB:.0f} MB are kept: get the buildings again to carry on from there."
                                if kept > CHUNK else "Check the internet connection and get the buildings again.")) from e
    size = part.stat().st_size
    if whole is not None and size != whole:
        raise BuildingsError(f"The download of {what} came {whole - size:,} bytes short. Get the buildings again to "
                             "carry on from there.")
    part.replace(dest)
    return dest


# ---------- Microsoft's footprints ----------

def manifest_url(readme: str) -> str | None:
    """The address of the current list of files, from the text of Microsoft's README: the newest dated one."""
    urls = re.findall(r"https://[^\s)\"'<>]+/dataset-links\.csv", readme)
    dated = sorted(((m.group(1), u) for u in urls
                    for m in [re.search(r"/(\d{4}-\d{2}-\d{2})/dataset-links\.csv$", u)] if m), reverse=True)
    return dated[0][1] if dated else (urls[0] if urls else None)


def read_manifest(path) -> dict:
    """{quadkey: [{"location", "url", "size", "date"}, ...]}: a tile on a border has a file for each country."""
    out: dict = {}
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            q, url = (r.get("QuadKey") or "").strip(), (r.get("Url") or "").strip()
            if q.isdigit() and url.startswith("https://"):
                out.setdefault(q.zfill(MS_ZOOM), []).append(
                    {"location": (r.get("Location") or "").strip(), "url": url, "size": (r.get("Size") or "").strip(),
                     "date": (r.get("UploadDate") or "").strip()})
    return out


def manifest(cache_dir, *, log=print, today: date | None = None) -> tuple:
    """(the list of files by quadkey, {"url", "fetched"}). Kept in the cache and fetched again after MANIFEST_DAYS."""
    cache = Path(cache_dir) / "microsoft"
    path, meta_path = cache / "dataset-links.csv", cache / "dataset-links.json"
    today = today or date.today()
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        age = (today - date.fromisoformat(meta["fetched"])).days
    except (OSError, ValueError, KeyError, TypeError):
        meta, age = {}, None
    if not (path.is_file() and age is not None and 0 <= age < MANIFEST_DAYS):
        candidates = []
        log("  The list of Microsoft's building files ...")
        try:
            with _open(MS_README, timeout=30.0) as r:
                found = manifest_url(r.read().decode("utf-8", errors="replace"))
            if found:
                candidates.append(found)
        except (urllib.error.URLError, OSError, http.client.HTTPException):
            pass
        candidates += [u for u in MS_MANIFESTS if u not in candidates]
        failure = None
        for url in candidates:
            new = cache / "dataset-links.new.csv"
            for old in (new, new.with_name(new.name + ".part")):
                old.unlink(missing_ok=True)
            try:
                download(url, new, log=log, label="the list of Microsoft's building files")
                if not read_manifest(new):
                    raise BuildingsError("the list is empty")
            except (BuildingsError, OSError, csv.Error, UnicodeDecodeError) as e:
                failure = e
                continue
            new.replace(path)
            meta = {"url": url, "fetched": today.isoformat()}
            meta_path.write_text(json.dumps(meta), encoding="utf-8")
            break
        else:
            if not path.is_file():
                raise BuildingsError(f"The list of Microsoft's building files could not be downloaded ({_short(failure)}).")
            log(f"  The list of Microsoft's building files could not be refreshed; the copy of {meta.get('fetched', 'an earlier day')} is used.")
    return read_manifest(path), meta


_FIRST = re.compile(r'"coordinates"\s*:\s*\[+\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)')


def read_microsoft(path, south: float, west: float, north: float, east: float) -> list:
    """[(lat, lon, area in m2)] of the footprints whose centre lies in the box. The files hold one GeoJSON feature a
    line, hundreds of thousands of lines: a line is parsed only when its first corner is near the box."""
    m_lon, m_lat = metres_per_degree((south + north) / 2.0)
    pad = 0.003                                 # about 300 m: a first corner can lie outside while the centre is inside
    out = []
    try:
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = _FIRST.search(line)
                if not m:
                    continue
                lon, lat = float(m.group(1)), float(m.group(2))
                if not (west - pad <= lon <= east + pad and south - pad <= lat <= north + pad):
                    continue
                try:
                    g = json.loads(line).get("geometry") or {}
                except ValueError:
                    continue
                polys = g.get("coordinates") or []
                if g.get("type") == "Polygon":
                    polys = [polys]
                elif g.get("type") != "MultiPolygon":
                    continue
                for poly in polys:
                    ring = poly[0] if poly else []
                    pts = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring
                    if len(pts) < 3:
                        continue
                    clon, clat = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
                    if not (west <= clon <= east and south <= clat <= north):
                        continue
                    xs, ys = [(p[0] - clon) * m_lon for p in pts], [(p[1] - clat) * m_lat for p in pts]
                    area = 0.5 * abs(sum(xs[i] * ys[i - 1] - xs[i - 1] * ys[i] for i in range(len(pts))))
                    out.append((clat, clon, round(area, 1)))
    except (OSError, EOFError, zlib.error) as e:
        raise BuildingsError(f"{Path(path).name} could not be read ({_short(e)}).") from e
    return out


def _megabytes(size: str) -> float:
    m = re.match(r"\s*([\d.]+)\s*([KMG]?B)?\s*$", size or "", re.IGNORECASE)
    if not m:
        return 0.0
    return float(m.group(1)) * {"B": 1e-6, "KB": 1e-3, "MB": 1.0, "GB": 1e3}[(m.group(2) or "B").upper()]


def fetch_microsoft(south: float, west: float, north: float, east: float, cache_dir, *, log=print,
                    today: date | None = None) -> tuple:
    """([(lat, lon, area)], {"release", "tiles", "megabytes"}). Files already in the cache are not downloaded again."""
    files, meta = manifest(cache_dir, log=log, today=today)
    keys = quadkeys(south, west, north, east)
    rows = [(q, r) for q in keys for r in files.get(q, [])]
    out, mb, release = [], 0.0, ""
    for q, r in rows:
        when = re.search(r"/(\d{4}-\d{2}-\d{2})/", r["url"])
        release = max(release, when.group(1) if when else r["date"])
        name = f"{q}_{re.sub(r'[^A-Za-z0-9]+', '', r['location']) or 'x'}.csv.gz"
        path = Path(cache_dir) / "microsoft" / ((when.group(1) if when else r["date"]) or "undated") / name
        size = _megabytes(r["size"])
        if not path.is_file():
            log(f"  Microsoft's footprints, file {q} ({r['location']}, {size:.0f} MB) ..." if size >= 1
                else f"  Microsoft's footprints, file {q} ({r['location']}) ...")
        download(r["url"], path, log=log, label=f"file {q}")
        try:
            out += read_microsoft(path, south, west, north, east)
        except BuildingsError as e:
            path.unlink(missing_ok=True)           # a damaged copy must not be met again
            raise BuildingsError(f"{e} The copy was removed: get the buildings again.") from e
        mb += size
    return out, {"release": release, "tiles": len(rows), "megabytes": round(mb, 1), "list": meta.get("url", "")}


# ---------- OpenStreetMap ----------

def overpass_query(south: float, west: float, north: float, east: float) -> str:
    """Ways and relations come with their tags and centre only (their corner points would triple the answer); a
    building mapped as a single node comes with its position."""
    box = f"{south:.6f},{west:.6f},{north:.6f},{east:.6f}"
    return (f'[out:json][timeout:120];(way["building"]({box});relation["building"]({box}););out tags center qt;'
            f'node["building"]({box});out body qt;')


def parse_overpass(obj: dict) -> list:
    """[(lat, lon, "w123")]: the centre of every way and relation tagged as a building, and building nodes."""
    out = []
    for e in obj.get("elements") or []:
        if (e.get("tags") or {}).get("building") == "no":
            continue
        c = e.get("center") or e
        try:
            out.append((float(c["lat"]), float(c["lon"]), f"{str(e.get('type') or 'x')[:1]}{e.get('id', '')}"))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def osm_parts(south: float, west: float, north: float, east: float) -> list:
    """The box cut into parts of at most OSM_PART_KM a side: [(key, (south, west, north, east))]."""
    m_lon, m_lat = metres_per_degree((south + north) / 2.0)
    ny = max(1, math.ceil((north - south) * m_lat / 1000.0 / OSM_PART_KM - 1e-6))
    nx = max(1, math.ceil((east - west) * m_lon / 1000.0 / OSM_PART_KM - 1e-6))
    dy, dx = (north - south) / ny, (east - west) / nx
    return [(f"{j},{i}", (south + j * dy, west + i * dx, south + (j + 1) * dy, west + (i + 1) * dx))
            for j in range(ny) for i in range(nx)]


def fetch_osm(south: float, west: float, north: float, east: float, *, log=print, timeout: float = 150.0, copy=None,
              today: date | None = None) -> tuple:
    """([(lat, lon, id)], server). The area is asked for in parts and each public server is tried in turn, the one
    that answered last first. With copy (a file path) the parts that arrived are kept for the day, so a second go
    asks only for what is missing."""
    today = today or date.today()
    box = [round(v, 6) for v in (south, west, north, east)]
    state = {"box": box, "fetched": today.isoformat(), "server": "", "parts": {}}
    if copy is not None:
        try:
            kept = json.loads(Path(copy).read_text(encoding="utf-8"))
            if kept["box"] == box and kept["fetched"] == state["fetched"] and isinstance(kept["parts"], dict):
                state = kept
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def save():
        if copy is not None:
            Path(copy).parent.mkdir(parents=True, exist_ok=True)
            Path(copy).write_text(json.dumps(state), encoding="utf-8")

    parts = osm_parts(south, west, north, east)
    todo = [(k, b) for k, b in parts if k not in state["parts"]]
    if not todo:
        log("  OpenStreetMap: the answer fetched earlier today is used again.")
    elif len(todo) < len(parts):
        log(f"  OpenStreetMap buildings: {len(parts) - len(todo)} of {len(parts)} parts arrived earlier today; the rest now ...")
    else:
        log(f"  OpenStreetMap buildings, in {len(parts)} parts ..." if len(parts) > 1 else "  OpenStreetMap buildings ...")
    servers = sorted(OVERPASS_URLS, key=lambda u: urllib.parse.urlsplit(u).netloc != state["server"])
    for n, (key, b) in enumerate(todo):
        data = urllib.parse.urlencode({"data": overpass_query(*b)}).encode()
        failure = None
        for url in list(servers):
            host = urllib.parse.urlsplit(url).netloc
            try:
                with _open(url, data=data, timeout=timeout) as r:
                    obj = json.loads(r.read())
                if not isinstance(obj, dict) or "elements" not in obj:
                    raise ValueError("no buildings list in the answer")
                if "error" in str(obj.get("remark") or "").lower():      # a query cut short answers with what it had
                    raise ValueError(str(obj["remark"]))
            except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError) as e:
                failure = e
                log(f"    no usable answer from {host} ({_short(e)})")
                servers.remove(url)
                servers.append(url)              # a server that failed goes to the back of the queue
                continue
            state["parts"][key], state["server"] = parse_overpass(obj), host
            save()
            break
        else:
            done = len(parts) - len(todo) + n
            raise BuildingsError(f"OpenStreetMap could not be reached ({_short(failure)})."
                                 + (f" {done} of {len(parts)} parts arrived and are kept for today." if done and copy is not None else ""))
        if n + 1 < len(todo) and OSM_PAUSE_S:
            time.sleep(OSM_PAUSE_S)
    seen, rows = set(), []
    for key, _ in parts:                         # a building on the edge of two parts comes with both: once is enough
        for lat, lon, ident in state["parts"][key]:
            if ident not in seen:
                seen.add(ident)
                rows.append((lat, lon, ident))
    return rows, state["server"]


# ---------- merging and comparing ----------

def _near(a: np.ndarray, b: np.ndarray, radius: float) -> np.ndarray:
    """For each point of a (n x 2, metres): is a point of b within radius?"""
    out = np.zeros(len(a), bool)
    if not len(a) or not len(b):
        return out
    cells: dict = {}
    for j, (cx, cy) in enumerate(np.floor(b / radius).astype(np.int64).tolist()):
        cells.setdefault((cx, cy), []).append(j)
    r2 = radius * radius
    for i, ((x, y), (cx, cy)) in enumerate(zip(a.tolist(), np.floor(a / radius).astype(np.int64).tolist())):
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in cells.get((cx + dx, cy + dy), ()):
                    if (b[j, 0] - x) ** 2 + (b[j, 1] - y) ** 2 <= r2:
                        out[i] = True
                        break
                if out[i]:
                    break
            if out[i]:
                break
    return out


def _xy(points: list, lat0: float, lon0: float) -> np.ndarray:
    m_lon, m_lat = metres_per_degree(lat0)
    if not points:
        return np.zeros((0, 2))
    p = np.array([(q[0], q[1]) for q in points], dtype=float)
    return np.column_stack([(p[:, 1] - lon0) * m_lon, (p[:, 0] - lat0) * m_lat])


def _where(flag: np.ndarray, weight: np.ndarray, half_m: float) -> str:
    """In words, where the flagged cells lie round the site."""
    n = flag.shape[0]
    if flag.mean() >= 0.7:
        return "across most of the area"
    size = 2.0 * half_m / n
    centre = (np.arange(n) + 0.5) * size - half_m
    X, Y = np.meshgrid(centre, centre)
    w = np.where(flag, weight, 0.0).astype(float)
    vx, vy = float((w * X).sum() / w.sum()), float((w * Y).sum() / w.sum())
    reach = float((w * np.hypot(X, Y)).sum() / w.sum())
    if math.hypot(vx, vy) < 0.4 * reach:
        return "on several sides of the site"
    names = ("north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west")
    return f"mostly {names[int(round(math.degrees(math.atan2(vx, vy)) / 45.0)) % 8]} of the site"


def compare(o_xy: np.ndarray, o_alone: np.ndarray, m_xy: np.ndarray, m_alone: np.ndarray, half_m: float) -> dict:
    """Where the two sources disagree. A cell is a gap in one source when the other knows of at least MIN_CELL
    buildings there that the first lacks, and they are at least half of the buildings of the cell."""
    size = 2.0 * half_m / CELLS

    def grid(xy, pick=None):
        if pick is not None:
            xy = xy[pick]
        if not len(xy):
            return np.zeros((CELLS, CELLS))
        c = np.clip(np.floor((xy + half_m) / size).astype(int), 0, CELLS - 1)
        return np.bincount(c[:, 1] * CELLS + c[:, 0], minlength=CELLS * CELLS).reshape(CELLS, CELLS).astype(float)

    osm, ms, osm_alone, ms_alone = grid(o_xy), grid(m_xy), grid(o_xy, o_alone), grid(m_xy, m_alone)
    out = {"cell_km": round(size / 1000.0, 2)}
    for key, alone, other in (("osm", ms_alone, osm), ("microsoft", osm_alone, ms)):
        gap = (alone >= MIN_CELL) & (alone >= 0.5 * (other + alone))
        if gap.any():
            out[key] = {"km2": round(float(gap.sum()) * (size / 1000.0) ** 2, 1), "buildings": int(alone[gap].sum()),
                        "where": _where(gap, alone, half_m), "share_of_area_pct": round(100.0 * float(gap.mean()), 0)}
    return out


def merge(osm: list, ms: list, lat0: float, lon0: float, half_km: float) -> tuple:
    """(rows of the merged list, counts, comparison). Every OpenStreetMap building is kept; a Microsoft footprint is
    added when no OpenStreetMap building lies within MERGE_M of its centre."""
    o_xy, m_xy = _xy(osm, lat0, lon0), _xy(ms, lat0, lon0)
    m_has, o_has = _near(m_xy, o_xy, MERGE_M), _near(o_xy, m_xy, MERGE_M)
    rows = [{"name": f"OSM {q[2]}", "lat": q[0], "lon": q[1], "source": "osm", "area_m2": ""} for q in osm]
    extra = sorted((q for q, has in zip(ms, m_has.tolist()) if not has), key=lambda q: (-q[0], q[1]))
    rows += [{"name": f"MS {i + 1:06d}", "lat": q[0], "lon": q[1], "source": "microsoft", "area_m2": q[2]}
             for i, q in enumerate(extra)]
    counts = {"n": len(rows), "n_osm": len(osm), "n_microsoft": len(ms), "n_added_from_microsoft": len(extra),
              "n_osm_only": int((~o_has).sum())}
    return rows, counts, compare(o_xy, ~o_has, m_xy, ~m_has, half_km * 1000.0)


def gap_warnings(cmp: dict, sources: dict) -> list:
    out = []
    for key, other in (("osm", "microsoft"), ("microsoft", "osm")):
        g = cmp.get(key)
        if g:
            out.append(f"{NAMES[key]} {'is' if key == 'osm' else 'are'} incomplete here. Over about {g['km2']:.0f} km², "
                       f"{g['where']}, {NAMES[other]} {'show' if other == 'microsoft' else 'has'} {g['buildings']:,} "
                       f"buildings that {NAMES[key]} {'does' if key == 'osm' else 'do'} not have.")
    for key, other in (("osm", "microsoft"), ("microsoft", "osm")):
        if not sources[key]["ok"]:
            out.append(f"{NAMES[key]} could not be reached, so the buildings come from {NAMES[other]} alone and nothing "
                       "checks them. Get the buildings again later.")
    return out


# ---------- the step ----------

def fetch(lat: float, lon: float, half_km: float, dest_dir, *, cache_dir=None, log=print, today: date | None = None) -> dict:
    """Downloads, merges and writes dest_dir/buildings.csv (name, latitude, longitude, source, area_m2). Returns the
    summary kept in the project. One source may fail: the other is used and the summary says so."""
    half_km = float(min(max(half_km, 1.0), MAX_HALF_KM))
    today = today or date.today()
    m_lon, m_lat = metres_per_degree(lat)
    dlat, dlon = half_km * 1000.0 / m_lat, half_km * 1000.0 / m_lon
    south, north, west, east = lat - dlat, lat + dlat, lon - dlon, lon + dlon
    log(f"Buildings within {half_km:g} km of the site, from Microsoft's footprints and from OpenStreetMap ...")
    osm, ms, sources = [], [], {}
    try:                         # the files first: what has arrived is kept, whatever happens next
        ms, info = fetch_microsoft(south, west, north, east, cache_dir or default_cache(), log=log, today=today)
        sources["microsoft"] = {"ok": True, "n": len(ms), **info}
        log(f"  {len(ms):,} footprints in Microsoft's data" + (f" (release {info['release']})." if info["release"] else "."))
    except BuildingsError as e:
        sources["microsoft"] = {"ok": False, "n": 0, "error": str(e)}
        log(f"  {e}")
    try:
        osm, server = fetch_osm(south, west, north, east, log=log, copy=Path(dest_dir) / OSM_COPY, today=today)
        sources["osm"] = {"ok": True, "n": len(osm), "server": server}
        log(f"  {len(osm):,} buildings in OpenStreetMap.")
    except BuildingsError as e:
        sources["osm"] = {"ok": False, "n": 0, "error": str(e)}
        log(f"  {e}")
    if not sources["osm"]["ok"] and not sources["microsoft"]["ok"]:
        raise BuildingsError("Neither OpenStreetMap nor Microsoft's building files could be reached. Check the internet "
                             f"connection and get the buildings again. ({sources['osm']['error']} {sources['microsoft']['error']})")
    rows, counts, cmp = merge(osm, ms, lat, lon, half_km)
    both = sources["osm"]["ok"] and sources["microsoft"]["ok"]
    warnings = gap_warnings(cmp if both else {}, sources)
    if sources["microsoft"]["ok"] and not sources["microsoft"]["tiles"]:
        warnings.append("Microsoft's data holds no file for this area: either nothing was detected there or the area is "
                        "not covered. The buildings come from OpenStreetMap alone.")
    dest = Path(dest_dir) / FILE
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["name", "latitude", "longitude", "source", "area_m2"])
        for r in rows:
            w.writerow([r["name"], f"{r['lat']:.7f}", f"{r['lon']:.7f}", r["source"], r["area_m2"]])
    log(f"  {counts['n']:,} buildings in all: {counts['n_osm']:,} from OpenStreetMap and {counts['n_added_from_microsoft']:,} "
        "more that only Microsoft's footprints show.")
    for line in warnings:
        log(f"  Caution: {line}")
    return {"file": FILE, "half_km": half_km, "fetched": today.isoformat(), **counts, "sources": sources,
            "compared": cmp if both else {}, "warnings": warnings,
            "attribution": [ATTRIBUTION[k] for k in ("osm", "microsoft") if sources[k]["ok"]]}


def load(path) -> list:
    """Rows of a buildings.csv written by fetch."""
    out = []
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                out.append({"name": r["name"], "lat": float(r["latitude"]), "lon": float(r["longitude"]),
                            "source": r.get("source") or ""})
    except (OSError, KeyError, ValueError) as e:
        raise BuildingsError(f"The buildings file could not be read ({_short(e)}). Get the buildings again.") from e
    return out


def combine(fetched: list, own: list, to_xy) -> tuple:
    """The homes the project works with: the fetched buildings with the user's own file on top.

    own: features of the user's file (environment.constraints.read_features). Its points are homes and always count.
    A fetched building within MERGE_M of one of them is the same building and gives way to it. A point named
    "not a home" takes out the fetched buildings within MERGE_M of it, and an outline of that name takes out those
    inside it: a shed, a ruin, a building the project will remove. to_xy(lat, lon) gives local metres.
    Returns (rows with name, lat, lon, x, y and source; counts)."""
    homes, marks = [], []
    for f in own:
        if NOT_A_HOME.match(f.name or ""):
            if f.kind in ("point", "polygon"):
                marks.append(f)
        elif f.kind == "point":
            homes.append(f)
    rows = []
    for f in homes:
        lat, lon = f.coords[0]
        x, y = to_xy(lat, lon)
        rows.append({"name": f.name or f"R{len(rows) + 1:03d}", "lat": lat, "lon": lon, "x": float(x), "y": float(y),
                     "source": "yours"})
    counts = {"yours": len(rows), "fetched": 0, "taken_out": 0, "replaced": 0, "marks": len(marks)}
    if not fetched:
        return rows, counts
    fx, fy = to_xy(np.array([r["lat"] for r in fetched]), np.array([r["lon"] for r in fetched]))
    fxy = np.column_stack([np.asarray(fx, dtype=float), np.asarray(fy, dtype=float)])
    out = np.zeros(len(fetched), bool)
    for f in marks:
        if f.kind == "point":
            px, py = to_xy(*f.coords[0])
            out |= np.hypot(fxy[:, 0] - float(px), fxy[:, 1] - float(py)) <= MERGE_M
        elif f.coords and len(f.coords[0]) >= 3:
            ring = np.array(f.coords[0], dtype=float)
            rx, ry = to_xy(ring[:, 0], ring[:, 1])
            out |= MplPath(np.column_stack([rx, ry])).contains_points(fxy)
    same = _near(fxy, np.array([[r["x"], r["y"]] for r in rows], dtype=float).reshape(-1, 2), MERGE_M) & ~out
    counts.update(taken_out=int(out.sum()), replaced=int(same.sum()), fetched=int((~(out | same)).sum()))
    for r, (x, y), gone in zip(fetched, fxy.tolist(), (out | same).tolist()):
        if not gone:
            rows.append({"name": r["name"], "lat": r["lat"], "lon": r["lon"], "x": x, "y": y, "source": r["source"]})
    return rows, counts
