"""Map pictures: wind speed, layout, noise and shadow flicker, in a dark theme for the app and a light theme for
printed packs. Axes are kilometres east and north of the mast.

Colour rules: magnitude uses one hue from near the surface to strong (never a rainbow); the state of a home
(within or over a limit) uses a fixed status colour together with a marker shape and a legend label, never
colour alone; text stays in the ink colours.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.colors import BoundaryNorm, LinearSegmentedColormap, ListedColormap, Normalize  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Circle, Patch  # noqa: E402

from . import ORG
from .terrain import Grid

THEMES = {
    "dark": {"plane": "#0A1210", "surface": "#101A17", "ink": "#EAF2EE", "ink2": "#B4C4BC", "muted": "#7E928A",
             "line": "#223530", "veil": "#0A1210", "marker": "#FFFFFF",
             # one hue each, dark (near the surface) to light
             "wind": ["#0E2A25", "#14463B", "#1A6652", "#21886B", "#31AC87", "#62CDA6", "#A5E8CC", "#DDF8EC"],
             "noise": ["#3A2416", "#6B3A1C", "#9C5222", "#CC6D2B", "#EC8E4A", "#F8B886"],
             "mast": "#d95926", "home": "#3987e5"},
    "light": {"plane": "#FFFFFF", "surface": "#FFFFFF", "ink": "#16202A", "ink2": "#4A5560", "muted": "#7C868F",
              "line": "#DCE3E1", "veil": "#FFFFFF", "marker": "#16202A",
              # one hue each, light (near the surface) to dark
              "wind": ["#EEF7F5", "#CFE9E5", "#A6D6D0", "#74BDB5", "#3F9F97", "#0F827B", "#006560", "#00433F"],
              "noise": ["#FBE9DA", "#F6CBA6", "#EFA872", "#E28446", "#C4622A", "#964719"],
              "mast": "#eb6834", "home": "#2a78d6"},
}
STATUS = {"good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b"}   # fixed in both themes
FOOT = "Developer estimate, Bohemian Renewables, open-source tools. Not an accredited or bankable assessment."


_FONT = None


def _font() -> str:
    """The house font when it is installed, else the system's."""
    global _FONT
    if _FONT is None:
        from matplotlib import font_manager  # noqa: PLC0415
        have = {f.name for f in font_manager.fontManager.ttflist}
        _FONT = next((n for n in ("Lato", "Segoe UI", "Helvetica Neue", "DejaVu Sans") if n in have), "sans-serif")
    return _FONT


def hillshade(grid: Grid, azimuth: float = 315.0, altitude: float = 45.0, exaggeration: float = 2.0) -> np.ndarray:
    """0 (shadow) to 1 (lit)."""
    gy, gx = np.gradient(grid.elev * exaggeration, grid.res)
    slope = np.pi / 2 - np.arctan(np.hypot(gx, gy))
    aspect = np.arctan2(-gx, gy)
    az, alt = np.deg2rad(360.0 - azimuth + 90.0), np.deg2rad(altitude)
    shade = np.sin(alt) * np.sin(slope) + np.cos(alt) * np.cos(slope) * np.cos(az - aspect - np.pi / 2)
    return np.clip((shade + 1) / 2, 0, 1)


def _figure(grid: Grid, theme: str, title: str, subtitle: str = "", size: float = 9.0):
    t = THEMES[theme]
    plt.rcParams.update({"font.family": _font(), "font.size": 9})
    fig = plt.figure(figsize=(size, size * 0.94), dpi=150, facecolor=t["plane"])
    ax = fig.add_axes([0.085, 0.085, 0.76, 0.80], facecolor=t["surface"])
    for s in ax.spines.values():
        s.set_color(t["line"])
        s.set_linewidth(0.8)
    ax.tick_params(colors=t["muted"], labelsize=8, length=3, width=0.6)
    ax.set_xlabel("km east of the mast", color=t["ink2"], fontsize=8.5)
    ax.set_ylabel("km north of the mast", color=t["ink2"], fontsize=8.5)
    fig.text(0.085, 0.955, title, color=t["ink"], fontsize=13, fontweight="bold", ha="left", va="center")
    if subtitle:
        fig.text(0.085, 0.917, subtitle, color=t["ink2"], fontsize=8.8, ha="left", va="center")
    fig.text(0.085, 0.022, FOOT, color=t["muted"], fontsize=7, ha="left", va="center")
    return fig, ax, t


