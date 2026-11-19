"""Buildings from open data: the two sources, the merge, the user's file on top. Every place, building and address
here is invented, and nothing touches the network. Run: python -m pytest tests/test_buildings.py -q"""
import csv
import gzip
import json
import re
import sys
import urllib.error
import urllib.parse
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bohemian_wind.environment import buildings as bld  # noqa: E402
from bohemian_wind.environment import constraints  # noqa: E402
from bohemian_wind.terrain import metres_per_degree  # noqa: E402

LAT0, LON0 = 10.0, 20.0                        # an invented site
TODAY = date(2026, 11, 19)
FILES = "https://files.example"
README = (f"* 2026-07-24 - moved: {FILES}/2026-07-24/dataset-links.csv. \n"
          f"All data is in a single index: [dataset-links.csv]({FILES}/2026-11-12/dataset-links.csv)\n"
          "older: https://old.example/global-buildings/dataset-links.csv\n")
quiet = lambda m: None  # noqa: E731


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    monkeypatch.setattr(bld, "OSM_PAUSE_S", 0)


def ll(x, y, lat0=LAT0, lon0=LON0):
    m_lon, m_lat = metres_per_degree(lat0)
    return lat0 + y / m_lat, lon0 + x / m_lon


def cluster(cx, cy, nx, ny, step=40.0):
    return [(cx + (i - (nx - 1) / 2) * step, cy + (j - (ny - 1) / 2) * step) for i in range(nx) for j in range(ny)]


def footprint(x, y, side=8.0, multi=False, lat0=LAT0, lon0=LON0) -> str:
    h = side / 2
    ring = [[round(lon, 7), round(lat, 7)] for lat, lon in (ll(x + dx, y + dy, lat0, lon0)
                                                           for dx, dy in ((-h, -h), (h, -h), (h, h), (-h, h), (-h, -h)))]
    geom = {"type": "MultiPolygon", "coordinates": [[ring]]} if multi else {"type": "Polygon", "coordinates": [ring]}
    return json.dumps({"type": "Feature", "properties": {"height": -1.0, "confidence": -1.0}, "geometry": geom})


def tile(points, lat0=LAT0, lon0=LON0, extra=()) -> bytes:
    return gzip.compress("\n".join([footprint(x, y, lat0=lat0, lon0=lon0) for x, y in points] + list(extra)).encode())


def overpass(points, lat0=LAT0, lon0=LON0, extra=()) -> bytes:
    els = [{"type": "way", "id": 1000 + i, "center": dict(zip(("lat", "lon"), ll(x, y, lat0, lon0))), "tags": {"building": "yes"}}
           for i, (x, y) in enumerate(points)]
    return json.dumps({"version": 0.6, "elements": els + list(extra)}).encode()


def overpass_server(points, lat0=LAT0, lon0=LON0):
    """Answers a question the way the real server does: with the buildings inside the box that was asked for."""
    def answer(url, data):
        q = urllib.parse.parse_qs(data.decode())["data"][0]
        s_, w_, n_, e_ = (float(v) for v in re.search(r"\(([-\d.]+),([-\d.]+),([-\d.]+),([-\d.]+)\)", q).groups())
        obj = json.loads(overpass(points, lat0, lon0))
        obj["elements"] = [e for e in obj["elements"] if s_ <= e["center"]["lat"] <= n_ and w_ <= e["center"]["lon"] <= e_]
        return json.dumps(obj).encode()
    return answer


class Resp:
    def __init__(self, body: bytes, status: int = 200, stop_after=None, claims=None):
        self.body, self.status, self.pos, self.stop_after = body, status, 0, stop_after
        self.headers = {"Content-Length": str(len(body) if claims is None else claims)}

    def read(self, n=-1):
        if self.stop_after is not None and self.pos >= self.stop_after:
            raise OSError("connection reset by peer")
        left = len(self.body) - self.pos if self.stop_after is None else self.stop_after - self.pos
        n = left if n is None or n < 0 else min(n, left)
        out = self.body[self.pos:self.pos + n]
        self.pos += len(out)
        return out

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Net:
    """Stands in for the network: pages by address, hosts that are down, and a record of what was asked."""

    def __init__(self):
        self.pages, self.down, self.calls, self.stop_after, self.no_ranges = {}, set(), [], {}, set()

    def __call__(self, url, *, data=None, headers=None, timeout=60.0):
        headers = dict(headers or {})
        self.calls.append((url, headers, data))
        if urllib.parse.urlsplit(url).netloc in self.down:
            raise urllib.error.URLError("no route to host")
        if url not in self.pages:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        body = self.pages[url]
        if isinstance(body, Exception):
            raise body
        if callable(body):
            body = body(url, data)
        start = int(headers["Range"].split("=")[1].rstrip("-")) if "Range" in headers and url not in self.no_ranges else 0
        if start and start >= len(body):
            raise urllib.error.HTTPError(url, 416, "Range Not Satisfiable", {}, None)
        return Resp(body[start:], 206 if start else 200, self.stop_after.pop(url, None))

    def asked(self, part: str) -> int:
        return sum(part in c[0] for c in self.calls)


