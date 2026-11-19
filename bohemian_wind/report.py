"""HTML report with embedded charts, plus Excel and CSV outputs."""
from __future__ import annotations

import base64
import html
import io
import json
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import matplotlib.dates as mdates  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

from . import APP_NAME, DISCLAIMER, ORG, __version__  # noqa: E402
from .points import VISIT_PAD  # noqa: E402
from .qc import (FLAG_ORDER, FLAG_TEXT, NO_SIGNAL_V, PRESSURE_BAND_HPA, STUCK_RECORDS,  # noqa: E402
                 maintenance_mask, site_pressure)
from .sympro import SAME_PLACE_M  # noqa: E402

# Validated categorical order (light surface) and one-hue sequential ramp
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
SEQ = ["#fcfcfb", "#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
INK, INK2, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#8a8984", "#e4e3df", "#fcfcfb"

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": MUTED, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8, "axes.spines.top": False,
    "axes.spines.right": False, "font.size": 9, "axes.titlesize": 10, "axes.titleweight": "bold",
    "axes.titlecolor": INK, "legend.frameon": False, "lines.linewidth": 2.0, "axes.axisbelow": True,
})


def _png(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def chart_recovery(rec: pd.DataFrame) -> str | None:
    if rec.empty:
        return None
    loss = (100.0 - rec.clip(0, 100)).to_numpy()
    h = max(2.2, 0.32 * len(rec) + 1.2)
    fig, ax = plt.subplots(figsize=(max(6.5, 0.55 * rec.shape[1] + 2.5), h))
    cmap = matplotlib.colors.LinearSegmentedColormap.from_list("loss", SEQ)
    im = ax.imshow(loss, aspect="auto", cmap=cmap, vmin=0, vmax=100)
    ax.set_xticks(range(rec.shape[1]), rec.columns, rotation=45, ha="right")
    ax.set_yticks(range(len(rec)), rec.index)
    ax.grid(False)
    for i in range(rec.shape[0]):
        for j in range(rec.shape[1]):
            v = rec.iat[i, j]
            ax.text(j, i, f"{v:.0f}", ha="center", va="center", fontsize=7,
                    color="white" if loss[i, j] > 55 else INK)
    cb = fig.colorbar(im, ax=ax, fraction=0.025, pad=0.01)
    cb.set_label("missing or flagged, % of expected records", color=INK2)
    ax.set_title("Valid data recovery by month, % (darker = more data lost)", loc="left")
    return _png(fig)


def chart_lines(df: pd.DataFrame, title: str, ylabel: str, xlabel: str = "", label_fmt="{:g} m") -> str | None:
    if df.empty:
        return None
    fig, ax = plt.subplots(figsize=(8, 3.2))
    x = df.index.to_timestamp() if isinstance(df.index, pd.PeriodIndex) else df.index
    dense = len(df) > 40
    for i, col in enumerate(df.columns):
        c = PALETTE[i % len(PALETTE)]
        ax.plot(x, df[col], color=c, label=label_fmt.format(col), linewidth=1.2 if dense else 2.0,
                marker=None if dense else "o", markersize=4)
    ax.set_title(title, loc="left")
    ax.set_ylabel(ylabel)
    if xlabel:
        ax.set_xlabel(xlabel)
    if len(df.columns) > 1:
        ax.legend(ncol=min(len(df.columns), 6), loc="upper left", bbox_to_anchor=(0, -0.18))
    return _png(fig)


def chart_pair(pr: dict) -> str | None:
    tab = pr.get("sectors")
    if tab is None or tab["count"].fillna(0).sum() == 0:
        return None
    fig, ax = plt.subplots(figsize=(8, 3.0))
    ok = tab["count"].fillna(0) >= 20
    ax.axhline(1.0, color=MUTED, linewidth=1)
    for w in pr.get("wake", []):
        if w.get("expected_wake_deg") is not None:
            ax.axvline(w["expected_wake_deg"], color=MUTED, linestyle="--", linewidth=1)
            ax.annotate(f"expected wake, {w.get('boom') or 'this'} cup", (w["expected_wake_deg"], 0.0),
                        xycoords=("data", "axes fraction"), xytext=(-3, 4), textcoords="offset points",
                        fontsize=7, color=INK2, rotation=90, ha="right", va="bottom")
    ax.plot(tab.index[ok], tab["median_ratio"][ok], color=PALETTE[0], marker="o", markersize=4)
    ax.set_xlim(-5, 360)
    ax.set_xticks(range(0, 361, 45))
    ax.set_xlabel(f"Wind direction, deg ({pr.get('vane', 'vane')})")
    ax.set_ylabel(f"{pr['a']} / {pr['b']}")
    ax.set_title(f"Paired cups at {pr['height']:g} m: speed ratio by direction (dips = mast shadow)", loc="left")
    return _png(fig)


def chart_shear(sh: dict) -> str | None:
    if not sh:
        return None
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(8.5, 3.0), gridspec_kw={"width_ratios": [1, 1.6]})
    hts, means = sh["heights"], sh["means"].to_numpy()
    zz = np.linspace(min(hts) * 0.8, max(hts) * 1.25, 50)
    ref = means[-1] * (zz / hts[-1]) ** sh["alpha_fit"]
    ax1.plot(ref, zz, color=MUTED, linewidth=1.2, label=f"power law, alpha {sh['alpha_fit']:.3f}")
    ax1.plot(means, hts, "o", color=PALETTE[0], markersize=6, label="measured mean")
    ax1.set_xlabel("Mean wind speed, m/s (concurrent data)")
    ax1.set_ylabel("Height, m")
    ax1.set_title("Vertical profile", loc="left")
    ax1.legend(loc="upper left", fontsize=8)
    bh = sh["by_hour"]
    ax2.plot(bh.index, bh.to_numpy(), color=PALETTE[0], marker="o", markersize=4)
    ax2.set_xticks(range(0, 24, 3))
    ax2.set_xlabel("Hour of day (logger time)")
    ax2.set_ylabel(f"Shear exponent {sh['lo']:g}-{sh['hi']:g} m")
    ax2.set_title("Shear by hour of day", loc="left")
    fig.tight_layout()
    return _png(fig)