def _extent(grid: Grid) -> list:
    h = grid.res / 2
    return [(grid.x[0] - h) / 1000, (grid.x[-1] + h) / 1000, (grid.y[0] - h) / 1000, (grid.y[-1] + h) / 1000]


def _terrain(ax, grid: Grid, t: dict, step: float | None = None) -> None:
    span = float(np.nanmax(grid.elev) - np.nanmin(grid.elev))
    step = step or (10 if span < 120 else 25 if span < 400 else 50 if span < 900 else 100)
    lo = np.floor(np.nanmin(grid.elev) / step) * step
    levels = np.arange(lo, np.nanmax(grid.elev) + step, step)
    if len(levels) > 1:
        ax.contour(grid.x / 1000, grid.y / 1000, grid.elev, levels=levels, colors=t["ink"], linewidths=0.35, alpha=0.28)


def _colourbar(fig, mappable, t: dict, label: str, ticks=None):
    cax = fig.add_axes([0.868, 0.30, 0.018, 0.42])
    cb = fig.colorbar(mappable, cax=cax, ticks=ticks)
    cb.outline.set_edgecolor(t["line"])
    cb.outline.set_linewidth(0.6)
    cb.ax.tick_params(colors=t["muted"], labelsize=8, length=2, width=0.5)
    cb.set_label(label, color=t["ink2"], fontsize=8.5)
    return cb


def _mast(ax, t: dict, mast_xy=(0.0, 0.0)) -> Line2D:
    ax.plot(mast_xy[0] / 1000, mast_xy[1] / 1000, marker="^", ms=8, mfc=t["mast"], mec=t["surface"], mew=1.3, ls="none", zorder=9)
    return Line2D([], [], marker="^", ms=8, mfc=t["mast"], mec=t["surface"], mew=1.2, ls="none", label="Met mast")


def _turbines(ax, t: dict, turbines: list, labels: bool = True) -> Line2D:
    xs = [q["x"] / 1000 for q in turbines]
    ys = [q["y"] / 1000 for q in turbines]
    ax.plot(xs, ys, marker="o", ms=7.5, mfc=t["marker"], mec=t["surface"], mew=1.6, ls="none", zorder=7)
    if labels:
        for q in turbines:
            ax.annotate(q.get("id", ""), (q["x"] / 1000, q["y"] / 1000), xytext=(8, -2.5), textcoords="offset points",
                        color=t["ink"], fontsize=7, zorder=8)
    return Line2D([], [], marker="o", ms=7, mfc=t["marker"], mec=t["surface"], mew=1.4, ls="none", label="Turbine")


def _legend(ax, t: dict, handles: list, loc: str = "upper left") -> None:
    if not handles:
        return
    leg = ax.legend(handles=handles, loc=loc, fontsize=7.8, frameon=True, framealpha=0.88, facecolor=t["surface"],
                    edgecolor=t["line"], labelcolor=t["ink2"], borderpad=0.7, handlelength=1.4)
    leg.get_frame().set_linewidth(0.6)
    leg.set_zorder(20)


def _save(fig, path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150, facecolor=fig.get_facecolor())
    plt.close(fig)
    return path


def _feature_lines(ax, grid: Grid, features, color: str, lw: float = 1.0, alpha: float = 0.9, fill=None) -> None:
    for f in features or []:
        rings = f.coords if f.kind == "polygon" else [f.coords] if f.kind == "line" else []
        for ring in rings:
            pts = np.array([grid.to_xy(lat, lon) for lat, lon in ring], dtype=float)
            if f.kind == "polygon":
                pts = np.vstack([pts, pts[:1]])
                if fill:
                    ax.fill(pts[:, 0] / 1000, pts[:, 1] / 1000, color=fill, alpha=0.30, lw=0, zorder=3)
            ax.plot(pts[:, 0] / 1000, pts[:, 1] / 1000, color=color, lw=lw, alpha=alpha, zorder=4,
                    solid_capstyle="round")


