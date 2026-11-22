"""Charts for the Word reports, in the house colours on white. Each returns PNG bytes, or None when there is
nothing to draw."""
from __future__ import annotations

import io
from math import gamma

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from ..maps import _font

NAVY, TEAL, INK, MUTED, LINE, ORANGE = "#222952", "#008781", "#16202A", "#4A5560", "#DCE3E1", "#D9762F"
SERIES = [NAVY, TEAL, ORANGE, "#7C6BB1", "#3987E5", "#8A9A2E", "#B3475F", "#4A5560"]     # fixed order, by height from the top


def _fig(w: float = 7.0, h: float = 3.4, polar: bool = False):
    plt.rcParams.update({"font.family": _font(), "font.size": 8.5})
    fig = plt.figure(figsize=(w, h), dpi=200, facecolor="white")
    ax = fig.add_subplot(111, projection="polar" if polar else None)
    if not polar:
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        for s in ("left", "bottom"):
            ax.spines[s].set_color(LINE)
        ax.grid(axis="y", color=LINE, linewidth=0.6)
        ax.set_axisbelow(True)
    ax.tick_params(colors=MUTED, labelsize=8, length=3, width=0.6)
    return fig, ax


def _png(fig) -> bytes:
    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png", facecolor="white")
    plt.close(fig)
    return buf.getvalue()


