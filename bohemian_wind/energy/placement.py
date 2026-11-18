"""Turbine placement: best cells first, an elliptical spacing rule aligned with the prevailing wind, and an
optional rule that keeps the wind farm in one piece (roads and cables)."""
from __future__ import annotations

import math

import numpy as np


def along_cross(dx, dy, theta_deg: float) -> tuple:
    """Components of a separation along and across the wind (theta = direction the wind comes from)."""
    t = math.radians(theta_deg)
    ux, uy = math.sin(t), math.cos(t)
    return dx * ux + dy * uy, -dx * uy + dy * ux


def spacing_ok(xs, ys, x0, y0, d: float, cross_d: float, along_d: float, theta: float) -> np.ndarray:
    """True for points outside the spacing ellipse round (x0, y0): cross_d rotor diameters across the wind,
    along_d along it."""
    al, cr = along_cross(np.asarray(xs) - x0, np.asarray(ys) - y0, theta)
    return (cr / (cross_d * d)) ** 2 + (al / (along_d * d)) ** 2 >= 1.0


def greedy(xs, ys, value, d: float, n: int, theta: float, cross_d: float = 3.0, along_d: float = 7.0,
           link_m: float | None = 2500.0) -> np.ndarray:
    """Indices of up to n cells: the best cell first, then the best cell that keeps the spacing rule and (with
    link_m) lies within link_m of a turbine already placed."""
    xs, ys, value = np.asarray(xs, dtype=float), np.asarray(ys, dtype=float), np.asarray(value, dtype=float)
    feasible = np.isfinite(value)
    connected = np.zeros(len(xs), bool)
    chosen = []
    for _ in range(n):
        ok = feasible & (connected if (chosen and link_m) else True)
        if not ok.any():
            break
        i = int(np.flatnonzero(ok)[np.argmax(value[ok])])
        chosen.append(i)
        feasible &= spacing_ok(xs, ys, xs[i], ys[i], d, cross_d, along_d, theta)
        if link_m:
            connected |= np.hypot(xs - xs[i], ys - ys[i]) <= link_m
    return np.array(chosen, dtype=int)


def connected_set(xs, ys, link_m: float | None) -> bool:
    """True when every turbine can be reached from every other in hops of at most link_m."""
    n = len(xs)
    if n <= 1 or not link_m:
        return True
    xs, ys = np.asarray(xs), np.asarray(ys)
    near = np.hypot(xs[:, None] - xs[None, :], ys[:, None] - ys[None, :]) <= link_m
    seen, stack = {0}, [0]
    while stack:
        i = stack.pop()
        for j in np.flatnonzero(near[i]):
            if int(j) not in seen:
                seen.add(int(j))
                stack.append(int(j))
    return len(seen) == n


def min_spacing_d(xs, ys, d: float) -> float:
    """Smallest centre-to-centre distance in rotor diameters."""
    xs, ys = np.asarray(xs), np.asarray(ys)
    if len(xs) < 2:
        return float("inf")
    dist = np.hypot(xs[:, None] - xs[None, :], ys[:, None] - ys[None, :])
    dist[np.eye(len(xs), dtype=bool)] = np.inf
    return float(dist.min() / d)