def wind_map(grid: Grid, mean_ws: np.ndarray, path, *, height_m: float, theme: str = "dark", title: str = "Wind map",
             subtitle: str = "", mast_xy=(0.0, 0.0), turbines: list | None = None, receptors: list | None = None,
             mask: np.ndarray | None = None, exclusions=None, boundary=None) -> Path:
    """Mean wind speed over shaded terrain. With mask (buildable cells), the rest is veiled so the buildable area
    reads at a glance."""
    fig, ax, t = _figure(grid, theme, title, subtitle)
    cmap = LinearSegmentedColormap.from_list("wind", t["wind"])
    lo, hi = np.nanpercentile(mean_ws, [1, 99])
    lo, hi = np.floor(lo * 2) / 2, np.ceil(hi * 2) / 2
    if hi - lo < 1.0:
        lo, hi = lo - 0.5, hi + 0.5
    norm = Normalize(lo, hi)
    rgb = cmap(norm(mean_ws))[..., :3]
    shade = hillshade(grid)
    rgb = np.clip(rgb * (0.62 + 0.38 * shade[..., None] * 1.25), 0, 1)
    ax.imshow(rgb, origin="lower", extent=_extent(grid), interpolation="bilinear", zorder=1)
    _terrain(ax, grid, t)
    handles = []
    if mask is not None:
        veil = np.zeros(mask.shape + (4,))
        veil[..., :3] = matplotlib.colors.to_rgb(t["veil"])
        veil[..., 3] = np.where(mask, 0.0, 0.62)
        ax.imshow(veil, origin="lower", extent=_extent(grid), interpolation="nearest", zorder=2)
        ax.contour(grid.x / 1000, grid.y / 1000, mask.astype(float), levels=[0.5], colors=t["ink"], linewidths=0.7, alpha=0.75, zorder=3)
        handles.append(Patch(facecolor=cmap(0.6), edgecolor=t["ink"], lw=0.7, label="Buildable area"))
        handles.append(Patch(facecolor=t["veil"], edgecolor=t["line"], lw=0.7, alpha=0.9, label="Ruled out by the constraints"))
    if boundary:
        _feature_lines(ax, grid, boundary, t["ink"], lw=1.3)
        handles.append(Line2D([], [], color=t["ink"], lw=1.3, label="Site boundary"))
    if exclusions:
        _feature_lines(ax, grid, exclusions, t["muted"], lw=0.9)
        handles.append(Line2D([], [], color=t["muted"], lw=0.9, label="Exclusion zones, roads and lines"))
    if receptors:
        ax.plot([r["x"] / 1000 for r in receptors], [r["y"] / 1000 for r in receptors], marker="s", ms=3.6, mfc=t["home"],
                mec=t["surface"], mew=0.6, ls="none", zorder=5)
        handles.append(Line2D([], [], marker="s", ms=5, mfc=t["home"], mec=t["surface"], mew=0.6, ls="none", label="Home"))
    handles.append(_mast(ax, t, mast_xy))
    if turbines:
        handles.append(_turbines(ax, t, turbines))
    _legend(ax, t, handles)
    sm = plt.cm.ScalarMappable(norm=norm, cmap=cmap)
    _colourbar(fig, sm, t, f"Mean wind speed at {height_m:g} m, m/s")
    ax.set_xlim(_extent(grid)[:2])
    ax.set_ylim(_extent(grid)[2:])
    ax.set_aspect("equal")
    return _save(fig, path)


def _status_points(ax, t: dict, receptors: list, over_key: str, labels: tuple) -> list:
    ok = [r for r in receptors if not r.get(over_key)]
    bad = [r for r in receptors if r.get(over_key)]
    handles = []
    if ok:
        ax.plot([r["x"] / 1000 for r in ok], [r["y"] / 1000 for r in ok], marker="o", ms=5.5, mfc=STATUS["good"],
                mec=t["surface"], mew=1.0, ls="none", zorder=6)
        handles.append(Line2D([], [], marker="o", ms=6, mfc=STATUS["good"], mec=t["surface"], mew=1.0, ls="none",
                              label=f"{labels[0]} ({len(ok)})"))
    if bad:
        ax.plot([r["x"] / 1000 for r in bad], [r["y"] / 1000 for r in bad], marker="X", ms=8, mfc=STATUS["critical"],
                mec=t["surface"], mew=1.0, ls="none", zorder=7)
        handles.append(Line2D([], [], marker="X", ms=8, mfc=STATUS["critical"], mec=t["surface"], mew=1.0, ls="none",
                              label=f"{labels[1]} ({len(bad)})"))
    return handles