def monthly(months: list, lt_monthly: list | None = None, min_share: float = 0.7) -> bytes | None:
    """months: [{"month", "mean", "share"}]; months short of min_share valid data are drawn pale."""
    if not months:
        return None
    fig, ax = _fig()
    x = np.arange(len(months))
    ok = np.array([m["share"] >= min_share for m in months])
    v = np.array([m["mean"] for m in months], dtype=float)
    ax.bar(x[ok], v[ok], color=TEAL, width=0.72, label=f"Month with at least {100 * min_share:.0f} % valid data")
    if (~ok).any():
        ax.bar(x[~ok], v[~ok], color="#A9D5D1", width=0.72, label="Month with less (left out of the monthly means)")
    if lt_monthly:
        ref = {m["month"]: m["reference"] for m in lt_monthly}
        r = np.array([ref.get(m["month"], np.nan) for m in months], dtype=float)
        if np.isfinite(r).any():
            ax.plot(x, r, color=NAVY, marker="o", ms=3, lw=1.2, label="Reanalysis reference, same months (its own height)")
    step = max(1, len(months) // 14)
    ax.set_xticks(x[::step])
    ax.set_xticklabels([m["month"] for m in months][::step], rotation=45, ha="right")
    ax.set_ylabel("Mean wind speed, m/s", color=MUTED)
    ax.legend(fontsize=7.5, frameon=False, loc="upper center", bbox_to_anchor=(0.5, 1.2), ncol=2)
    return _png(fig)


def rose(sectors: list, kind: str = "freq", rho: float = 1.225) -> bytes | None:
    """Frequency (per cent of the time) or energy (per cent of the wind energy) by direction."""
    if not sectors:
        return None
    centres = np.deg2rad([float(s["centre"]) for s in sectors])
    if kind == "freq":
        v = 100.0 * np.array([float(s["freq"]) for s in sectors])
    else:
        e = np.array([float(s["freq"]) * float(s["A"]) ** 3 * gamma(1 + 3 / float(s["k"])) if s["A"] and s["k"] else 0.0 for s in sectors])
        v = 100.0 * e / e.sum() if e.sum() else e
    fig, ax = _fig(3.6, 3.7, polar=True)
    ax.set_theta_zero_location("N")
    ax.set_theta_direction(-1)
    ax.bar(centres, v, width=2 * np.pi / len(sectors) * 0.92, color=TEAL if kind == "freq" else NAVY, edgecolor="white", linewidth=0.7)
    ax.set_xticks(np.deg2rad(np.arange(0, 360, 45)))
    ax.set_xticklabels(["N", "NE", "E", "SE", "S", "SW", "W", "NW"], color=INK)
    ax.tick_params(axis="y", labelsize=6.5)
    ax.set_rlabel_position(202)
    ax.grid(color=LINE, linewidth=0.6)
    ax.spines["polar"].set_color(LINE)
    ax.set_title("Share of the time, %" if kind == "freq" else "Share of the wind energy, %", fontsize=9, color=NAVY, pad=12)
    return _png(fig)


def distribution(A: float, k: float, speeds=None, height_m: float | None = None, label: str | None = None) -> bytes | None:
    """The Weibull fit, over the histogram of the records when they are at hand."""
    if not (A and k):
        return None
    fig, ax = _fig()
    x = np.linspace(0, 30, 301)
    if speeds is not None and len(speeds):
        ax.hist(np.asarray(speeds, dtype=float), bins=np.arange(0, 31, 1.0), density=True, color="#A9D5D1", edgecolor="white",
                linewidth=0.5, label="Valid 10-minute records")
    pdf = (k / A) * (x / A) ** (k - 1) * np.exp(-(x / A) ** k)
    ax.plot(x, pdf, color=NAVY, lw=1.6, label=f"{label or 'Weibull fit'}: A = {A:.2f} m/s, k = {k:.2f}")
    ax.set_xlim(0, min(30, max(20, A * 2.6)))
    ax.set_xlabel("Wind speed" + (f" at {height_m:g} m" if height_m else "") + ", m/s", color=MUTED)
    ax.set_ylabel("Share of the time per m/s", color=MUTED)
    ax.legend(fontsize=7.5, frameon=False)
    return _png(fig)


def profile(heights: list, alpha: float | None, lt_top: float | None = None, top_m: float | None = None,
            to_m: float = 180.0) -> bytes | None:
    """Mean wind speed against height: the measured heights, and the power law carried upward."""
    pts = [(h["height_m"], h.get("momm_ws") or h.get("mean_ws")) for h in heights if (h.get("momm_ws") or h.get("mean_ws"))]
    if not pts:
        return None
    fig, ax = _fig(4.6, 3.9)
    ax.grid(axis="x", color=LINE, linewidth=0.6)
    z, v = zip(*sorted(pts))
    ax.plot(v, z, "o", color=TEAL, ms=5, label="Measured period (mean of monthly means)")
    if alpha is not None and top_m:
        zz = np.linspace(min(z) * 0.8, to_m, 80)
        ax.plot(v[-1] * (zz / z[-1]) ** alpha, zz, color=TEAL, lw=1.0, ls="--", label=f"Measured period, power law, α = {alpha:.3f}")
        if lt_top and abs(lt_top / v[-1] - 1) > 0.002:
            ax.plot(lt_top * (zz / top_m) ** alpha, zz, color=NAVY, lw=1.4, label="Long-term mean, same power law")
        ax.axhline(top_m, color=MUTED, lw=0.6, ls=":")
    ax.set_xlabel("Mean wind speed, m/s", color=MUTED)
    ax.set_ylabel("Height above ground, m", color=MUTED)
    ax.legend(fontsize=7.5, frameon=False, loc="lower right")
    return _png(fig)


def lines(df, ylabel: str, xlabel: str = "", suffix: str = " m") -> bytes | None:
    """One line for each column of a table whose first column is the x axis (hour of day)."""
    if df is None or not len(df) or df.shape[1] < 2:
        return None
    fig, ax = _fig()
    x = df.iloc[:, 0]
    for i, c in enumerate(df.columns[1:9]):
        ax.plot(x, df[c], color=SERIES[i % len(SERIES)], lw=1.4, label=f"{c}{suffix if str(c).replace('.', '').isdigit() else ''}")
    ax.set_xlabel(xlabel, color=MUTED)
    ax.set_ylabel(ylabel, color=MUTED)
    ax.legend(fontsize=7.5, frameon=False, ncol=4, loc="upper center", bbox_to_anchor=(0.5, 1.18))
    return _png(fig)


def turbulence(ti) -> bytes | None:
    """Mean and representative turbulence intensity by wind speed, against the reference classes when listed."""
    if ti is None or not len(ti):
        return None
    cols = {str(c): c for c in ti.columns}
    x = ti[cols.get("ws_bin_mps", ti.columns[0])]
    fig, ax = _fig()
    for name, colour, style, label in (("mean_TI", TEAL, "-", "Mean"), ("rep_TI", NAVY, "-", "Representative (mean + 1.28 standard deviations)")):
        if name in cols:
            ax.plot(x, ti[cols[name]], color=colour, ls=style, lw=1.5, marker="o", ms=2.5, label=label)
    for name, style in (("IEC_A", "--"), ("IEC_B", "-."), ("IEC_C", ":")):
        if name in cols:
            ax.plot(x, ti[cols[name]], color=MUTED, ls=style, lw=0.8, label=f"Reference class {name[-1]}")
    ax.set_xlabel("Wind speed, m/s", color=MUTED)
    ax.set_ylabel("Turbulence intensity", color=MUTED)
    ax.set_ylim(0, None)
    ax.legend(fontsize=7.2, frameon=False, ncol=2)
    return _png(fig)


def correlation(lt_monthly: list, r: float | None = None) -> bytes | None:
    """Monthly means of the mast against the reanalysis reference."""
    pts = [(m["reference"], m["mast"]) for m in lt_monthly or [] if m.get("reference") is not None and m.get("mast") is not None]
    if len(pts) < 3:
        return None
    fig, ax = _fig(4.2, 3.8)
    ax.grid(axis="x", color=LINE, linewidth=0.6)
    x, y = map(np.array, zip(*pts))
    ax.plot(x, y, "o", color=TEAL, ms=5)
    slope = float((x * y).sum() / (x * x).sum())
    xx = np.array([0, x.max() * 1.08])
    ax.plot(xx, slope * xx, color=NAVY, lw=1.2, label=f"Ratio through the origin: {slope:.3f}" + (f"; r = {r:.2f}" if r is not None else ""))
    ax.set_xlim(x.min() * 0.9, x.max() * 1.08)
    ax.set_ylim(y.min() * 0.9, y.max() * 1.08)
    ax.set_xlabel("Reanalysis reference, monthly mean, m/s", color=MUTED)
    ax.set_ylabel("Mast, monthly mean, m/s", color=MUTED)
    ax.legend(fontsize=7.5, frameon=False, loc="upper left")
    return _png(fig)


def recovery(rec) -> bytes | None:
    """Valid data by sensor and month as a grid of shades (the darker, the more complete)."""
    if rec is None or not len(rec) or rec.shape[1] < 2:
        return None
    names = [str(v) for v in rec.iloc[:, 0]]
    vals = rec.iloc[:, 1:].apply(lambda c: np.asarray(c, dtype=float)).to_numpy(dtype=float)
    fig, ax = _fig(7.0, max(1.8, 0.23 * len(names) + 1.0))
    ax.grid(False)
    cmap = matplotlib.colors.LinearSegmentedColormap.from_list("rec", ["#FFFFFF", "#CFE9E5", "#3F9F97", "#00433F"])
    im = ax.imshow(vals, aspect="auto", cmap=cmap, vmin=0, vmax=100)
    ax.set_yticks(range(len(names)))
    ax.set_yticklabels(names, fontsize=7)
    cols = [str(c) for c in rec.columns[1:]]
    step = max(1, len(cols) // 14)
    ax.set_xticks(range(0, len(cols), step))
    ax.set_xticklabels(cols[::step], rotation=45, ha="right", fontsize=7)
    cb = fig.colorbar(im, ax=ax, fraction=0.025, pad=0.02)
    cb.set_label("Valid data, %", fontsize=7.5, color=MUTED)
    cb.ax.tick_params(labelsize=7)
    return _png(fig)


def turbine_bars(positions: list) -> bytes | None:
    """Net energy of each turbine, and under it the free wind speed there: two panels, one measure each."""
    if not positions:
        return None
    plt.rcParams.update({"font.family": _font(), "font.size": 8.5})
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(7.0, 4.2), dpi=200, facecolor="white", sharex=True,
                                 gridspec_kw={"height_ratios": [3, 2]})
    ids = [p["id"] for p in positions]
    x = np.arange(len(ids))
    a1.bar(x, [p["p50_mwh"] / 1000.0 for p in positions], color=TEAL, width=0.7)
    a1.set_ylabel("Net energy, P50,\nGWh a year", color=MUTED)
    ws = [p["free_ws"] for p in positions]
    a2.plot(x, ws, "o-", color=NAVY, ms=4, lw=0.8)
    a2.set_ylabel("Free wind at\nhub height, m/s", color=MUTED)
    a2.set_ylim(min(ws) - 0.3, max(ws) + 0.3)
    a2.set_xticks(x)
    a2.set_xticklabels(ids, rotation=90 if len(ids) > 16 else 0, fontsize=7 if len(ids) > 16 else 8)
    for ax in (a1, a2):
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        for s in ("left", "bottom"):
            ax.spines[s].set_color(LINE)
        ax.grid(axis="y", color=LINE, linewidth=0.6)
        ax.set_axisbelow(True)
        ax.tick_params(colors=MUTED, labelsize=8, length=3, width=0.6)
    return _png(fig)


def cascade(gross: float, after_wake: float, net: float) -> bytes | None:
    """From gross to net energy."""
    fig, ax = _fig(5.2, 2.9)
    steps = [("Gross", gross, NAVY), ("After wakes", after_wake, TEAL), ("Net, P50", net, TEAL)]
    ax.bar([s[0] for s in steps], [s[1] for s in steps], color=[s[2] for s in steps], width=0.55)
    for i, s in enumerate(steps):
        ax.text(i, s[1], f"{s[1]:.1f}", ha="center", va="bottom", fontsize=8.5, color=INK)
    ax.set_ylabel("GWh a year", color=MUTED)
    ax.set_ylim(0, gross * 1.12)
    return _png(fig)


def budget(terms: dict) -> bytes | None:
    """The uncertainty terms, in per cent of energy, largest first."""
    if not terms:
        return None
    items = sorted(terms.items(), key=lambda kv: kv[1])
    fig, ax = _fig(7.0, 0.36 * len(items) + 1.0)
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", color=LINE, linewidth=0.6)
    ax.barh([k.split(" (")[0] for k, _ in items], [v for _, v in items], color=TEAL, height=0.6)
    for i, (_, v) in enumerate(items):
        ax.text(v, i, f" {v:.1f}", va="center", fontsize=8, color=INK)
    ax.set_xlabel("1 standard deviation, % of energy", color=MUTED)
    ax.tick_params(axis="y", labelsize=7.5)
    return _png(fig)


def exceedance(p50: float, sigma_pct: dict) -> bytes | None:
    """Energy against the probability of exceeding it, for the long term, ten years and a single year."""
    from statistics import NormalDist  # noqa: PLC0415
    fig, ax = _fig(5.6, 3.2)
    ax.grid(axis="x", color=LINE, linewidth=0.6)
    p = np.linspace(0.01, 0.99, 99)
    z = np.array([NormalDist().inv_cdf(v) for v in p])
    for (key, label), colour in zip((("10_year", "10-year average"), ("1_year", "Any single year")), (NAVY, ORANGE)):
        if key in sigma_pct:
            ax.plot(100 * p, p50 * (1 - z * sigma_pct[key] / 100.0), color=colour, lw=1.5, label=label)
    for q in (50, 75, 90):
        ax.axvline(q, color=MUTED, lw=0.5, ls=":")
    ax.set_xlabel("Probability of exceedance, %", color=MUTED)
    ax.set_ylabel("Net energy, GWh a year", color=MUTED)
    ax.set_xticks([10, 25, 50, 75, 90, 99])
    ax.legend(fontsize=7.5, frameon=False)
    return _png(fig)


def shading(pair, height_m, wakes: list | None = None) -> bytes | None:
    """Tower shading at one level: the ratio of the two anemometers by wind direction. Away from the mast's wake the
    ratio sits near 1; where one cup stands behind the mast it dips or peaks. wakes: (direction, label) to mark."""
    if pair is None or len(pair) < 6:
        return None
    cols = {str(c): c for c in pair.columns}
    x = np.asarray(pair[cols.get("sector_centre_deg", pair.columns[0])], dtype=float)
    y = np.asarray(pair[cols.get("median_ratio", pair.columns[1])], dtype=float)
    fig, ax = _fig(7.0, 2.9)
    ax.axhline(1.0, color=MUTED, lw=0.7)
    ax.axhspan(0.99, 1.01, color="#E6F3F2", zorder=0, label="Within 1 %")
    ax.plot(x, y, color=TEAL, lw=1.4, marker="o", ms=3, label=f"Median ratio of the two anemometers at {height_m:g} m")
    for deg, label in wakes or []:
        if deg is not None and np.isfinite(deg):
            ax.axvline(float(deg), color=ORANGE, lw=1.0, ls="--")
            ax.text(float(deg), ax.get_ylim()[1], f" {label}", color=ORANGE, fontsize=7, va="top", rotation=90)
    ax.set_xlim(0, 360)
    ax.set_xticks(range(0, 361, 30))
    span = max(0.06, float(np.nanmax(np.abs(y - 1.0))) * 1.25) if np.isfinite(y).any() else 0.06
    ax.set_ylim(1 - span, 1 + span)
    ax.set_xlabel("Wind direction, degrees", color=MUTED)
    ax.set_ylabel("Ratio", color=MUTED)
    ax.legend(fontsize=7.2, frameon=False, loc="lower left", ncol=2)
    return _png(fig)


def scatter(x, y, xlabel: str, ylabel: str) -> bytes | None:
    """Two wind speed series against each other as a density of records, with the 1:1 line and the fitted ratio."""
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    ok = np.isfinite(x) & np.isfinite(y)
    if ok.sum() < 200:
        return None
    x, y = x[ok], y[ok]
    fig, ax = _fig(4.2, 4.0)
    ax.grid(axis="x", color=LINE, linewidth=0.6)
    top = float(np.percentile(np.concatenate([x, y]), 99.9)) * 1.05
    cmap = matplotlib.colors.LinearSegmentedColormap.from_list("d", ["#E6F3F2", "#74BDB5", "#008781", "#00433F"])
    ax.hexbin(x, y, gridsize=55, extent=(0, top, 0, top), cmap=cmap, mincnt=1, bins="log", linewidths=0)
    ax.plot([0, top], [0, top], color=MUTED, lw=0.8, ls="--", label="1 : 1")
    slope = float((x * y).sum() / (x * x).sum())
    r = float(np.corrcoef(x, y)[0, 1])
    ax.plot([0, top], [0, slope * top], color=NAVY, lw=1.2, label=f"Ratio {slope:.3f}; r = {r:.3f}; {len(x):,} records")
    ax.set_xlim(0, top)
    ax.set_ylim(0, top)
    ax.set_aspect("equal")
    ax.set_xlabel(xlabel, color=MUTED)
    ax.set_ylabel(ylabel, color=MUTED)
    ax.legend(fontsize=7.2, frameon=False, loc="upper left")
    return _png(fig)