def site_net(osm_pts, ms_pts, lat0=LAT0, lon0=LON0, half_km=4.0) -> Net:
    net = Net()
    m_lon, m_lat = metres_per_degree(lat0)
    box = (lat0 - half_km * 1000 / m_lat, lon0 - half_km * 1000 / m_lon, lat0 + half_km * 1000 / m_lat, lon0 + half_km * 1000 / m_lon)
    keys = bld.quadkeys(*box)
    rows = ["Location,QuadKey,Url,Size,UploadDate"]
    for i, q in enumerate(keys):
        url = f"{FILES}/2026-11-12/global-buildings.geojsonl/RegionName=Testland/quadkey={q}/part-{i}.csv.gz"
        rows.append(f"Testland,{q},{url},1.2MB,2026-11-12")
        far = footprint(60_000.0, 0.0, lat0=lat0, lon0=lon0)             # in the file, far outside the area
        net.pages[url] = tile(ms_pts if i == 0 else [], lat0, lon0, extra=[far, "not a line of GeoJSON"])
    net.pages[bld.MS_README] = README.encode()
    net.pages[f"{FILES}/2026-11-12/dataset-links.csv"] = "\n".join(rows).encode()
    net.pages[bld.OVERPASS_URLS[0]] = overpass_server(osm_pts, lat0, lon0)
    return net


# buildings both sources know (west), some only OpenStreetMap knows (north-west), some only Microsoft knows (east)
BOTH = cluster(-2500.0, -2500.0, 5, 8)
ONLY_OSM = cluster(-2500.0, 2500.0, 4, 5)
ONLY_MS = cluster(2500.0, 500.0, 5, 5) + cluster(2500.0, -500.0, 5, 5)
OSM_PTS = BOTH + ONLY_OSM
MS_PTS = [(x + 5.0, y - 4.0) for x, y in BOTH] + ONLY_MS


def test_tiles_are_named_the_way_bing_names_them():
    assert bld.quadkey(3, 5, 3) == "213"                                   # the example in Bing's own description
    assert [bld.quadkey(*bld.tile_xy(la, lo, 1), 1) for la, lo in ((5, -5), (5, 5), (-5, -5), (-5, 5))] == ["0", "1", "2", "3"]
    assert bld.quadkeys(-5, -5, 5, 5, 1) == ["0", "1", "2", "3"]
    keys = bld.quadkeys(LAT0 - 0.04, LON0 - 0.04, LAT0 + 0.04, LON0 + 0.04)
    assert len(keys) == 1 and len(keys[0]) == bld.MS_ZOOM and keys[0] == bld.quadkey(*bld.tile_xy(LAT0, LON0))
    assert len(bld.quadkeys(LAT0 - 0.5, LON0 - 0.5, LAT0 + 0.5, LON0 + 0.5)) >= 4          # a wide box spans several


def test_the_newest_list_of_files_is_the_one_the_readme_names(tmp_path):
    assert bld.manifest_url(README) == f"{FILES}/2026-11-12/dataset-links.csv"
    assert bld.manifest_url("only https://old.example/x/dataset-links.csv here") == "https://old.example/x/dataset-links.csv"
    assert bld.manifest_url("nothing here") is None
    f = tmp_path / "links.csv"
    f.write_text("Location,QuadKey,Url,Size,UploadDate\nTestland,23,https://a.example/t1.csv.gz,74.7KB,2026-11-12\n"
                 "Testland,122320113,https://a.example/t2.csv.gz,1.2MB,2026-11-12\n"
                 "Otherland,122320113,https://a.example/t3.csv.gz,3MB,2026-11-12\nbroken,,,,\n", encoding="utf-8")
    m = bld.read_manifest(f)
    assert set(m) == {"000000023", "122320113"}                             # a key that lost its leading zeros is padded
    assert [r["location"] for r in m["122320113"]] == ["Testland", "Otherland"]      # a tile on a border: a file each
    assert bld._megabytes("74.7KB") == pytest.approx(0.0747) and bld._megabytes("1.2MB") == 1.2 and bld._megabytes("") == 0