def chart_ti(ti: pd.DataFrame, height) -> str | None:
    t = ti[ti["count"] >= 10]
    if t.empty:
        return None
    fig, ax = plt.subplots(figsize=(8, 3.2))
    styles = {"A+": ":", "A": "--", "B": "-.", "C": (0, (1, 3))}
    xl = 5 if 5 in t.index else t.index[0]  # label where the curves are well apart
    for cls, ls in styles.items():
        ax.plot(t.index, t[f"IEC_{cls}"], color=MUTED, linewidth=1.1, linestyle=ls)
        ax.annotate(f"IEC {cls}", (xl, t.loc[xl, f"IEC_{cls}"]), xytext=(3, 2),
                    textcoords="offset points", color=INK2, fontsize=7, va="bottom")
    ax.plot(t.index, t["rep_TI"], color=PALETTE[1], marker="o", markersize=4, label="representative TI (mean + 1.28 SD)")
    ax.plot(t.index, t["mean_TI"], color=PALETTE[0], marker="o", markersize=4, label="mean TI")
    ax.set_xlabel("Wind speed bin, m/s")
    ax.set_ylabel("Turbulence intensity")
    ax.set_title(f"Turbulence intensity at {height:g} m against IEC 61400-1 reference curves", loc="left")
    ax.legend(loc="upper right")
    return _png(fig)


def chart_rose(rose: pd.DataFrame, title: str) -> str | None:
    if rose is None or rose["freq_%"].sum() == 0:
        return None
    fig = plt.figure(figsize=(4.2, 4.2))
    ax = fig.add_subplot(projection="polar")
    ax.set_theta_zero_location("N")
    ax.set_theta_direction(-1)
    width = 2 * np.pi / len(rose) * 0.85
    ax.bar(np.radians(rose.index.to_numpy()), rose["freq_%"].to_numpy(), width=width,
           color=PALETTE[0], edgecolor=SURFACE, linewidth=1)
    ax.set_title(title, loc="left", pad=14)
    ax.tick_params(colors=INK2, labelsize=7)
    return _png(fig)


def chart_weibull(ws: pd.Series, A: float, k: float, height) -> str | None:
    if ws.empty or not np.isfinite(A):
        return None
    fig, ax = plt.subplots(figsize=(8, 3.0))
    bins = np.arange(0, np.ceil(ws.max()) + 1, 1.0)
    ax.hist(ws, bins=bins, density=True, color=PALETTE[0], edgecolor=SURFACE, linewidth=1, label="measured")
    x = np.linspace(0.01, bins[-1], 300)
    pdf = (k / A) * (x / A) ** (k - 1) * np.exp(-(x / A) ** k)
    ax.plot(x, pdf, color=PALETTE[1], label=f"Weibull A {A:.2f} m/s, k {k:.2f}")
    ax.set_xlabel("Wind speed, m/s")
    ax.set_ylabel("Probability density")
    ax.set_title(f"Wind speed distribution at {height:g} m", loc="left")
    ax.legend(loc="upper right")
    return _png(fig)


def chart_density(dens: dict) -> str | None:
    if "monthly" not in dens:
        return None
    m = dens["monthly"]
    fig, ax = plt.subplots(figsize=(8, 2.8))
    ax.plot(m.index.to_timestamp(), m.to_numpy(), color=PALETTE[2], marker="o", markersize=4)
    ax.set_ylabel("Air density, kg/m³")
    ax.set_title("Monthly mean air density" + (" (pressure estimated)" if dens.get("estimated") else ""), loc="left")
    return _png(fig)


def _segment_category(action: str) -> int:
    a = str(action)
    if a.startswith(("not analysed", "channel set", "no values")):
        return 2
    return 0 if a == "as logged" else 1


def chart_timeline(seg: pd.DataFrame, visits: list, order: list) -> str | None:
    if seg is None or seg.empty:
        return None
    d = seg[(seg["point"] != "–") & seg["from"].notna() & seg["to"].notna()].copy()
    if d.empty:
        return None
    pts = [p for p in order if p in set(d["point"])] + sorted(set(d["point"]) - set(order))
    d["cat"] = d["action"].map(_segment_category)
    t0, t1 = mdates.date2num(d["from"].min()), mdates.date2num(d["to"].max())
    span = max(t1 - t0, 1e-6)
    fig, ax = plt.subplots(figsize=(8.5, 0.36 * len(pts) + 1.5))
    for a, b, _n in visits:
        ax.axvspan(mdates.date2num(a), mdates.date2num(b), color="#f6e3b4", linewidth=0, zorder=0)
    colors = [PALETTE[0], PALETTE[1], "#b8b7b2"]
    for _, r in d.iterrows():
        y = pts.index(r["point"])
        x0, x1 = mdates.date2num(r["from"]), mdates.date2num(r["to"])
        w = max(x1 - x0, span * 0.004)
        ax.barh(y, w, left=x0, height=0.62, color=colors[r["cat"]], edgecolor=SURFACE, linewidth=1, zorder=2)
        if w > span * 0.07:
            ax.text(x0 + w / 2, y, f"Ch{r['channel']}", ha="center", va="center", fontsize=7,
                    color="white" if r["cat"] < 2 else INK, zorder=3)
    ax.set_yticks(range(len(pts)), pts)
    ax.invert_yaxis()
    ax.set_xlim(t0 - span * 0.01, t1 + span * 0.01)
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(maxticks=8))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    ax.grid(axis="y", visible=False)
    used = sorted(set(d["cat"]))
    names = ["latest configuration", "earlier configuration, merged by serial number", "not analysed"]
    handles = [Patch(color=colors[c], label=names[c]) for c in used]
    if visits:
        handles.append(Patch(color="#f6e3b4", label="site visit (maintenance)"))
    ax.legend(handles=handles, ncol=len(handles), loc="upper left", bbox_to_anchor=(0, -0.08), fontsize=8)
    ax.set_title("Which logger channel fed each measurement point, and when", loc="left")
    return _png(fig)


