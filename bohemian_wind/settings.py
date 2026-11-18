"""Optional site settings: corrections to the site properties written in the logger files.

A small JSON file, for example:

    {
      "site_name": "Example Hill (Example County)",
      "elevation_m": 213,
      "source": "SRTM 1 arc-second terrain at the logged coordinates"
    }

Keys: site_name, elevation_m, latitude, longitude, source, notes. The run looks for
'<output folder>/site settings/<data folder name>.json' unless a file is given. Every
value used in place of a logged one is printed in the report beside the logged value.
"""
from __future__ import annotations

import json
from pathlib import Path

from .sympro import Dataset, distance_m

FOLDER = "site settings"
KEYS = {"site_name": str, "elevation_m": (int, float), "latitude": (int, float), "longitude": (int, float),
        "source": str, "notes": str}
LIMITS = {"elevation_m": (-500.0, 6000.0), "latitude": (-90.0, 90.0), "longitude": (-180.0, 180.0)}


class SettingsError(ValueError):
    """The settings file cannot be used; the message says why."""


def load(path) -> dict:
    p = Path(path)
    try:
        raw = json.loads(p.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise SettingsError(f"Site settings file not found: {p}") from None
    except json.JSONDecodeError as e:
        raise SettingsError(f"Site settings file {p.name} is not valid JSON (line {e.lineno}: {e.msg}).") from None
    if not isinstance(raw, dict):
        raise SettingsError(f"Site settings file {p.name} must hold one JSON object {{...}}.")
    unknown = sorted(set(raw) - set(KEYS))
    if unknown:
        raise SettingsError(f"Site settings file {p.name}: unknown setting {', '.join(unknown)}. "
                            f"Allowed: {', '.join(KEYS)}.")
    for k, v in raw.items():
        if isinstance(v, bool) or not isinstance(v, KEYS[k]):
            kind = "text" if KEYS[k] is str else "a number"
            raise SettingsError(f"Site settings file {p.name}: {k} must be {kind}.")
    for k, (lo, hi) in LIMITS.items():
        if k in raw and not lo <= float(raw[k]) <= hi:
            raise SettingsError(f"Site settings file {p.name}: {k} {raw[k]} is outside {lo:g} to {hi:g}.")
    if ("latitude" in raw) != ("longitude" in raw):
        raise SettingsError(f"Site settings file {p.name}: give latitude and longitude together.")
    return raw


def _num(v):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def apply(ds: Dataset, settings: dict, path) -> list[str]:
    """Use the settings in place of the logged site properties. Returns one note per change."""
    logged = ds.site_logged or dict(ds.site)
    notes = []
    if "elevation_m" in settings:
        new = f"{float(settings['elevation_m']):g}"
        old = str(logged.get("Elevation", "")).strip()
        ds.site["Elevation"] = new
        notes.append(f"Elevation {new} m in place of {old + ' m' if old else 'none'} in the logger files")
    if "latitude" in settings:
        lat, lon = float(settings["latitude"]), float(settings["longitude"])
        old_lat, old_lon = _num(logged.get("Latitude")), _num(logged.get("Longitude"))
        ds.site["Latitude"], ds.site["Longitude"] = f"{lat:.7f}", f"{lon:.7f}"
        if old_lat is not None and old_lon is not None:
            d = distance_m((lat, lon), (old_lat, old_lon))
            notes.append(f"Position {lat:.6f}, {lon:.6f} in place of {old_lat:.6f}, {old_lon:.6f} in the logger "
                         f"files ({d:,.0f} m apart)")
        else:
            notes.append(f"Position {lat:.6f}, {lon:.6f}; the logger files give none")
    if "site_name" in settings:
        notes.append(f"Report title: {settings['site_name']}")
    ds.settings = {"path": str(path), "values": dict(settings), "changes": notes}
    return notes