def test_a_download_that_stops_carries_on_where_it_stopped(tmp_path, monkeypatch):
    monkeypatch.setattr(bld, "CHUNK", 64)
    body = bytes(range(256)) * 4                                            # 1,024 bytes
    net, url, dest = Net(), f"{FILES}/big.csv.gz", tmp_path / "cache" / "big.csv.gz"
    net.pages[url] = body
    monkeypatch.setattr(bld, "_open", net)
    net.stop_after[url] = 300
    with pytest.raises(bld.BuildingsError, match="carry on from there"):
        bld.download(url, dest, log=quiet, label="file 1")
    part = dest.with_name(dest.name + ".part")
    assert not dest.exists() and part.read_bytes() == body[:300]
    lines = []
    assert bld.download(url, dest, log=lines.append, label="file 1") == dest
    assert dest.read_bytes() == body and not part.exists()
    assert net.calls[-1][1]["Range"] == "bytes=300-" and any("carrying on" in ln for ln in lines)
    n = len(net.calls)
    bld.download(url, dest, log=quiet)                                      # there already: nothing is asked for
    assert len(net.calls) == n
    # a server that sends the whole file again: start over, do not append
    again = tmp_path / "again.gz"
    again.with_name("again.gz.part").write_bytes(b"x" * 200)
    net.no_ranges.add(url)
    bld.download(url, again, log=quiet)
    assert again.read_bytes() == body
    # everything had arrived before the last stop: the server has nothing left to send
    whole = tmp_path / "whole.gz"
    whole.with_name("whole.gz.part").write_bytes(body)
    net.no_ranges.discard(url)
    bld.download(url, whole, log=quiet)
    assert whole.read_bytes() == body
    # fewer bytes than announced, without an error from the connection
    monkeypatch.setattr(bld, "_open", lambda u, **k: Resp(body[:500], claims=1024))
    with pytest.raises(bld.BuildingsError, match="short"):
        bld.download(url, tmp_path / "short.gz", log=quiet)
    monkeypatch.setattr(bld, "_open", lambda u, **k: (_ for _ in ()).throw(urllib.error.URLError("offline")))
    with pytest.raises(bld.BuildingsError, match="Check the internet connection"):
        bld.download(url, tmp_path / "none.gz", log=quiet)


def test_footprints_are_read_by_their_centre(tmp_path):
    m_lon, m_lat = metres_per_degree(LAT0)
    box = (LAT0 - 1000 / m_lat, LON0 - 1000 / m_lon, LAT0 + 1000 / m_lat, LON0 + 1000 / m_lon)       # 1 km each way
    lines = [footprint(0, 0, side=10), footprint(990, 0), footprint(1003, 0), footprint(-400, 300, multi=True),
             footprint(5000, 5000), "{broken", json.dumps({"type": "Feature", "geometry": {"type": "Point", "coordinates": [LON0, LAT0]}})]
    f = tmp_path / "t.csv.gz"
    f.write_bytes(gzip.compress("\n".join(lines).encode()))
    got = bld.read_microsoft(f, *box)
    assert len(got) == 3                                   # the one whose centre is 3 m outside is left out, and the far one
    lat, lon, area = got[0]
    assert (lat, lon) == pytest.approx((LAT0, LON0), abs=1e-6) and area == pytest.approx(100.0, abs=0.5)
    assert got[2][2] == pytest.approx(64.0, abs=0.5)        # a footprint stored as a multi-polygon counts the same
    f.write_bytes(gzip.compress("\n".join(lines).encode())[:-20])
    with pytest.raises(bld.BuildingsError, match="could not be read"):
        bld.read_microsoft(f, *box)