def findings(ctx: dict) -> list[str]:
    """Plain-language list of what needs checking, most important first."""
    ds, qcr, an = ctx["ds"], ctx["qc"], ctx["an"]
    out, pts = [], ds.points
    fs = ctx["flag_summary"].set_index("sensor") if not ctx["flag_summary"].empty else pd.DataFrame()
    maint = max((int(f["maintenance"].sum()) for f in qcr["flags"].values()), default=0)
    if ds.settings.get("changes"):
        src = ds.settings["values"].get("source", "")
        out.append("<b>Site settings file applied</b> (" + html.escape(Path(ds.settings["path"]).name) + "): "
                   + html.escape("; ".join(ds.settings["changes"])) + "."
                   + (f" Source: {html.escape(src)}." if src else " No source given: add one to the file."))
    chk = ds.site_checks
    if chk.get("spread_m", 0) > SAME_PLACE_M:
        out.append(f"<b>The logger coordinates changed during the campaign</b>, by up to {chk['spread_m']:,.0f} m: "
                   + html.escape("; ".join(f"{a:.6f}, {b:.6f} in {n:,} files" for a, b, n in chk["coordinates"][:4]))
                   + ". Check whether the mast moved or the coordinates were corrected.")
    if len(ds.conflict_times):
        inside = int(maintenance_mask(pd.DatetimeIndex(ds.conflict_times), pts.get("visits", [])).sum())
        out.append(f"<b>{len(ds.conflict_times):,} timestamps have conflicting records</b> from overlapping logger files "
                   f"({ds.conflict_rows:,} records; {inside:,} of the timestamps fall inside site visits). Each is a part-interval "
                   "record written when a file restarted; none is used (flag 'conflict'). See Quality flags.")
    for a, b, n in pts.get("visits", []):
        when = (f"{a + VISIT_PAD:%d %b %Y %H:%M}" if b - a <= 2 * VISIT_PAD else
                f"{a + VISIT_PAD:%d %b %Y %H:%M} to {b - VISIT_PAD:%d %b %Y %H:%M}")
        out.append(f"<b>Site visit, {when}:</b> {n} channel setting change{'s' if n != 1 else ''} in the logger. "
                   f"Records from one hour before to one hour after are flagged as maintenance and left out "
                   f"({maint:,} records across all visits).")
    if pts.get("moved"):
        out.append("<b>Sensors moved to other logger channels</b> and were followed by serial number: "
                   + html.escape(", ".join(pts["moved"])) + ". Confirm the mapping against the site visit report "
                   "(see Measurement points).")
    if pts.get("rescaled"):
        out.append(f"<b>{pts['rescaled']} earlier configurations</b> were recorded with a different calibration or vane "
                   "offset from the sensor's latest one; those records are re-expressed with the latest values. If a "
                   "later change was a physical re-alignment rather than a correction, reverse this.")
    for label, f in qcr["flags"].items():
        n = int(f["no_signal"].sum())
        if n:
            idx = f["no_signal"][f["no_signal"]].index
            out.append(f"<b>{html.escape(label)}: output at its zero value in {n:,} records</b> "
                       f"({idx[0]:%d %b %Y} to {idx[-1]:%d %b %Y}). The sensor was unpowered, disconnected or failed; "
                       "check its excitation setting and wiring.")
    silent = {label for label, f in qcr["flags"].items() if f["no_signal"].any()}
    if not fs.empty:
        for label, row in fs.iterrows():
            if row["valid_%"] < 80:
                counts = {k: int(row[k]) for k in FLAG_ORDER if k in row}
                top = max(counts, key=counts.get)
                if top == "no_signal" and label in silent:
                    continue  # already reported above
                out.append(f"<b>{html.escape(label)}: {row['valid_%']:.0f}% valid.</b> Main reason: "
                           f"{html.escape(FLAG_TEXT[top].lower())} ({100.0 * counts[top] / max(row['records'], 1):.0f}% of records).")
    rows = [w for pr in an.get("pairs", []) for w in pr.get("wake", [])]
    for w in rows:
        if not str(w["verdict"]).startswith("consistent"):
            out.append(f"<b>{html.escape(w['cup'])}</b>: {html.escape(w.get('note', w['verdict']))}.")
    diffs = [w["difference_deg"] for w in rows if np.isfinite(w.get("difference_deg", np.nan))]
    if len(diffs) >= 2 and all(abs(x) > 20 for x in diffs) and max(diffs) - min(diffs) <= 30:
        out.append(f"<b>Every mast wake found sits {np.mean(diffs):+.0f}° from where the logged boom bearings put it.</b> "
                   "One rotation shared by all booms points to the vane's north reference (or to the bearings entered in "
                   "the logger) rather than to the booms. Check the vane alignment against the installation record "
                   "before using directional results.")
    for pr in an.get("pairs", []):
        if pr.get("drift_months"):
            out.append(f"<b>Possible cup drift at {pr['height']:g} m:</b> monthly ratio {html.escape(pr['a'])} / "
                       f"{html.escape(pr['b'])} off its median month by more than 2% in "
                       + ", ".join(f"{m} ({v:+.1f}%)" for m, v in pr["drift_months"]) + ".")
    conv = ctx.get("conversion") or {}
    reasons = conv.get("failure_reasons") or {}
    if conv.get("failure_logs"):
        nf = len(conv["failure_logs"])
        out.append(f"<b>{nf} RLD file{'s' if nf != 1 else ''} could not be converted</b> by SymPRO Desktop: " +
                   "; ".join(f"{html.escape(n.replace('_meas.log', '.rld'))} ({html.escape(reasons.get(n, '') or 'no reason given')})"
                             for n in conv["failure_logs"]) + ".")
    tq = qcr["time"]
    for g0, g1, nrec in sorted(tq["gaps"], key=lambda g: -g[2]):
        days = nrec * tq["interval_s"] / 86400.0
        if days >= 3:
            out.append(f"<b>No data from {g0:%d %b %Y} to {g1:%d %b %Y}</b> ({days:.1f} days).")
    dens = an.get("density", {})
    if dens.get("estimated"):
        out.append("<b>Air density uses an estimated pressure</b> (standard atmosphere at the site elevation), because "
                   "the barometer has little or no valid data.")
    return out


