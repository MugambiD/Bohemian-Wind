"""Bohemian linear flow model, v0.1 (prototype): terrain speed-up over low hills.

What it does
    Given a terrain grid, a wind direction, a surface roughness and a height above ground, it returns the fractional
    speed-up dS at every grid point: wind speed there = upstream wind speed at that height x (1 + dS).
    A wind map follows by running one direction per sector and scaling the mast's wind in that sector by
    (1 + dS at the point) / (1 + dS at the mast).

Method (all from published theory; nothing here is taken from WAsP or any other program's code)
    1. The terrain is split into waves with a 2-D Fourier transform. For each wave (wavenumber kappa, length scale
       L = 1/kappa) the flow far above the ground is the linear potential-flow answer:
           du/U = (ks^2 / kappa) * h_hat * exp(-kappa z)        ks = wavenumber along the wind
    2. Near the ground the upstream wind is a log profile, u0(z) = (u*/0.4) ln(z/z0), and the hill's pressure field
       is set by the wind at the hill's own scale, u0(L). Following Jackson & Hunt (1975) and Hunt, Leibovich &
       Richards (1988):
           above L             du = sigma * u0(L)                      (potential flow)
           between l and L     du = sigma * u0(L)^2 / u0(z)            (middle layer)
           below l             du = sigma * u0(L)^2 / u0(l)            (inner layer)
       with the inner-layer depth l from   l ln(l/z0) = 2 * 0.4^2 * L   (Jackson & Hunt), or Jensen's variant.
       dS = du / u0(z). Below l, dS is held at its value at l, [u0(L)/u0(l)]^2 * sigma: the Jackson-Hunt
       estimate of the largest speed-up. (A first build let dS keep growing down to l/3; it over-shot the
       published ridge guideline of 2 h/L by a third, so the simpler, more cautious form is used. The detail of
       the lowest few metres is therefore not resolved; hub heights sit in the middle layer and are unaffected.)
    3. The waves are added back together.

What it does not do yet (so do not use it beyond these limits)
    - Roughness changes (forest edge, lake shore), obstacles, thermal stability, flow separation.
    - Steep terrain: linear theory over-predicts where slopes pass about 0.3 (17 degrees). steep_share() reports it.
    - It is a screening model. [COMPLIANCE] Not an accredited or bankable flow model.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

KARMAN = 0.4
__version__ = "0.1.0-dev"


def inner_layer_depth(L, z0: float, variant: str = "jackson-hunt"):
    """Inner-layer depth l for horizontal scale L (same units).  jackson-hunt: l ln(l/z0) = 2 k^2 L;
    jensen: l ln^2(l/z0) = 2 k^2 L (Jensen, Petersen & Troen 1984). Solved by Newton iteration."""
    L = np.asarray(L, dtype=float)
    rhs = 2.0 * KARMAN ** 2 * L
    p = 1 if variant == "jackson-hunt" else 2
    if variant not in ("jackson-hunt", "jensen"):
        raise ValueError("variant must be 'jackson-hunt' or 'jensen'")
    l = np.maximum(rhs / 5.0 ** p, 3.0 * z0)
    for _ in range(60):
        ln = np.log(np.maximum(l, 1.0001 * z0) / z0)
        f = l * ln ** p - rhs
        df = ln ** p + p * ln ** (p - 1)
        l = np.maximum(l - f / df, 1.0001 * z0)
    return l


def _amplification(kappa, z: float, z0: float, variant: str):
    """u0(L)^2 / (u0(zc) * u0(zs)) per wavenumber: the factor that turns the potential-flow perturbation, relative
    to a uniform stream, into a speed-up relative to the log profile at height z."""
    with np.errstate(divide="ignore"):
        L = np.where(kappa > 0, 1.0 / np.maximum(kappa, 1e-30), np.inf)
    amp = np.ones_like(L)
    ok = np.isfinite(L) & (L > 30.0 * z0)              # scales too small to hold a log layer: no amplification
    Lo = L[ok]
    l = np.minimum(inner_layer_depth(Lo, z0, variant), Lo)
    zs = np.maximum(z, l)                               # dS held constant below the inner-layer depth
    zc = np.clip(zs, l, Lo)                             # inner layer: u0(l); middle: u0(z); above L: u0(L)
    ln = lambda a: np.log(np.maximum(a, 1.0001 * z0) / z0)  # noqa: E731
    amp[ok] = ln(Lo) ** 2 / (ln(zc) * ln(zs))
    return amp


def _taper(h: np.ndarray, share: float) -> np.ndarray:
    """Removes the mean and fades the edges to zero, so the Fourier transform's wrap-around does not put a cliff
    between opposite edges of the map."""
    h = h - np.mean(h)
    if share <= 0:
        return h
    def win(n):
        m = max(int(n * share), 1)
        w = np.ones(n)
        ramp = 0.5 * (1 - np.cos(np.pi * (np.arange(m) + 0.5) / m))
        w[:m], w[-m:] = ramp, ramp[::-1]
        return w
    return h * np.outer(win(h.shape[0]), win(h.shape[1]))


@dataclass
class FlowResult:
    speedup: np.ndarray          # dS: fractional speed-up of wind speed
    turning_deg: np.ndarray      # change of wind direction, degrees, positive = veers clockwise
    direction_deg: float
    height_m: float
    z0_m: float


def speedup(terrain, dx: float, direction_deg: float, height_m: float, z0_m: float = 0.03, *, dy: float | None = None,
            taper: float = 0.1, pad: int = 0, variant: str = "jackson-hunt", inner_layer: bool = True) -> FlowResult:
    """terrain[row, col]: elevation in metres, rows running south to north, columns west to east, spacing dy, dx.
    direction_deg: where the wind comes FROM (meteorological). height_m: above ground. pad: flat cells added all
    round (use for small maps so the hill does not see its own periodic copies)."""
    h = np.asarray(terrain, dtype=float)
    if h.ndim != 2 or min(h.shape) < 8:
        raise ValueError("terrain must be a 2-D grid of at least 8 x 8 cells")
    if not (height_m > z0_m > 0):
        raise ValueError("need height above ground > roughness length > 0")
    dy = dx if dy is None else dy
    work = _taper(h, taper)
    if pad:
        work = np.pad(work, pad)
    ny, nx = work.shape
    kx = 2 * np.pi * np.fft.fftfreq(nx, dx)[None, :]
    ky = 2 * np.pi * np.fft.fftfreq(ny, dy)[:, None]
    kappa = np.hypot(kx, ky)
    th = np.deg2rad(direction_deg)
    ex, ey = -np.sin(th), -np.cos(th)                   # unit vector the wind blows TOWARDS (x east, y north)
    ks = kx * ex + ky * ey                              # wavenumber along the wind
    kn = -kx * ey + ky * ex                             # across the wind, 90 deg anticlockwise from it
    with np.errstate(divide="ignore", invalid="ignore"):
        base = np.where(kappa > 0, np.exp(-kappa * height_m) / kappa, 0.0)
    amp = _amplification(kappa, height_m, z0_m, variant) if inner_layer else 1.0
    hh = np.fft.fft2(work)
    du = np.real(np.fft.ifft2(ks * ks * base * amp * hh))
    dv = np.real(np.fft.ifft2(ks * kn * base * amp * hh))   # across-wind perturbation, anticlockwise positive
    if pad:
        du, dv = du[pad:-pad, pad:-pad], dv[pad:-pad, pad:-pad]
    turn = -np.rad2deg(np.arctan2(dv, 1.0 + du))        # anticlockwise flow turn = wind backs; report veer positive
    return FlowResult(speedup=np.hypot(1.0 + du, dv) - 1.0, turning_deg=turn, direction_deg=float(direction_deg),
                      height_m=float(height_m), z0_m=float(z0_m))


def steep_share(terrain, dx: float, dy: float | None = None, limit: float = 0.3) -> float:
    """Share of the map steeper than `limit` (0.3 = 17 degrees). Above a few per cent, treat results with care."""
    gy, gx = np.gradient(np.asarray(terrain, dtype=float), dx if dy is None else dy, dx)
    return float((np.hypot(gx, gy) > limit).mean())


def transfer(result: FlowResult, mast_rc: tuple, site_rc) -> np.ndarray:
    """Ratio of wind speed at each site to wind speed at the mast, same height, for this wind direction.
    mast_rc: (row, col) of the mast; site_rc: array of (row, col)."""
    s = 1.0 + result.speedup
    site_rc = np.atleast_2d(site_rc)
    return s[site_rc[:, 0], site_rc[:, 1]] / s[mast_rc[0], mast_rc[1]]


def sector_map(terrain, dx: float, mast_rc: tuple, height_m: float, z0_m: float, sector_freq, sector_mean_ws,
               **kw) -> dict:
    """Mean wind speed map from a mast's sector statistics (frequency and mean speed per sector, sector 0 centred
    on north, clockwise). One model run per sector."""
    f = np.asarray(sector_freq, dtype=float)
    f = f / f.sum()
    u = np.asarray(sector_mean_ws, dtype=float)
    n = len(f)
    out = np.zeros(np.shape(terrain))
    ratios = []
    for i in range(n):
        r = speedup(terrain, dx, 360.0 * i / n, height_m, z0_m, **kw)
        s = (1.0 + r.speedup) / (1.0 + r.speedup[mast_rc[0], mast_rc[1]])
        ratios.append(s)
        out += f[i] * u[i] * s
    return {"mean_ws": out, "sector_ratio": np.array(ratios), "mast_mean_ws": float((f * u).sum())}