def test_openstreetmap_answers_are_read_and_a_query_cut_short_is_refused(monkeypatch):
    extra = [{"type": "relation", "id": 7, "center": {"lat": LAT0, "lon": LON0}, "tags": {"building": "school"}},
             {"type": "node", "id": 8, "lat": LAT0 + 0.001, "lon": LON0, "tags": {"building": "hut"}},
             {"type": "way", "id": 9, "center": {"lat": LAT0, "lon": LON0}, "tags": {"building": "no"}},
             {"type": "way", "id": 10, "tags": {"building": "yes"}}]
    got = bld.parse_overpass(json.loads(overpass([(100.0, 200.0)], extra=extra)))
    assert [g[2] for g in got] == ["w1000", "r7", "n8"]                     # "no" and a way without a centre are left out
    assert got[0][:2] == pytest.approx(ll(100.0, 200.0))
    q = bld.overpass_query(9.9, 19.9, 10.1, 20.1)
    assert 'way["building"](9.900000,19.900000,10.100000,20.100000)' in q and "out tags center" in q and 'node["building"]' in q
    net = Net()
    monkeypatch.setattr(bld, "_open", net)
    first, second, third = bld.OVERPASS_URLS
    net.pages[first] = urllib.error.HTTPError(first, 504, "Gateway Timeout", {}, None)
    net.pages[second] = json.dumps({"elements": [], "remark": "runtime error: Query timed out"}).encode()
    net.pages[third] = overpass([(0.0, 0.0), (50.0, 50.0)])
    lines = []
    rows, server = bld.fetch_osm(9.99, 19.99, 10.01, 20.01, log=lines.append)          # 2 km: one question
    assert len(rows) == 2 and server == urllib.parse.urlsplit(third).netloc
    assert sum("no usable answer" in ln for ln in lines) == 2 and b"building" in urllib.parse.unquote_to_bytes(net.calls[0][2])
    net.pages[third] = b"<html>busy</html>"
    with pytest.raises(bld.BuildingsError, match="OpenStreetMap could not be reached"):
        bld.fetch_osm(9.99, 19.99, 10.01, 20.01, log=quiet)


def test_openstreetmap_is_asked_in_parts_and_what_arrived_is_kept(tmp_path, monkeypatch):
    m_lon, m_lat = metres_per_degree(LAT0)
    box = (LAT0 - 4000 / m_lat, LON0 - 4000 / m_lon, LAT0 + 4000 / m_lat, LON0 + 4000 / m_lon)       # 8 km: four parts
    parts = bld.osm_parts(*box)
    assert [k for k, _ in parts] == ["0,0", "0,1", "1,0", "1,1"] and parts[0][1][:2] == box[:2] and parts[-1][1][2:] == pytest.approx(box[2:])
    assert len(bld.osm_parts(LAT0 - 0.01, LON0 - 0.01, LAT0 + 0.01, LON0 + 0.01)) == 1
    pts = [(-2000.0, -2000.0), (2000.0, -2000.0), (-2000.0, 2000.0), (2000.0, 2000.0), (0.0, 0.0)]    # the last on the corner of all four
    first, second, third = bld.OVERPASS_URLS
    net = Net()
    monkeypatch.setattr(bld, "_open", net)
    net.pages[first] = urllib.error.HTTPError(first, 502, "Bad Gateway", {}, None)
    net.pages[second] = overpass_server(pts)
    copy = tmp_path / "constraints" / bld.OSM_COPY
    rows, server = bld.fetch_osm(*box, log=quiet, copy=copy, today=TODAY)
    assert sorted(r[2] for r in rows) == ["w1000", "w1001", "w1002", "w1003", "w1004"]       # the corner building once, not four times
    assert server == urllib.parse.urlsplit(second).netloc
    assert net.asked(urllib.parse.urlsplit(first).netloc) == 1 and net.asked(urllib.parse.urlsplit(second).netloc) == 4   # the server that failed is not asked again
    # a day on which the servers give up half way: what arrived is kept, and the next go asks only for the rest
    answers = overpass_server(pts)
    count = {"n": 0}

    def two_then_nothing(url, data):
        count["n"] += 1
        if count["n"] > 2:
            raise urllib.error.URLError("timed out")
        return answers(url, data)

    net2 = Net()
    monkeypatch.setattr(bld, "_open", net2)
    net2.pages[first] = two_then_nothing
    tomorrow = TODAY + timedelta(days=1)
    lines = []
    with pytest.raises(bld.BuildingsError, match="2 of 4 parts arrived and are kept for today"):
        bld.fetch_osm(*box, log=lines.append, copy=copy, today=tomorrow)
    assert len(json.loads(copy.read_text("utf-8"))["parts"]) == 2
    net2.pages[first] = answers
    n = len(net2.calls)
    lines = []
    rows, _ = bld.fetch_osm(*box, log=lines.append, copy=copy, today=tomorrow)
    assert len(net2.calls) - n == 2 and len(rows) == 5 and any("2 of 4 parts arrived earlier today" in ln for ln in lines)