# ---------- HTML ----------

def _fmt(v, nd=2):
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "–"
    if isinstance(v, (float, np.floating)):
        return f"{v:,.{nd}f}"
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d %H:%M")
    return html.escape(str(v))


def _table(df: pd.DataFrame, nd=2, index=False, max_rows=None) -> str:
    if df is None or df.empty:
        return "<p class='muted'>None.</p>"
    d = df.head(max_rows) if max_rows else df
    wide = {c for c in d.columns if d[c].dtype == object and d[c].astype(str).str.len().max() > 40}
    sty = lambda c: " class='wrap'" if c in wide else ""  # noqa: E731
    head = ("<th></th>" if index else "") + "".join(f"<th{sty(c)}>{html.escape(str(c))}</th>" for c in d.columns)
    cols = []  # format column by column so integer counts never pick up decimals
    for c in d.columns:
        col = d[c]
        if pd.api.types.is_integer_dtype(col):
            cols.append([f"{v:,}" for v in col])
        else:
            cols.append([_fmt(v, nd) for v in col])
    rows = []
    for i, idx in enumerate(d.index):
        cells = (f"<td>{_fmt(idx, nd)}</td>" if index else "") + "".join(
            f"<td{sty(name)}>{c[i]}</td>" for name, c in zip(d.columns, cols))
        rows.append(f"<tr>{cells}</tr>")
    more = f"<p class='muted'>Showing {len(d)} of {len(df)} rows; all rows are in the Excel file.</p>" if max_rows and len(df) > max_rows else ""
    return f"<div class='tw'><table><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table></div>{more}"


def _img(b64: str | None, alt: str) -> str:
    return f"<img alt='{html.escape(alt)}' src='data:image/png;base64,{b64}'>" if b64 else ""


CSS = """
body{font-family:Segoe UI,Arial,sans-serif;color:#0b0b0b;background:#f3f2ef;margin:0}
main{max-width:1040px;margin:0 auto;background:#fcfcfb;padding:28px 36px 48px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:30px 0 8px;padding-top:8px;border-top:1px solid #e4e3df}
p,li{font-size:13px;line-height:1.45}.muted{color:#52514e;font-size:12px}
.flag{background:#fff6dd;border-left:4px solid #eda100;padding:10px 14px;font-size:12.5px;margin:14px 0}
.find{border:1px solid #e4e3df;border-left:4px solid #eb6834;padding:6px 14px 6px 4px;margin:14px 0}
.find li{margin:4px 0}
.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:16px 0}
.card{border:1px solid #e4e3df;border-radius:6px;padding:10px 12px}.card b{display:block;font-size:20px}
.card span{font-size:11.5px;color:#52514e}
.tw{overflow-x:auto}table{border-collapse:collapse;font-size:12px;margin:8px 0;width:100%}
th,td{border-bottom:1px solid #e4e3df;padding:4px 8px;text-align:left;white-space:nowrap}
th{background:#f3f2ef;font-weight:600}img{max-width:100%;margin:6px 0}
td.wrap,th.wrap{white-space:normal;min-width:260px}
"""


