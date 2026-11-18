"""Position of the sun (for shadow flicker). The algorithm is the one behind NOAA's solar calculator, after
Meeus, "Astronomical Algorithms": good to a few hundredths of a degree for the years 1901-2099, far finer than a
rotor's apparent size."""
from __future__ import annotations

import numpy as np


def position(times_utc, lat: float, lon: float, refraction: bool = True) -> tuple:
    """times_utc: numpy datetime64 array (UTC). Returns (elevation, azimuth) in degrees; azimuth clockwise from
    north. With refraction, the elevation is the apparent one (what an observer sees)."""
    t = np.asarray(times_utc, dtype="datetime64[s]")
    unix = t.astype("int64").astype(float)
    jd = unix / 86400.0 + 2440587.5
    T = (jd - 2451545.0) / 36525.0
    rad, deg = np.deg2rad, np.rad2deg
    L0 = (280.46646 + T * (36000.76983 + 0.0003032 * T)) % 360.0
    M = 357.52911 + T * (35999.05029 - 0.0001537 * T)
    e = 0.016708634 - T * (0.000042037 + 0.0000001267 * T)
    C = (np.sin(rad(M)) * (1.914602 - T * (0.004817 + 0.000014 * T)) + np.sin(rad(2 * M)) * (0.019993 - 0.000101 * T)
         + np.sin(rad(3 * M)) * 0.000289)
    omega = 125.04 - 1934.136 * T
    lam = L0 + C - 0.00569 - 0.00478 * np.sin(rad(omega))
    eps0 = 23.0 + (26.0 + (21.448 - T * (46.815 + T * (0.00059 - T * 0.001813))) / 60.0) / 60.0
    eps = eps0 + 0.00256 * np.cos(rad(omega))
    decl = np.arcsin(np.sin(rad(eps)) * np.sin(rad(lam)))
    y = np.tan(rad(eps / 2.0)) ** 2
    eot = 4.0 * deg(y * np.sin(2 * rad(L0)) - 2 * e * np.sin(rad(M)) + 4 * e * y * np.sin(rad(M)) * np.cos(2 * rad(L0))
                    - 0.5 * y * y * np.sin(4 * rad(L0)) - 1.25 * e * e * np.sin(2 * rad(M)))
    minutes = (unix % 86400.0) / 60.0
    tst = (minutes + eot + 4.0 * lon) % 1440.0
    ha = rad(tst / 4.0 - 180.0)
    la = rad(lat)
    cos_zen = np.clip(np.sin(la) * np.sin(decl) + np.cos(la) * np.cos(decl) * np.cos(ha), -1.0, 1.0)
    zen = np.arccos(cos_zen)
    elev = 90.0 - deg(zen)
    # azimuth from the east and north components of the sun's direction (well-behaved at the zenith and the poles)
    east = -np.cos(decl) * np.sin(ha)
    north = np.cos(la) * np.sin(decl) - np.sin(la) * np.cos(decl) * np.cos(ha)
    az = deg(np.arctan2(east, north)) % 360.0
    if refraction:
        elev = elev + _refraction(elev)
    return elev, az


def _refraction(elev):
    """Atmospheric refraction in degrees (NOAA's approximation)."""
    e = np.asarray(elev, dtype=float)
    te = np.tan(np.deg2rad(np.clip(e, -0.575, 89.999)))
    hi = 58.1 / te - 0.07 / te ** 3 + 0.000086 / te ** 5
    mid = 1735.0 + e * (-518.2 + e * (103.4 + e * (-12.79 + e * 0.711)))
    low = -20.772 / np.tan(np.deg2rad(np.minimum(e, -0.575)))
    r = np.where(e > 85.0, 0.0, np.where(e > 5.0, hi, np.where(e > -0.575, mid, low)))
    return r / 3600.0


def unit_vector(elev, az) -> np.ndarray:
    """[n, 3] unit vectors pointing at the sun: east, north, up."""
    e, a = np.deg2rad(elev), np.deg2rad(az)
    return np.column_stack([np.cos(e) * np.sin(a), np.cos(e) * np.cos(a), np.sin(e)])