def _zoom(ax, grid: Grid, turbines: list, receptors: list, pad_m: float) -> None:
    xs = [q["x"] for q in turbines] + [r["x"] for r in receptors]
    ys = [q["y"] for q in turbines] + [r["y"] for r in receptors]
    if not xs:
        return
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    half = max(max(xs) - min(xs), max(ys) - min(ys)) / 2 + pad_m
    e = _extent(grid)
    ax.set_xlim(max(e[0], (cx - half) / 1000), min(e[1], (cx + half) / 1000))
    ax.set_ylim(max(e[2], (cy - half) / 1000), min(e[3], (cy + half) / 1000))


def noise_map(grid: Grid, levels: np.ndarray, turbines: list, receptors: list, path, *, limit: float,
              theme: str = "dark", title: str = "Turbine noise", subtitle: str = "") -> Path:
    """levels: dB(A) at every cell (environment.noise.on_grid). receptors: rows of noise.at_receptors."""
    fig, ax, t = _figure(grid, theme, title, subtitle)
    shade = hillshade(grid)
    base = np.array(matplotlib.colors.to_rgb(t["surface"]))
    ax.imshow(np.clip(base[None, None, :] * (0.75 + 0.5 * shade[..., None]), 0, 1), origin="lower", extent=_extent(grid),
              interpolation="bilinear", zorder=1)
    _terrain(ax, grid, t)
    bounds = [35, 40, 45, 50, 55, 60, 200]
    cmap = ListedColormap(t["noise"])
    norm = BoundaryNorm(bounds, cmap.N)
    ax.contourf(grid.x / 1000, grid.y / 1000, levels, levels=bounds, cmap=cmap, norm=norm, alpha=0.82, zorder=2)
    ax.contour(grid.x / 1000, grid.y / 1000, levels, levels=[limit], colors=t["ink"], linewidths=1.6, zorder=4)
    handles = [Line2D([], [], color=t["ink"], lw=1.6, label=f"{limit:g} dB(A) limit")]
    handles += _status_points(ax, t, receptors, "over_limit", ("Home within the limit", "Home over the limit"))
    handles.append(_turbines(ax, t, turbines))
    _legend(ax, t, handles)
    sm = plt.cm.ScalarMappable(norm=BoundaryNorm(bounds[:-1] + [65], cmap.N), cmap=cmap)
    cb = _colourbar(fig, sm, t, "Sound level, dB(A)", ticks=bounds[:-1])
    cb.ax.set_yticklabels([f"{b:g}" for b in bounds[:-1]])
    ax.set_aspect("equal")
    _zoom(ax, grid, turbines, [], 2600.0)
    return _save(fig, path)


def flicker_map(grid: Grid, turbines: list, receptors: list, path, *, rotor_m: float, range_rotors: float = 10.0,
                theme: str = "dark", title: str = "Shadow flicker", subtitle: str = "", label_worst: int = 5) -> Path:
    """receptors: rows of flicker.at_receptors. Rings show the distance within which flicker is counted."""
    fig, ax, t = _figure(grid, theme, title, subtitle)
    shade = hillshade(grid)
    base = np.array(matplotlib.colors.to_rgb(t["surface"]))
    ax.imshow(np.clip(base[None, None, :] * (0.75 + 0.5 * shade[..., None]), 0, 1), origin="lower", extent=_extent(grid),
              interpolation="bilinear", zorder=1)
    _terrain(ax, grid, t)
    for q in turbines:
        ax.add_patch(Circle((q["x"] / 1000, q["y"] / 1000), range_rotors * rotor_m / 1000, fill=False, ec=t["muted"], lw=0.5,
                            alpha=0.45, zorder=3))
    handles = [Line2D([], [], color=t["muted"], lw=0.8, label=f"{range_rotors:g} rotor diameters from a turbine")]
    handles += _status_points(ax, t, receptors, "over_limit", ("Home within the limits", "Home over a limit"))
    handles.append(_turbines(ax, t, turbines))
    for r in [r for r in receptors if r.get("hours_per_year", 0) > 0][:label_worst]:     # rows arrive worst first
        ax.annotate(f"{r['name']}  {r['hours_per_year']:.0f} h/yr", (r["x"] / 1000, r["y"] / 1000), xytext=(7, -9),
                    textcoords="offset points", color=t["ink"], fontsize=7, zorder=9)
    _legend(ax, t, handles)
    ax.set_aspect("equal")
    _zoom(ax, grid, turbines, [], range_rotors * rotor_m + 500.0)
    return _save(fig, path)


def credit_line(*credits: str) -> str:
    return " ".join(c for c in credits if c) + f" Prepared by {ORG}."