def shear_test_html(st: pd.DataFrame) -> str:
    if st is None or st.empty:
        return ("<p class='muted'>Shear model test: needs at least 100 concurrent records at 3 m/s or more in both "
                "the first 80% and the last 20% of the record.</p>")
    r = st.sort_values("from_m").iloc[-1]  # nearest lower height: the closest analogue to extrapolating upward
    txt = (f"<p><b>Shear model test.</b> The exponent fitted on the first 80% of concurrent records "
           f"({r['trained_from']:%d %b %Y} to {r['trained_to']:%d %b %Y}) predicts the {r['to_m']:g} m speed from "
           f"{r['from_m']:g} m over the last 20% ({r['tested_from']:%d %b %Y} to {r['tested_to']:%d %b %Y}), which it "
           f"has not seen: mean absolute error {r['MAE_mps']:.3f} m/s against "
           f"{r['baseline_MAE_mps']:.3f} m/s with no shear, and an error in the mean of {r['mean_error_%']:+.2f}% against "
           f"{r['baseline_mean_error_%']:+.2f}%. The exponent was {r['alpha_train']:.3f} in the first period and "
           f"{r['alpha_test']:.3f} in the last.")
    if np.isfinite(r.get("alpha_all_heights", np.nan)):
        txt += (f" One exponent fitted across all heights ({r['alpha_all_heights']:.3f}) gives an error in the mean of "
                f"{r['all_heights_mean_error_%']:+.2f}% for the same prediction"
                + ("; the exponent of the top two heights is the better guide for carrying the wind upward."
                   if abs(r["all_heights_mean_error_%"]) > abs(r["mean_error_%"]) + 0.25 else "."))
    txt += (" An error in the mean of this size is a guide to the error in carrying the top-height wind upward "
            "with the same law; the two periods cover different seasons.</p>")
    tab = pd.DataFrame({
        "heights, m": [f"{a:g} → {b:g}" for a, b in zip(st["from_m"], st["to_m"])],
        "records, fit / test": [f"{a:,} / {b:,}" for a, b in zip(st["train_records"], st["test_records"])],
        "exponent, fit / test": [f"{a:.3f} / {b:.3f}" for a, b in zip(st["alpha_train"], st["alpha_test"])],
        "MAE, m/s": st["MAE_mps"], "MAE no shear, m/s": st["baseline_MAE_mps"],
        "error in mean, %": st["mean_error_%"], "no shear, %": st["baseline_mean_error_%"],
        "all-heights exponent, %": st["all_heights_mean_error_%"]})
    return txt + _table(tab, nd=3) + "<p class='muted'>RMSE and bias for each pair are in summary.xlsx (Shear test).</p>"


