"""Report page for Campbell TOA5 and Nomad2 runs, built from the files the run wrote (stats.json, findings.txt,
climate.json, figures). SymphoniePRO runs write their own fuller report."""
from __future__ import annotations

import html
import json
from pathlib import Path

from .. import APP_NAME, DISCLAIMER, ORG, __version__
from ..report import CSS

FIGURE_TITLES = [("01_recovery", "Valid data by month and sensor"), ("02_monthly_ws", "Monthly mean wind speed"),
                 ("03_diurnal_ws", "Wind speed by hour of day"), ("04_pair", "Paired anemometers"),
                 ("05_shear", "Wind shear"), ("06_ti", "Turbulence intensity"), ("07_rose", "Wind rose"),
                 ("08_weibull", "Wind speed distribution"), ("09_density", "Air density")]


def _f(v, nd=2, unit=""):
    try:
        return f"{float(v):.{nd}f}{unit}"
    except (TypeError, ValueError):
        return "–"


def write_report(run_dir) -> Path:
    run_dir = Path(run_dir)
    stats = json.loads((run_dir / "stats.json").read_text(encoding="utf-8")) if (run_dir / "stats.json").is_file() else {}
    clim = json.loads((run_dir / "climate.json").read_text(encoding="utf-8")) if (run_dir / "climate.json").is_file() else {}
    findings = stats.get("findings") or []
    name = stats.get("site") or (clim.get("site") or {}).get("name") or run_dir.name
    top = clim.get("height_m")
    cards = [
        (html.escape(str(stats.get("period") or " – ".join(str(x)[:10] for x in clim.get("period", [])))), "Period covered"),
        (f"{clim.get('records', 0):,}", "Valid records at the top height"),
        (_f(clim.get("recovery_pct"), 1, "%"), "Valid data at the top height"),
        (_f(clim.get("mean_ws"), 2, " m/s"), f"Mean wind speed at {top:g} m" if top else "Mean wind speed"),
        (_f((clim.get("shear") or {}).get("alpha"), 3), "Shear exponent (power law)"),
        (_f((clim.get("ti") or {}).get("representative_15"), 3), "Representative TI at 15 m/s"),
        (_f((clim.get("density") or {}).get("mean"), 3, " kg/m³"), "Mean air density"),
        (html.escape(str(clim.get("source_format") or stats.get("reader") or "")), "Logger format"),
    ]
    card_html = "".join(f"<div class='card'><b>{v}</b><span>{html.escape(t)}</span></div>" for v, t in cards)
    rows = "".join(
        f"<tr><td>{h['height_m']:g}</td><td>{_f(h.get('mean_ws'))}</td><td>{_f(h.get('momm_ws'))}</td>"
        f"<td>{_f(h.get('recovery_pct'), 1)}</td><td>{_f(h.get('weibull_A'))}</td><td>{_f(h.get('weibull_k'))}</td></tr>"
        for h in clim.get("heights", []))
    figs = []
    fig_dir = run_dir / "figures"
    if fig_dir.is_dir():
        for p in sorted(fig_dir.glob("*.png")):
            title = next((t for key, t in FIGURE_TITLES if p.name.startswith(key)), p.stem)
            figs.append(f"<h2>{html.escape(title)}</h2><img alt='{html.escape(title)}' src='figures/{html.escape(p.name)}'>")
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{html.escape(name)} – {APP_NAME}</title>
<style>{CSS}.card b{{font-size:17px}}</style></head><body><main>
<h1>{html.escape(name)}: met mast data screening</h1>
<p class="muted">{APP_NAME} v{__version__}, {ORG}. Run {html.escape(run_dir.name)}.</p>
<div class="flag"><b>[COMPLIANCE]</b> {html.escape(DISCLAIMER)}</div>
<div class="cards">{card_html}</div>
<h2>Findings</h2>
<div class="find"><ol>{''.join(f'<li>{html.escape(str(x))}</li>' for x in findings) or '<li>No findings recorded.</li>'}</ol></div>
<h2>Wind speed by height</h2>
<div class="tw"><table><tr><th>Height, m</th><th>Mean of valid records, m/s</th><th>Mean of monthly means, m/s</th>
<th>Valid data, %</th><th>Weibull A, m/s</th><th>Weibull k</th></tr>{rows}</table></div>
{''.join(figs)}
<h2>Files of this run</h2>
<p>summary.xlsx (all tables), timeseries_by_height.csv (cleaned series with validity flags), inputs.csv (SHA-256 of
every input file), run.log, stats.json, climate.json (hand-off to the long-term, map and layout steps).</p>
</main></body></html>"""
    out = run_dir / "report.html"
    out.write_text(page, encoding="utf-8")
    return out