def test_the_two_sources_are_merged_and_their_gaps_are_named(tmp_path, monkeypatch):
    net = site_net(OSM_PTS, MS_PTS)
    monkeypatch.setattr(bld, "_open", net)
    lines = []
    out = bld.fetch(LAT0, LON0, 4.0, tmp_path / "constraints", cache_dir=tmp_path / "cache", log=lines.append, today=TODAY)
    assert (out["n_osm"], out["n_microsoft"], out["n_added_from_microsoft"], out["n"], out["n_osm_only"]) == (60, 90, 50, 110, 20)
    assert out["sources"]["osm"] == {"ok": True, "n": 60, "server": "overpass-api.de"}
    ms = out["sources"]["microsoft"]
    assert ms["ok"] and ms["release"] == "2026-11-12" and ms["tiles"] == 1 and ms["list"].endswith("/2026-11-12/dataset-links.csv")
    rows = list(csv.DictReader(open(tmp_path / "constraints" / "buildings.csv", encoding="utf-8")))
    assert len(rows) == 110 and [r["source"] for r in rows].count("microsoft") == 50
    assert rows[0]["name"] == "OSM w1000" and rows[60]["name"] == "MS 000001" and float(rows[60]["area_m2"]) == pytest.approx(64, abs=1)
    added = {(round(float(r["latitude"]), 5), round(float(r["longitude"]), 5)) for r in rows if r["source"] == "microsoft"}
    assert added == {tuple(round(v, 5) for v in ll(x, y)) for x, y in ONLY_MS}        # the footprints OpenStreetMap lacks
    assert bld.load(tmp_path / "constraints" / "buildings.csv")[0] == {"name": "OSM w1000", "lat": pytest.approx(ll(*BOTH[0])[0]),
                                                                    "lon": pytest.approx(ll(*BOTH[0])[1]), "source": "osm"}
    cmp = out["compared"]
    assert cmp["cell_km"] == 1.0 and cmp["osm"] == {"km2": 2.0, "buildings": 50, "where": "mostly east of the site", "share_of_area_pct": 3.0}
    assert cmp["microsoft"]["buildings"] == 20 and cmp["microsoft"]["where"] == "mostly north-west of the site"
    assert out["warnings"][0] == ("OpenStreetMap is incomplete here. Over about 2 km², mostly east of the site, Microsoft's "
                                  "footprints show 50 buildings that OpenStreetMap does not have.")
    assert out["warnings"][1].startswith("Microsoft's footprints are incomplete here.") and "OpenStreetMap has 20 buildings" in out["warnings"][1]
    assert len(out["attribution"]) == 2 and "Open Database License" in out["attribution"][0] and "Permissive 2.0" in out["attribution"][1]
    assert any("110 buildings in all" in ln for ln in lines) and any(ln.strip().startswith("Caution:") for ln in lines)
    json.dumps(out)                                                         # it goes into the project file as it is
    # a second time the same day: nothing is asked for again, so a run that failed half way costs little to repeat
    n = len(net.calls)
    lines = []
    again = bld.fetch(LAT0, LON0, 4.0, tmp_path / "constraints", cache_dir=tmp_path / "cache", log=lines.append, today=TODAY)
    assert len(net.calls) == n and again["n"] == 110 and again["sources"]["osm"] == out["sources"]["osm"]
    assert any("fetched earlier today is used again" in ln for ln in lines)
    # the next day, or for another area, OpenStreetMap is asked again; the footprints still come from the cache
    bld.fetch(LAT0, LON0, 4.0, tmp_path / "constraints", cache_dir=tmp_path / "cache", log=quiet, today=TODAY + timedelta(days=1))
    bld.fetch(LAT0, LON0, 3.0, tmp_path / "constraints", cache_dir=tmp_path / "cache", log=quiet, today=TODAY + timedelta(days=1))
    assert net.asked("overpass-api.de") == 4 + 4 + 1 and net.asked("quadkey=") == 1 and net.asked("dataset-links") == 1     # 8 km in four parts, 6 km in one