def write_html(path: Path, ctx: dict) -> None:
    ds, qcr, an, sensors = ctx["ds"], ctx["qc"], ctx["an"], ctx["sensors"]
    tq = qcr["time"]
    hrs = {k: v * tq["interval_s"] / 3600.0 for k, v in STUCK_RECORDS.items()}
    p0 = site_pressure(ds.site)
    ctx["range_text"] = ("cups 0–75 m/s, vanes 0–360°, temperature −40 to 60 °C, humidity 0–105 %, pressure "
                         + (f"{p0 - PRESSURE_BAND_HPA:.0f}–{p0 + PRESSURE_BAND_HPA:.0f} hPa (standard atmosphere at "
                            f"{ds.site.get('Elevation')} m ± {PRESSURE_BAND_HPA:g} hPa)" if p0 else "500–1100 hPa"))
    ctx["no_signal_text"] = ", ".join(f"{k} below {v:g} V" for k, v in NO_SIGNAL_V.items())
    ctx["stuck_text"] = (f"{hrs['wind_speed']:g} h on cups and vanes while the other cups read 3 m/s or more; "
                         f"{hrs['temperature']:g} h temperature, {hrs['humidity']:g} h humidity (below 99.5 %), "
                         f"{hrs['pressure']:g} h pressure")
    site = ds.site
    name = ctx["site_name"]
    hs = an.get("height_stats", pd.DataFrame())
    top = an.get("top_height")
    sh = an.get("shear", {})
    ti = an.get("ti", pd.DataFrame())
    ti15 = ti.loc[15, "rep_TI"] if (not ti.empty and 15 in ti.index and ti.loc[15, "count"] >= 10) else np.nan
    dens = an.get("density", {})
    top_row = hs.iloc[0] if len(hs) else None

    cards = [
        (f"{tq['start']:%d %b %Y} – {tq['end']:%d %b %Y}", "Period covered"),
        (f"{100.0 * tq['present'] / max(tq['expected'], 1):.1f}%", f"Timestamps present ({tq['interval_s'] // 60} min records)"),
        (_fmt(top_row["mean_ws"]) + " m/s" if top_row is not None else "–", f"Mean wind speed at {top:g} m" if top else "Mean wind speed"),
        (f"{top_row['recovery_%']:.1f}%" if top_row is not None else "–", "Valid data at top height"),
        (_fmt(sh.get("alpha_fit"), 3) if sh else "–", "Shear exponent (power law)"),
        (_fmt(ti15, 3), "Representative TI at 15 m/s"),
        (_fmt(dens.get("mean"), 3) + " kg/m³" if dens.get("mean") else "–", "Mean air density"),
        (f"{len(ds.files)}", "Export files read"),
    ]
    card_html = "".join(f"<div class='card'><b>{v}</b><span>{html.escape(t)}</span></div>" for v, t in cards)

    logged = ds.site_logged or site
    def _used(key):  # value used, and the logged one when a site settings file replaced it
        v, w = str(site.get(key, "")), str(logged.get(key, ""))
        return v if v == w else f"{v} (site settings; logger files: {w or 'none'})"
    chk = ds.site_checks
    if chk.get("coordinates"):
        n1 = chk["coordinates"][0][2]
        agree = ((f"all {n1:,} files with coordinates agree" if n1 > 1 else "1 file with coordinates")
                 if len(chk["coordinates"]) == 1 else
                 f"{len(chk['coordinates'])} positions, up to {chk['spread_m']:,.0f} m apart")
        if chk.get("files_without_coordinates"):
            agree += f"; {chk['files_without_coordinates']:,} files give none"
    else:
        agree = "no coordinates in the files"
    site_rows = pd.DataFrame([
        ("Site number", site.get("Site Number", "")), ("Description", site.get("Site Description", "")),
        ("Project", site.get("Project", "")), ("Location", site.get("Location", "")),
        ("Latitude, longitude", f"{_used('Latitude')}, {_used('Longitude')}"),
        ("Coordinates across files", agree),
        ("Elevation, m", _used("Elevation")),
        ("Time zone (logger)", ", ".join(f"{z} ({n:,} files)" for z, n in chk.get("time_zones", [])) or site.get("Time Zone", "")),
        ("Logger", "; ".join(f"{lg.get('Model', '')} s/n {lg.get('Serial Number', '')} fw {lg.get('Firmware', '')}" for lg in ds.loggers)),
        ("iPack", "; ".join(f"{ip.get('Model', '')} s/n {ip.get('Serial Number', '')}" for ip in ds.ipacks)),
        ("Export format", ds.source_format),
    ], columns=["Item", "Value"])

    gaps = pd.DataFrame(tq["gaps"], columns=["gap_start", "gap_end", "missing_records"])
    gaps = gaps.sort_values("missing_records", ascending=False)
    gaps["missing_days"] = gaps["missing_records"] * tq["interval_s"] / 86400.0

    pair_html = []
    for pr in an.get("pairs", []):
        txt = [f"Median ratio {pr['a']} / {pr['b']}: <b>{_fmt(pr['median_ratio'], 3)}</b> over {pr['n']:,} records above 4 m/s."]
        if "shadow_a_deg" in pr:
            txt.append(f"Lowest ratio at {pr['shadow_a_deg']:.0f}° ({_fmt(pr['shadow_a_ratio'], 3)}): {html.escape(pr['a'])} in the mast wake. "
                       f"Highest at {pr['shadow_b_deg']:.0f}° ({_fmt(pr['shadow_b_ratio'], 3)}): {html.escape(pr['b'])} in the wake.")
        if pr.get("drift_months"):
            txt.append("<b>Possible drift or damage:</b> monthly ratio off its median month by more than 2% in " +
                       ", ".join(f"{m} ({v:+.1f}%)" for m, v in pr["drift_months"]) +
                       f". A plus sign means {html.escape(pr['a'])} reads higher, or {html.escape(pr['b'])} lower, than in the "
                       "other months: the ratio cannot say which. Check calibrations and site visit notes.")
        elif "monthly" in pr:
            txt.append("Monthly ratio stays within 2% of its median month: no sign of cup drift.")
        wake = pd.DataFrame(pr.get("wake", [])).drop(columns=["note"], errors="ignore")
        pair_html.append("<p>" + " ".join(txt) + "</p>" + _img(chart_pair(pr), f"pair ratio {pr['height']} m") +
                         ("<p class='muted'>Mast wake against the logged boom bearings:</p>" + _table(wake, nd=1) if not wake.empty else ""))

    pts_seg = ds.points.get("segments", pd.DataFrame())
    visits = ds.points.get("visits", [])
    find = findings(ctx)
    shear_tab = pd.DataFrame()
    if sh:
        shear_tab = pd.DataFrame({"height_m": sh["heights"], "mean_ws_concurrent": sh["means"].to_numpy()})
    rose_tab = an.get("rose")
    flags_sum = ctx["flag_summary"]
    flags_top = ctx["flag_periods"]

    parts = [f"<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'><title>{html.escape(name)} – met mast data screening</title><style>{CSS}</style></head><body><main>",
             f"<h1>{html.escape(name)}: met mast data screening</h1>",
             f"<p class='muted'>{html.escape(ORG)} · {APP_NAME} v{__version__} · generated {ctx['generated']}</p>",
             f"<div class='flag'><b>[COMPLIANCE]</b> {html.escape(DISCLAIMER)}</div>",
             f"<div class='cards'>{card_html}</div>",
             ("<h2>Findings to check</h2><div class='find'><ul>" + "".join(f"<li>{x}</li>" for x in find) + "</ul></div>"
              if find else ""),
             "<h2>Site and logger</h2>", _table(site_rows),
             "<h2>Data recovery</h2>",
             "<p>Valid records as a share of the records the logger should have written each month. A record counts as valid when it is present and passes every check below.</p>",
             _img(chart_recovery(ctx["recovery"]), "recovery heatmap"),
             _table(an["sensor_table"], nd=2),
             "<h2>Quality flags</h2>",
             "<p>" + " ".join(f"<b>{k}</b>: {html.escape(v)}." for k, v in FLAG_TEXT.items()) + "</p>",
             _table(flags_sum, nd=1),
             "<p class='muted'>Longest flagged periods:</p>", _table(flags_top, max_rows=40),
             f"<p>Timestamps: {tq['present']:,} of {tq['expected']:,} expected records present; {len(tq['gaps'])} gaps; "
             f"{ds.duplicates_removed:,} repeated records (identical copies in overlapping files) merged; "
             f"{len(ds.conflict_times):,} timestamps where overlapping files disagree ({ds.conflict_rows:,} records) quarantined as 'conflict'; "
             f"{tq['off_grid']:,} records off the {tq['interval_s'] // 60}-minute grid.</p>",
             _table(gaps.head(25)),
             ("<p class='muted'>Conflicting records (largest difference shown):</p>" + _table(ds.conflicts, nd=3, max_rows=30)
              if len(ds.conflicts) else ""),
             "<h2>Wind speed</h2>",
             "<p>Where two cups share a height, each record uses the cup that is upwind of the mast for that direction sector, so mast shadow is removed. MoMM is the mean of the 12 calendar-month means and needs every calendar month present.</p>",
             _table(hs, nd=2),
             _img(chart_lines(an.get("monthly_ws", pd.DataFrame()), "Monthly mean wind speed by height", "m/s"), "monthly wind speed"),
             _img(chart_lines(an.get("daily_ws", pd.DataFrame()), "Daily mean wind speed by height", "m/s"), "daily wind speed"),
             _img(chart_lines(an.get("diurnal_ws", pd.DataFrame()), "Mean wind speed by hour of day", "m/s", "Hour of day (logger time)"), "diurnal wind speed"),
             "<h2>Paired cups and mast shadow</h2>", "".join(pair_html) or "<p class='muted'>No height has two anemometers.</p>",
             "<h2>Wind shear</h2>",
             (f"<p>Power-law exponent {sh['alpha_fit']:.3f} fitted across {len(sh['heights'])} heights; {sh['lo']:g}–{sh['hi']:g} m pair {sh['alpha_top_bottom']:.3f}; "
              + (f"top pair {sh['top_pair'][0]:g}–{sh['top_pair'][1]:g} m {sh['alpha_top_pair']:.3f}; " if "top_pair" in sh else "")
              + f"{sh['n']:,} concurrent records with all heights at 3 m/s or more.</p>" if sh else "<p class='muted'>Needs anemometers at two or more heights.</p>"),
             _img(chart_shear(sh), "shear"), _table(shear_tab, nd=2),
             shear_test_html(an.get("shear_test", pd.DataFrame())),
             "<h2>Turbulence intensity</h2>",
             _img(chart_ti(ti, top) if top else None, "turbulence"),
             _table(ti.reset_index()[["ws_bin_mps", "count", "mean_TI", "rep_TI", "IEC_A", "IEC_B", "IEC_C"]] if not ti.empty else ti, nd=3),
             "<h2>Wind direction</h2>",
             (_img(chart_rose(rose_tab, f"Direction frequency, % ({an.get('rose_vane', '')})"), "wind rose") +
              _table(rose_tab.reset_index(), nd=1)) if rose_tab is not None else "<p class='muted'>No valid wind vane.</p>",
             "<h2>Wind speed distribution</h2>",
             _img(chart_weibull(an.get("weibull_sample", pd.Series(dtype=float)), top_row["weibull_A"], top_row["weibull_k"], top) if top_row is not None else None, "weibull"),
             "<h2>Temperature and air density</h2>",
             (f"<p>{html.escape(dens.get('note', ''))}. Mean air density {dens['mean']:.3f} kg/m³ at the sensor height; "
              f"wind power density at {top:g} m about {an.get('wpd_top', float('nan')):,.0f} W/m².</p>" if dens.get("mean") else f"<p class='muted'>{html.escape(dens.get('note', 'No temperature sensor found.'))}</p>"),
             _img(chart_density(dens), "air density"),
             (f"<p class='muted'>Monthly air temperature, °C, at {dens.get('sensor_height') or 0:g} m:</p>" +
              _table(dens["t_monthly"].rename_axis("month").reset_index().astype({"month": str}), nd=1)) if "t_monthly" in dens else "",
             "<h2>Measurement points and configuration changes</h2>",
             "<p>Every logger file carries the channel settings in force when it was written. Each sensor is followed by "
             "its serial number, so a sensor re-wired to another channel or re-labelled keeps one continuous record. Its "
             "position is the height and boom of its latest configuration. Records logged under an earlier calibration "
             "or vane offset are re-expressed with the sensor's latest values. Check every row that is not "
             "'as logged' against the site visit report.</p>",
             _img(chart_timeline(pts_seg, visits, [s.label for s in sensors]), "configuration timeline"),
             (_table(pd.DataFrame([(f"{a + VISIT_PAD:%Y-%m-%d %H:%M}", f"{b - VISIT_PAD:%Y-%m-%d %H:%M}", n,
                                    f"{a:%Y-%m-%d %H:%M}", f"{b:%Y-%m-%d %H:%M}") for a, b, n in visits],
                                  columns=["first change", "last change", "changes", "maintenance from", "maintenance to"]))
              if visits else "<p class='muted'>No configuration changes inside the record.</p>"),
             _table(pts_seg[pts_seg["records"] > 0] if not pts_seg.empty else pts_seg, nd=4),
             "<p class='muted'>Configurations that recorded no values are listed in the Excel file only.</p>",

             "<h2>Method and traceability</h2>",
             "<ul>"
             "<li>Input: NRG SymphoniePRO measurement exports (RLD files converted with NRG SymphoniePRO Desktop). "
             f"Every file in the data folder and every converted export read is fingerprinted with SHA-256 in inputs.csv; "
             f"input set fingerprint {html.escape(str(ctx.get('inputs', {}).get('inventory_sha256', ''))[:16])}.</li>"
             "<li>One mast and one clock: the run stops if the files carry different site numbers, coordinates more than "
             "1 km apart or different logger time zones. Overlapping files: identical copies of a record are merged; where "
             "copies disagree the timestamp is kept with no values and flagged 'conflict'.</li>"
             f"<li>Checks: missing values; maintenance (one hour either side of a cluster of logger configuration changes); sensor output at its zero value ({ctx['no_signal_text']}); plausible range ({ctx['range_text']}); Min/Max/SD consistency; flat signal for {ctx['stuck_text']}; jumps of more than 8 m/s, 8 °C or 10 hPa from the 5-record median. Flagged periods less than 24 h apart are listed as one period.</li>"
             "<li>Measurement points: each sensor is followed by serial number across logger files; its position is its latest logged height and boom; records logged under an earlier slope and offset (or vane direction offset) are re-expressed with its latest values. Sensors with no serial number keep the position in their column names. A position with under a day of data is listed but not analysed.</li>"
             "<li>Wind directions use each vane's latest direction offset. Mast-wake check: a cup on a boom at bearing b should read low when the wind blows from b + 180°; the observed wake centre is the deficit-weighted mean direction around the deepest dip of the paired-cup ratio (sectors with 30 records or more, dip of 3% or more). Times are logger time.</li>"
             "<li>Shear model test: chronological 80/20 split of concurrent valid records (both heights at 3 m/s or more); "
             "the exponent from the first part predicts the top-height speed in the second; baseline: no shear.</li>"
             "<li>Turbulence reference curves: IEC 61400-1 normal turbulence model, I_ref 0.18/0.16/0.14/0.12 for classes A+/A/B/C. Air density from measured temperature, pressure and humidity where available.</li>"
             f"<li>Software: {APP_NAME} v{__version__}; pandas {pd.__version__}; numpy {np.__version__}; matplotlib {matplotlib.__version__}.</li>"
             f"<li>Files read: {len(ds.files)}; unreadable: {len(ds.read_errors)}.</li></ul>",
             _table(pd.DataFrame({"unreadable file": ds.read_errors}), max_rows=20) if ds.read_errors else "",
             f"<div class='flag'><b>[COMPLIANCE]</b> {html.escape(DISCLAIMER)} Client data: use only for the assignment it was provided for.</div>",
             "</main></body></html>"]
    path.write_text("".join(parts), encoding="utf-8")