def test_one_source_down_is_said_and_both_down_stops(tmp_path, monkeypatch):
    net = site_net(OSM_PTS, MS_PTS)
    monkeypatch.setattr(bld, "_open", net)
    net.down |= {urllib.parse.urlsplit(u).netloc for u in bld.OVERPASS_URLS}
    out = bld.fetch(LAT0, LON0, 4.0, tmp_path / "a", cache_dir=tmp_path / "cache", log=quiet, today=TODAY)
    assert not out["sources"]["osm"]["ok"] and out["n"] == out["n_added_from_microsoft"] == 90 and out["compared"] == {}
    assert out["warnings"] == ["OpenStreetMap could not be reached, so the buildings come from Microsoft's footprints alone and "
                               "nothing checks them. Get the buildings again later."]
    assert len(out["attribution"]) == 1 and "Microsoft" in out["attribution"][0]
    # the other way round, with nothing in the cache
    net2 = site_net(OSM_PTS, MS_PTS)
    monkeypatch.setattr(bld, "_open", net2)
    net2.down |= {"files.example", "raw.githubusercontent.com"} | {urllib.parse.urlsplit(u).netloc for u in bld.MS_MANIFESTS}
    out = bld.fetch(LAT0, LON0, 4.0, tmp_path / "b", cache_dir=tmp_path / "cache2", log=quiet, today=TODAY)
    assert out["sources"]["microsoft"]["ok"] is False and out["n"] == out["n_osm"] == 60
    assert out["warnings"][0].startswith("Microsoft's footprints could not be reached, so the buildings come from OpenStreetMap alone")
    net2.down |= {urllib.parse.urlsplit(u).netloc for u in bld.OVERPASS_URLS}
    with pytest.raises(bld.BuildingsError, match="Neither OpenStreetMap nor Microsoft's building files could be reached"):
        bld.fetch(LAT0, LON0, 4.0, tmp_path / "c", cache_dir=tmp_path / "cache2", log=quiet, today=TODAY)
    # an area for which Microsoft holds no file at all
    net3 = site_net(OSM_PTS, MS_PTS)
    net3.pages[f"{FILES}/2026-11-12/dataset-links.csv"] = b"Location,QuadKey,Url,Size,UploadDate\nElsewhere,000000001,https://files.example/e.csv.gz,1KB,2026-11-12\n"
    monkeypatch.setattr(bld, "_open", net3)
    out = bld.fetch(LAT0, LON0, 4.0, tmp_path / "d", cache_dir=tmp_path / "cache3", log=quiet, today=TODAY)
    assert out["sources"]["microsoft"]["tiles"] == 0 and out["n"] == 60 and "holds no file for this area" in out["warnings"][-1]


def test_the_list_of_files_is_kept_for_a_month_and_survives_a_failed_refresh(tmp_path, monkeypatch):
    net = site_net([], [])
    monkeypatch.setattr(bld, "_open", net)
    files, meta = bld.manifest(tmp_path, log=quiet, today=TODAY)
    assert meta == {"url": f"{FILES}/2026-11-12/dataset-links.csv", "fetched": "2026-11-19"} and len(files) >= 1
    n = len(net.calls)
    bld.manifest(tmp_path, log=quiet, today=TODAY + timedelta(days=29))
    assert len(net.calls) == n                                               # still fresh
    bld.manifest(tmp_path, log=quiet, today=TODAY + timedelta(days=31))
    assert net.asked("README") == 2 and net.asked("dataset-links") == 2          # fetched again
    net.down |= {"files.example", "raw.githubusercontent.com"} | {urllib.parse.urlsplit(u).netloc for u in bld.MS_MANIFESTS}
    lines = []
    files2, meta2 = bld.manifest(tmp_path, log=lines.append, today=TODAY + timedelta(days=90))
    assert files2 == files and any("could not be refreshed" in ln for ln in lines)      # the old copy serves
    with pytest.raises(bld.BuildingsError, match="could not be downloaded"):
        bld.manifest(tmp_path / "empty", log=quiet, today=TODAY)
    # the README cannot be read: the addresses the application knows are tried
    net2 = Net()
    monkeypatch.setattr(bld, "_open", net2)
    net2.pages[bld.MS_MANIFESTS[1]] = b"Location,QuadKey,Url,Size,UploadDate\nTestland,122320113,https://a.example/t.csv.gz,1KB,2026-02-23\n"
    _, meta3 = bld.manifest(tmp_path / "second", log=quiet, today=TODAY)
    assert meta3["url"] == bld.MS_MANIFESTS[1]