def write_excel(path: Path, ctx: dict) -> None:
    an, qcr = ctx["an"], ctx["qc"]
    tq = qcr["time"]
    sheets = {
        "Read me": pd.DataFrame({"Item": ["Site", "Generated", "Input files", "Input set fingerprint (SHA-256)",
                                          "Site settings", "Disclaimer"],
                                 "Value": [ctx["site_name"], ctx["generated"], ctx.get("inputs", {}).get("files", ""),
                                           ctx.get("inputs", {}).get("inventory_sha256", ""),
                                           "; ".join(ctx["ds"].settings.get("changes", [])) or "none", DISCLAIMER]}),
        "Sensors": an["sensor_table"],
        "Heights": an.get("height_stats", pd.DataFrame()),
        "Recovery % by month": ctx["recovery"].reset_index().rename(columns={"index": "sensor"}),
        "Flag summary": ctx["flag_summary"],
        "Flag periods": ctx["flag_periods"],
        "Gaps": pd.DataFrame(tq["gaps"], columns=["gap_start", "gap_end", "missing_records"]),
        "Monthly WS": an["monthly_ws"].rename(index=str).reset_index() if "monthly_ws" in an else pd.DataFrame(),
        "TI bins": an["ti"].reset_index() if "ti" in an else pd.DataFrame(),
        "Wind rose": an["rose"].reset_index() if an.get("rose") is not None else pd.DataFrame(),
        "Points & configurations": ctx["ds"].points.get("segments", pd.DataFrame()),
        "Site visits": pd.DataFrame([(a, b, n) for a, b, n in ctx["ds"].points.get("visits", [])],
                                    columns=["maintenance_from", "maintenance_to", "changes"]),
        "Channel history": ctx["changes"],
    }
    wake = [w for pr in an.get("pairs", []) for w in pr.get("wake", [])]
    if wake:
        sheets["Mast wake check"] = pd.DataFrame(wake)
    sheets["Findings"] = pd.DataFrame({"finding": [html.unescape(re.sub("<[^>]+>", "", x)) for x in findings(ctx)]})
    if len(ctx["ds"].conflicts):
        sheets["Conflicting records"] = ctx["ds"].conflicts
    st = an.get("shear_test", pd.DataFrame())
    if st is not None and not st.empty:
        sheets["Shear test"] = st
    sh = an.get("shear", {})
    if sh:
        sheets["Shear by hour"] = sh["by_hour"].rename("alpha").reset_index().rename(columns={"index": "hour"})
    for pr in an.get("pairs", []):
        if pr.get("sectors") is not None:
            sheets[f"Pair {pr['height']:g} m"[:31]] = pr["sectors"].reset_index()
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        for name, df in sheets.items():
            df = df.copy()
            for c in df.columns:
                if pd.api.types.is_datetime64_any_dtype(df[c]):
                    df[c] = df[c].dt.tz_localize(None) if getattr(df[c].dt, "tz", None) else df[c]
            df.to_excel(xw, sheet_name=name[:31], index=False)


def write_timeseries(path: Path, ctx: dict) -> None:
    ds, qcr, an = ctx["ds"], ctx["qc"], ctx["an"]
    out = ds.data.copy()
    for label, v in qcr["valid"].items():
        out[f"valid | {label}"] = v.astype(int)
    for h, d in an.get("heights", {}).items():
        out[f"WS_{h:g}m_combined"] = d["ws"]
    out.to_csv(path, float_format="%.4f")


def write_meta(path: Path, ctx: dict) -> None:
    ds, tq = ctx["ds"], ctx["qc"]["time"]
    meta = {"app": APP_NAME, "version": __version__, "generated": ctx["generated"], "site": ctx["site_name"],
            "input_folder": str(ctx["input"]), "files_read": len(ds.files), "unreadable": ds.read_errors,
            "period": [str(tq["start"]), str(tq["end"])], "interval_s": tq["interval_s"],
            "conversion": ctx.get("conversion"), "inputs": ctx.get("inputs"),
            "site_checks": ds.site_checks, "site_settings": ds.settings or None,
            "repeated_records_merged": ds.duplicates_removed,
            "conflicts": {"timestamps": len(ds.conflict_times), "records": ds.conflict_rows},
            "disclaimer": DISCLAIMER}
    path.write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