def test_your_own_file_goes_on_top(tmp_path):
    to_xy = lambda lat, lon: ((np.asarray(lon, dtype=float) - LON0) * metres_per_degree(LAT0)[0],  # noqa: E731
                              (np.asarray(lat, dtype=float) - LAT0) * metres_per_degree(LAT0)[1])
    fetched = [{"name": f"OSM w{i}", "lat": ll(x, y)[0], "lon": ll(x, y)[1], "source": "osm"}
               for i, (x, y) in enumerate(cluster(0.0, 0.0, 5, 5, step=100.0))]                    # 25 buildings, 100 m apart
    F = constraints.Feature
    own = [F("point", [ll(10.0, 0.0)], "Mama Achieng"),             # 10 m from a fetched building: stands for it
           F("point", [ll(1000.0, 1000.0)], ""),                    # a home nobody had mapped
           F("point", [ll(-200.0, -190.0)], "Not a home: cattle shed"),          # takes out the building 10 m away
           F("polygon", [[ll(50.0, 50.0), ll(250.0, 50.0), ll(250.0, 250.0), ll(50.0, 250.0)]], "not a home"),   # four inside
           F("polygon", [[ll(-250.0, 150.0), ll(-150.0, 150.0), ll(-150.0, 250.0)]], "Grazing land"),   # an outline with another name: ignored
           F("line", [ll(0.0, 0.0), ll(5.0, 5.0)], "remove")]
    rows, n = bld.combine(fetched, own, to_xy)
    assert n == {"yours": 2, "fetched": 19, "taken_out": 5, "replaced": 1, "marks": 2}
    assert [r["name"] for r in rows[:2]] == ["Mama Achieng", "R002"] and all(r["source"] == "yours" for r in rows[:2])
    assert len(rows) == 21 and {"x", "y", "lat", "lon", "name", "source"} <= set(rows[5])
    gone = {"OSM w0", "OSM w12"} | {f"OSM w{i}" for i in (18, 19, 23, 24)}      # the shed, the one that is hers, the four in the outline
    assert {f"OSM w{i}" for i in range(25)} - {r["name"] for r in rows} == gone
    assert rows[2]["name"] == "OSM w1" and (rows[2]["x"], rows[2]["y"]) == pytest.approx((-200.0, -100.0), abs=0.01)
    # without fetched buildings the homes are the user's points, as they always were
    homes = [f for f in own if f.kind == "point"]
    rows, n = bld.combine([], homes[:2], to_xy)
    assert n == {"yours": 2, "fetched": 0, "taken_out": 0, "replaced": 0, "marks": 0}
    assert [(r["name"], r["x"], r["y"]) for r in rows] == [(r["name"], r["x"], r["y"]) for r in
                                                         constraints.points_of(homes[:2], type("G", (), {"to_xy": staticmethod(to_xy)})())]
    rows, n = bld.combine([], [], to_xy)
    assert rows == [] and n["yours"] == 0


def test_the_project_fetches_the_buildings_and_every_later_step_uses_them(tmp_path, monkeypatch):
    pytest.importorskip("py_wake")
    from bohemian_wind import demo
    from bohemian_wind.project import Project, ProjectError
    p = demo.create(tmp_path / "Demo Ridge", today=date(2026, 11, 18))
    for step in ("data", "longterm", "map", "layout"):
        p.run(step, log=quiet)
    site = p.site()
    assert p.status("layout") == "done" and p.state()["buildings"] is None
    before = p.result("layout")["receptors_in_area"]
    own_points = sum(f.kind == "point" for f in p.features("receptors"))
    assert p.result("layout")["receptor_origin"] == {"yours": own_points, "fetched": 0, "taken_out": 0, "replaced": 0, "marks": 0,
                                                     "on_the_terrain": before}
    west, east = cluster(-3000.0, -3000.0, 6, 5), cluster(3000.0, 2500.0, 5, 5)
    half = p.s["layout"]["rules"]["radius_km"] + bld.MARGIN_KM
    net = site_net(west, [(x + 5.0, y) for x, y in west] + east, site["lat"], site["lon"], half_km=half)
    monkeypatch.setattr(bld, "_open", net)
    monkeypatch.setenv("BOHEMIAN_WIND_CACHE", str(tmp_path / "cache"))
    lines = []
    out = p.run("buildings", log=lines.append)
    assert (out["n_osm"], out["n_added_from_microsoft"], out["n"]) == (30, 25, 55) and out["half_km"] == half
    assert out["file"] == "constraints/buildings.csv" and (p.folder / out["file"]).is_file() and (p.folder / out["map"]).is_file()
    assert (tmp_path / "cache" / "buildings" / "microsoft" / "dataset-links.csv").is_file()
    p = Project.open(p.folder)                                               # it is all in the project file
    assert p.s["layout"]["buildings_file"] == "constraints/buildings.csv" and p.state()["buildings"]["n"] == 55
    assert p.status("layout") == "stale"                                     # the layout was made without them
    lines = []
    lay = p.run("layout", log=lines.append)
    o = lay["receptor_origin"]
    assert o["yours"] == own_points and o["fetched"] + o["replaced"] == 55 and o["taken_out"] == 0
    assert lay["receptors_in_area"] == o["on_the_terrain"] > before and p.status("layout") == "done"
    assert any(ln.startswith("Homes: ") and "buildings from open data" in ln for ln in lines)
    assert any("homes and other receptors" in s["rule"] and f"({lay['receptors_in_area']})" in s["rule"] for s in lay["constraints"]["steps"])
    # a file of the user's own on top: an outline named "not a home" round the eastern group takes it out
    box = [ll(x, y, site["lat"], site["lon"]) for x, y in ((2800, 2300), (3200, 2300), (3200, 2700), (2800, 2700))]
    mine = tmp_path / "mine.geojson"
    mine.write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"name": "not a home"}, "geometry": {"type": "Polygon", "coordinates": [[[lon, lat] for lat, lon in box + box[:1]]]}},
        {"type": "Feature", "properties": {"name": "Caretaker's house"}, "geometry": {"type": "Point", "coordinates": list(ll(1500.0, -2500.0, site["lat"], site["lon"]))[::-1]}}]}),
        encoding="utf-8")
    p.attach("receptors", mine)
    lay = p.run("layout", log=quiet)
    assert lay["receptor_origin"] == {"yours": 1, "fetched": 30, "taken_out": 25, "replaced": 0, "marks": 1, "on_the_terrain": 31}
    env = p.run("environment", log=quiet)
    assert env["noise"]["n_receptors"] == 31
    pack = p.run("pack", log=quiet)
    page = (p.folder / pack["folder"] / "index.html").read_text("utf-8")
    assert "Where the homes come from: 30 buildings from open data (OpenStreetMap and Microsoft" in page
    assert "the developer's file took 25 buildings out" in page and "Every building is counted as a home" in page
    assert "OpenStreetMap contributors" in page and "Permissive 2.0" in page and "keep this notice with them" in page
    assert "Where the building sources disagree" in page                     # the eastern group is in Microsoft's data only
    # an outline alone is a homes file too, once there are buildings to take out
    only = tmp_path / "only.geojson"
    only.write_text(json.dumps({"type": "Feature", "properties": {"name": "Not a home"}, "geometry":
                                {"type": "Polygon", "coordinates": [[[lon, lat] for lat, lon in box + box[:1]]]}}), encoding="utf-8")
    p.attach("receptors", only)
    # removing the buildings removes their summary and marks the layout out of date again
    p.update("layout", {"buildings_file": ""})
    assert p.state()["buildings"] is None and p.status("layout") == "stale"
    p.s["layout"]["buildings_file"] = "constraints/gone.csv"
    with pytest.raises(ProjectError, match="buildings file is missing"):
        p.receptors(p.grid())
    # with the connection gone the same day, the OpenStreetMap answer already in the project still serves
    net.down |= {urllib.parse.urlsplit(u).netloc for u in bld.OVERPASS_URLS} | {"files.example"}
    monkeypatch.setenv("BOHEMIAN_WIND_CACHE", str(tmp_path / "cache-empty"))
    net.down |= {"raw.githubusercontent.com"} | {urllib.parse.urlsplit(u).netloc for u in bld.MS_MANIFESTS}
    out = p.run("buildings", log=quiet)
    assert out["sources"]["osm"]["ok"] and not out["sources"]["microsoft"]["ok"] and out["n"] == 30
    (p.folder / "constraints" / bld.OSM_COPY).unlink()
    with pytest.raises(ProjectError, match="Check the internet connection"):
        p.run("buildings", log=quiet)


def test_the_interface_runs_the_buildings_like_a_step():
    from bohemian_wind.project import STEPS, Project
    assert "buildings" in Project.RUNNERS and "buildings" not in STEPS       # run like a step, but not one of the six
    root = Path(__file__).resolve().parents[1] / "bohemian_wind" / "app"
    js = (root / "static" / "app.js").read_text("utf-8")
    assert 'data-run="buildings"' in js and "STEP.buildings" in js and 'data-kind="buildings"' in js
    assert "in Project.RUNNERS" in (root / "server.py").read_text("utf-8")
