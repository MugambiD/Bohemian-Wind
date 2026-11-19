/* Bohemian Wind: the interface. Plain JavaScript, no libraries.
   One step per screen: what to give it on the left, what it found on the right. */
(() => {
  'use strict';

  const TOKEN = document.querySelector('meta[name="bw-token"]').content;
  const PAGE = (window.crypto && crypto.randomUUID) ? crypto.randomUUID() : String(Math.random()).slice(2) + Date.now();   // this window
  const $ = (s, el = document) => el.querySelector(s);
  const main = $('#main'), rail = $('#rail'), tip = $('#tip');

  let S = null;            // state from the application
  let view = 'home';       // 'home' or a step key
  let job = null;          // the run in progress
  let logLines = [];
  let lastError = null;    // {step, message, log}

  const STEP = {
    data: { n: 1, title: 'Mast data', verb: 'Check the mast data', short: 'Quality checks and statistics', photo: 'mast.jpg',
      why: 'Reads the logger files, flags the records that cannot be trusted and describes the wind that was measured.',
      empty: ['No data checked yet', 'Choose the folder that holds the logger files, then check the mast data.'] },
    longterm: { n: 2, title: 'Long-term correction', verb: 'Correct to the long term', short: 'Scale to 20 years', photo: 'land.jpg',
      why: 'Sets the measured months against 20 years of reference data, so a windy or a calm campaign is put in proportion.',
      empty: ['Not corrected yet', 'Without this step the measured period is used as it is.'] },
    map: { n: 3, title: 'Wind map', verb: 'Draw the wind map', short: 'Wind across the site', photo: 'hero.jpg',
      why: 'Carries the wind from the mast across the terrain around it.',
      empty: ['No wind map yet', 'The terrain is downloaded once for the area around the mast, then the wind is computed over it.'] },
    layout: { n: 4, title: 'Layout and energy', verb: 'Place turbines and estimate energy', short: 'Constraints, turbines, P50 and P90', photo: 'hero.jpg',
      why: 'Applies your exclusion zones and buffers, places turbines where the wind is best, and estimates wakes, losses and yearly energy.',
      empty: ['No layout yet', 'Add your exclusion zones and homes if you have them, choose a turbine size and a number, then place the turbines.'] },
    environment: { n: 5, title: 'Noise and shadow flicker', verb: 'Check noise and shadow flicker', short: 'Screening at homes', photo: 'land.jpg',
      why: 'Screens every home for turbine noise and for worst-case shadow flicker, against the limits you set.',
      empty: ['Not screened yet', 'This step needs a layout and the homes round the site: get the buildings from open data in step 4, or give a file of your own.'] },
    pack: { n: 6, title: 'Permitting pack', verb: 'Build the permitting pack', short: 'Tables, maps and GIS files', photo: 'mast.jpg',
      why: 'Collects coordinates, maps and tables into one folder to start the conversation with authorities and consultants.',
      empty: ['No pack built yet', 'The pack holds turbine coordinates with tip heights, GIS files, the constraint record, the screening tables and a summary page.'] },
  };
  const ORDER = ['data', 'longterm', 'map', 'layout', 'environment', 'pack'];
  // fetching the buildings for step 4 runs like a step and shows on that step's page, but is not one of the six
  STEP.buildings = { n: 4, title: 'Buildings from open data', verb: 'Get buildings', short: '', photo: 'hero.jpg', why: '', empty: ['', ''] };
  const pageOf = k => k === 'buildings' ? 'layout' : k;

  const ICON = {
    check: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="8" cy="8" r="6.5"/><path d="M5 8.2l2 2 4-4.2"/></svg>',
    warn: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 2l6.5 11.5h-13z"/><path d="M8 6.5v3.2M8 11.6v.1"/></svg>',
    cross: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="8" cy="8" r="6.5"/><path d="M5.7 5.7l4.6 4.6M10.3 5.7l-4.6 4.6"/></svg>',
    ring: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="8" cy="8" r="6.5"/></svg>',
    cups: '<svg class="cups" viewBox="0 0 32 32" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="16" cy="16" r="2.2" fill="currentColor" stroke="none"/><path d="M16 16V6.5M16 16l8.2 4.8M16 16l-8.2 4.8"/><circle cx="16" cy="4.6" r="2.8"/><circle cx="26.2" cy="22" r="2.8"/><circle cx="5.8" cy="22" r="2.8"/></svg>',
  };

  // ---------- small helpers ----------
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const ok = v => v !== null && v !== undefined && v !== '' && Number.isFinite(Number(v));
  const num = (v, d = 1) => ok(v) ? Number(v).toLocaleString('en-GB', { minimumFractionDigits: d, maximumFractionDigits: d }) : '–';
  const get = (o, path) => path.split('.').reduce((a, k) => (a == null ? a : a[k]), o);
  const setPath = (o, path, v) => { const ks = path.split('.'); let a = o; ks.slice(0, -1).forEach(k => { a = a[k] = a[k] || {}; }); a[ks[ks.length - 1]] = v; return o; };
  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const day = s => { const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(s || '')); return m ? `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]} ${m[1]}` : '–'; };
  const when = s => { const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}:\d{2})/.exec(String(s || '')); return m ? `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]}, ${m[4]}` : ''; };
  const monthName = s => { const m = /^(\d{4})-(\d{2})/.exec(String(s)); return m ? `${MONTHS[Number(m[2]) - 1]} ${m[1]}` : String(s); };
  const fileUrl = (rel, v) => '/files/' + TOKEN + '/' + String(rel).split('/').map(encodeURIComponent).join('/') + (v ? '?v=' + encodeURIComponent(v) : '');
  const stepOf = key => S.project.steps.find(s => s.key === key);
  const compass = deg => ['N', 'NNE', 'ENE', 'E', 'ESE', 'SSE', 'S', 'SSW', 'WSW', 'W', 'WNW', 'NNW'][Math.round(((deg % 360) + 360) % 360 / 30) % 12];

  async function api(path, body) {
    const opt = { headers: { 'X-BW-Token': TOKEN, 'X-BW-Page': PAGE } };
    if (body !== undefined) { opt.method = 'POST'; opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
    let r;
    try { r = await fetch(path, opt); } catch (e) { throw new Error('Bohemian Wind is not running. Start it again, then reload this page.'); }
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || `The request failed (${r.status}).`);
    return data;
  }

  function toast(msg, bad) {
    const d = document.createElement('div');
    if (bad) d.className = 'bad';
    d.textContent = msg;
    $('#toast').appendChild(d);
    setTimeout(() => d.remove(), bad ? 11000 : 5500);
  }

  // ---------- building blocks ----------
  const tile = (k, v, s, lead) => `<div class="tile${lead ? ' lead' : ''}"><div class="k">${k}</div><div class="v">${v}</div>${s ? `<div class="s">${s}</div>` : ''}</div>`;
  const card = (title, body, cls = '') => `<section class="card ${cls}">${title ? `<h2>${title}</h2>` : ''}${body}</section>`;
  const list = (items, cls = '') => `<ul class="plain ${cls}">${items.map(x => `<li>${esc(x)}</li>`).join('')}</ul>`;
  const state = (kind, text) => `<span class="state ${kind}">${kind === 'ok' ? ICON.check : kind === 'over' ? ICON.cross : ICON.warn}${esc(text)}</span>`;

  function table(head, rows, numeric = []) {
    const th = head.map((h, i) => `<th${numeric.includes(i) ? ' class="num"' : ''}>${h}</th>`).join('');
    const tr = rows.map(r => '<tr>' + r.map((c, i) => `<td${numeric.includes(i) ? ' class="num"' : ''}>${c}</td>`).join('') + '</tr>').join('');
    return `<div class="tw"><table><thead><tr>${th}</tr></thead><tbody>${tr}</tbody></table></div>`;
  }

  function pill(st) {
    const r = st.result;
    if (job && job.status === 'running' && job.current === st.key) return `<span class="pill">${ICON.ring}Working</span>`;
    if (st.status === 'done') return `<span class="pill done">${ICON.check}Done ${esc(when(r.at))}</span>`;
    if (st.status === 'stale') return `<span class="pill stale">${ICON.warn}Out of date</span>`;
    if (st.status === 'blocked') return `<span class="pill">${ICON.ring}Waiting for ${esc(st.needs.join(', ').toLowerCase())}</span>`;
    return `<span class="pill">${ICON.ring}Not run yet</span>`;
  }

  let uid = 0;
  function fNum(section, key, label, o = {}) {
    const v = get(S.project[section], key), id = 'f' + (++uid);
    return `<div class="field"><label for="${id}">${label}</label><input id="${id}" type="number" inputmode="decimal" step="${o.step || 'any'}"${o.min != null ? ` min="${o.min}"` : ''}${o.max != null ? ` max="${o.max}"` : ''} value="${ok(v) ? v : ''}" data-s="${section}" data-k="${key}" data-t="num"${o.placeholder ? ` placeholder="${esc(o.placeholder)}"` : ''}${o.disabled ? ' disabled' : ''}${o.rerender ? ' data-rerender="1"' : ''}>${o.help ? `<small>${o.help}</small>` : ''}</div>`;
  }
  function fText(section, key, label, o = {}) {
    const v = section === 'name' ? S.project.name : get(S.project[section], key), id = 'f' + (++uid);
    const input = `<input id="${id}" type="text" value="${esc(v || '')}" data-s="${section}" data-k="${key}"${o.placeholder ? ` placeholder="${esc(o.placeholder)}"` : ''} spellcheck="false" autocomplete="off">`;
    const body = o.pick ? `<div class="with-btn">${input}<button class="btn" type="button" data-pick="${o.pick}" data-for="${id}" data-title="${esc(o.title || label)}">Browse</button></div>` : input;
    return `<div class="field"><label for="${id}">${label}</label>${body}${o.help ? `<small>${o.help}</small>` : ''}</div>`;
  }
  function fSelect(section, key, label, options, o = {}) {
    const v = get(S.project[section], key), id = 'f' + (++uid);
    return `<div class="field"><label for="${id}">${label}</label><select id="${id}" data-s="${section}" data-k="${key}"${o.rerender ? ' data-rerender="1"' : ''}>${options.map(([val, text]) => `<option value="${esc(val)}"${String(val) === String(v) ? ' selected' : ''}>${esc(text)}</option>`).join('')}</select>${o.help ? `<small>${o.help}</small>` : ''}</div>`;
  }

  // ---------- charts ----------
  function roseChart(sectors) {
    const R = 118, cx = 160, cy = 150, n = sectors.length, w = 360 / n, gap = 1.6, r0 = 7;
    const max = Math.max(...sectors.map(s => s.freq));
    const stepv = max > 0.32 ? 0.1 : max > 0.16 ? 0.05 : 0.02, top = Math.ceil(max / stepv) * stepv;
    const pt = (deg, r) => [cx + r * Math.sin(deg * Math.PI / 180), cy - r * Math.cos(deg * Math.PI / 180)];
    let g = '';
    for (let f = stepv; f <= top + 1e-9; f += stepv) {
      const r = r0 + (R - r0) * f / top;
      g += `<circle class="grid" cx="${cx}" cy="${cy}" r="${r.toFixed(1)}"/><text x="${(cx + 4).toFixed(1)}" y="${(cy - r - 3).toFixed(1)}">${Math.round(f * 100)} %</text>`;
    }
    [['N', 0], ['E', 90], ['S', 180], ['W', 270]].forEach(([t, a]) => { const [x, y] = pt(a, R + 22); g += `<text class="ink" x="${x.toFixed(1)}" y="${(y + 4).toFixed(1)}" text-anchor="middle" font-weight="700">${t}</text>`; });
    sectors.forEach(s => {
      const r = r0 + (R - r0) * s.freq / top, a0 = s.centre - w / 2 + gap, a1 = s.centre + w / 2 - gap;
      const [x0, y0] = pt(a0, r0), [x1, y1] = pt(a0, r), [x2, y2] = pt(a1, r), [x3, y3] = pt(a1, r0);
      const label = `<b>From ${Math.round(s.centre)}° (${compass(s.centre)})</b><span>${num(100 * s.freq, 1)} % of the time${ok(s.mean) ? `, mean ${num(s.mean, 1)} m/s` : ''}</span>`;
      g += `<path d="M${x0.toFixed(1)} ${y0.toFixed(1)}L${x1.toFixed(1)} ${y1.toFixed(1)}A${r.toFixed(1)} ${r.toFixed(1)} 0 0 1 ${x2.toFixed(1)} ${y2.toFixed(1)}L${x3.toFixed(1)} ${y3.toFixed(1)}A${r0} ${r0} 0 0 0 ${x0.toFixed(1)} ${y0.toFixed(1)}Z" fill="var(--series-1)" tabindex="0" data-tip="${esc(label)}"/>`;
    });
    return `<div class="chart rose"><svg viewBox="0 0 320 300" role="img" aria-label="Wind rose: share of time the wind comes from each direction">${g}</svg></div>`;
  }

  function lineChart(rows, series, yLabel) {
    // rows: [{label, values: [..]}]; series: [{name, colour}]
    const W = 760, H = 270, L = 46, Rr = 150, T = 12, B = 34, pw = W - L - Rr, ph = H - T - B;
    const all = rows.flatMap(r => r.values).filter(ok);
    let lo = Math.floor(Math.min(...all)), hi = Math.ceil(Math.max(...all));
    if (hi - lo < 2) { lo -= 1; hi += 1; }
    const x = i => L + (rows.length === 1 ? pw / 2 : pw * i / (rows.length - 1)), y = v => T + ph * (1 - (v - lo) / (hi - lo));
    let g = '';
    const ticks = hi - lo > 8 ? 2 : 1;
    for (let v = lo; v <= hi; v += ticks) g += `<line class="grid" x1="${L}" x2="${L + pw}" y1="${y(v).toFixed(1)}" y2="${y(v).toFixed(1)}"/><text x="${L - 8}" y="${(y(v) + 4).toFixed(1)}" text-anchor="end">${v}</text>`;
    const every = Math.ceil(rows.length / 8);
    rows.forEach((r, i) => { if (i % every === 0) g += `<text x="${x(i).toFixed(1)}" y="${H - 12}" text-anchor="middle">${esc(r.label)}</text>`; });
    g += `<text x="${L - 34}" y="${T + ph / 2}" transform="rotate(-90 ${L - 34} ${T + ph / 2})" text-anchor="middle">${esc(yLabel)}</text>`;
    const ends = [];
    series.forEach((s, k) => {
      const pts = rows.map((r, i) => ok(r.values[k]) ? [x(i), y(r.values[k])] : null).filter(Boolean);
      if (!pts.length) return;
      g += `<polyline fill="none" stroke="${s.colour}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" points="${pts.map(p => p.map(c => c.toFixed(1)).join(',')).join(' ')}"/>`;
      const e = pts[pts.length - 1];
      g += `<circle cx="${e[0].toFixed(1)}" cy="${e[1].toFixed(1)}" r="4.5" fill="${s.colour}" stroke="var(--surface)" stroke-width="2"/>`;
      ends.push({ y: e[1], name: s.name });
    });
    if (ends.length === 2 && Math.abs(ends[0].y - ends[1].y) >= 15) ends.forEach(e => { g += `<text class="ink" x="${L + pw + 12}" y="${(e.y + 4).toFixed(1)}">${esc(e.name)}</text>`; });
    const bw = rows.length > 1 ? pw / (rows.length - 1) : pw;
    rows.forEach((r, i) => {
      const label = `<b>${esc(r.label)}</b>` + series.map((s, k) => `<span>${esc(s.name)}: ${num(r.values[k], 2)} m/s</span>`).join('<br>');
      g += `<rect x="${(x(i) - bw / 2).toFixed(1)}" y="${T}" width="${bw.toFixed(1)}" height="${ph}" fill="transparent" tabindex="0" data-tip="${esc(label)}"/>`;
    });
    const legend = `<div class="legend">${series.map(s => `<span><i style="background:${s.colour}"></i>${esc(s.name)}</span>`).join('')}</div>`;
    return `<div class="chart">${legend}<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(yLabel)} by month">${g}</svg></div>`;
  }

  // ---------- rail ----------
  function renderRail() {
    const p = S.project, working = !!(job && job.status === 'running');
    let h = `<a class="brand" href="#" data-go="home"><img src="/static/img/bohemian-logo-white.png" alt="Bohemian Renewables"><span>Bohemian Wind ${esc(S.app.version)}</span></a>`;
    h += `<div class="mast${working ? ' busy' : ''}">${ICON.cups}<ol>`;
    h += `<li class="${view === 'home' ? 'current ' : ''}${p ? 'done' : 'ready'}"><button type="button" data-go="home"><span class="node"></span><span class="label"><b>Overview</b><small>${p ? 'Where the project stands' : 'Start here'}</small></span></button></li>`;
    ORDER.forEach(key => {
      const m = STEP[key], st = p ? stepOf(key) : { status: 'blocked' };
      const running = working && job.current === key;
      h += `<li class="${view === key ? 'current ' : ''}${running ? 'running' : st.status}"><button type="button" data-go="${key}"${p ? '' : ' aria-disabled="true"'}><span class="node"></span><span class="label"><b>${m.n}. ${m.title}</b><small>${m.short}</small></span></button></li>`;
    });
    h += '</ol></div>';
    if (p) h += `<div class="project"><b title="${esc(p.name)}">${esc(p.name)}</b><small title="${esc(p.folder)}">${esc(p.folder.length > 34 ? '…' + p.folder.slice(-33) : p.folder)}</small><button class="btn quiet" type="button" data-act="close">Switch project</button></div>`;
    rail.innerHTML = h;
  }

  function renderFoot() {
    $('#foot').innerHTML = `<b>Not for financing decisions</b><span>${esc(S.app.notice)}</span>`;
  }

  // ---------- start (no project) ----------
  function viewStart() {
    const recent = S.recent.length ? `<div class="recent">${S.recent.map(r => `<button type="button" data-act="open-recent" data-folder="${esc(r.folder)}"><b>${esc(r.name)}</b><small>${esc(r.folder)}</small></button>`).join('')}</div>` : '<p class="note">Projects you open will be listed here.</p>';
    return `<div class="hero"><img src="/static/img/hero.jpg" alt=""><div class="in"><h1>From mast data to a permitting pack</h1>
      <p>Six steps, in order: check the mast data, correct it to the long term, map the wind, place the turbines round your constraints, screen noise and shadow flicker, and build the pack.</p>
      <div class="acts"><button class="btn primary" type="button" data-act="demo">Open the demo project</button></div></div></div>
      <div class="page"><div class="start">
      ${card('New project', `
        <div class="field"><label for="np-name">Project name</label><input id="np-name" type="text" placeholder="For example: Example Hill"></div>
        <div class="field"><label for="np-folder">Folder for the project</label><div class="with-btn"><input id="np-folder" type="text" spellcheck="false" placeholder="${esc(S.app.projects_home)}"><button class="btn" type="button" data-pick="folder" data-for="np-folder" data-title="Folder for the new project">Browse</button></div><small>Leave empty to create it in ${esc(S.app.projects_home)}.</small></div>
        <div class="row3"><div class="field"><label for="np-lat">Latitude</label><input id="np-lat" type="number" step="any" placeholder="optional"></div>
        <div class="field"><label for="np-lon">Longitude</label><input id="np-lon" type="number" step="any" placeholder="optional"></div>
        <div class="field"><label for="np-elev">Elevation, m</label><input id="np-elev" type="number" step="any" placeholder="optional"></div></div>
        <p class="hint">The position is read from the logger files when they carry one.</p>
        <button class="btn primary wide" type="button" data-act="new" style="margin-top:12px">Create the project</button>`)}
      ${card('Open a project', `
        <div class="field"><label for="op-folder">Project folder</label><div class="with-btn"><input id="op-folder" type="text" spellcheck="false"><button class="btn" type="button" data-pick="folder" data-for="op-folder" data-title="Project folder">Browse</button></div></div>
        <button class="btn wide" type="button" data-act="open">Open the project</button>
        <h2 style="margin-top:18px">Recent</h2>${recent}`)}
      ${card('The demo project', `<p class="note">An invented site with everything filled in, so each step can be tried in a minute without any data or internet connection. The mast records, the terrain, the homes and the zones are all made up.</p>
        <p class="note">Five faults are planted in the mast records for the checks to find.</p>`)}
      </div><p class="photo-credit">Field photographs supplied by Bohemian Renewables.</p></div>`;
  }

  // ---------- overview ----------
  function sumLine(key, r) {
    if (!r) return STEP[key].short;
    if (key === 'data') return `${num(r.mean_ws, 2)} m/s at ${num(r.top_height_m, 0)} m, ${num(r.recovery_pct, 1)} % valid, ${day(r.period[0])} to ${day(r.period[1])}`;
    if (key === 'longterm') return `Factor ${num(r.factor, 3)}: long-term mean ${num(r.long_term, 2)} m/s at ${num(r.height_m, 0)} m`;
    if (key === 'map') return `${num(r.min_ws, 1)} to ${num(r.max_ws, 1)} m/s at ${num(r.height_m, 0)} m over ${num(r.area_km2, 0)} km²`;
    if (key === 'layout') return `${r.n_placed} turbines, ${num(r.capacity_mw, 1)} MW, P50 ${num(r.p50_gwh, 0)} GWh a year`;
    if (key === 'environment') return `${num(r.noise.over_limit, 0)} of ${num(r.noise.n_receptors, 0)} homes over the noise level, ${num(r.flicker.over_limit, 0)} over a flicker limit`;
    return `${r.files.length + 1} files in ${r.folder}`;
  }

  function workingPanel() {
    const cur = job.current || job.steps[Math.min(job.done.length, job.steps.length - 1)];
    const many = job.steps.length > 1 ? ` (step ${Math.min(job.done.length + 1, job.steps.length)} of ${job.steps.length} in this run)` : '';
    return `<section class="card"><div class="working">${ICON.cups}<div><h2 id="w-title">Working: ${esc(STEP[cur].title.toLowerCase())}${many}</h2>
      <p class="note">This can take from a few seconds to a few minutes. You can look at the other steps meanwhile.</p>
      <div class="logbox" id="w-log">${logLines.slice(-60).map(l => `<div>${esc(l)}</div>`).join('')}</div></div></div></section>`;
  }

  function errorPanel() {
    return `<section class="card alert"><h2>${ICON.cross}${esc(STEP[lastError.step].title)} did not finish</h2><p class="note">${esc(lastError.message)}</p>
      ${lastError.log.length ? `<details class="more"><summary>Show the working log</summary><div class="logbox" style="font-size:13px;color:var(--ink-2);display:grid;gap:3px">${lastError.log.map(l => `<div style="white-space:pre-wrap;overflow-wrap:anywhere">${esc(l)}</div>`).join('')}</div></details>` : ''}</section>`;
  }

  function viewHome() {
    const p = S.project, d = stepOf('data').result;
    const pos = ok(p.site.lat) && ok(p.site.lon) ? `${num(p.site.lat, 4)}, ${num(p.site.lon, 4)}` : d && ok(d.lat) ? `${num(d.lat, 4)}, ${num(d.lon, 4)}` : 'position not set yet';
    const done = p.steps.filter(s => s.status === 'done').length;
    const running = job && job.status === 'running';
    let h = `<div class="hero"><img src="/static/img/hero.jpg" alt=""><div class="in"><h1>${esc(p.name)}</h1>
      <p>Mast at ${esc(pos)}. ${done} of 6 steps done${p.steps.some(s => s.status === 'stale') ? ', some out of date' : ''}.</p>
      <div class="acts"><button class="btn primary" type="button" data-act="runall"${running ? ' disabled' : ''}>Run all six steps</button>
      <button class="btn" type="button" data-act="reveal" data-path="">Open the project folder</button></div></div></div><div class="page">`;
    if (running) h += workingPanel() + '<div style="height:18px"></div>';
    else if (lastError) h += errorPanel() + '<div style="height:18px"></div>';
    if (p.demo) h += card('', '<p class="note">This is the demo project. Every number in it is invented: the mast records, the terrain, the reference, the homes and the zones. No result says anything about a real site.</p>') + '<div style="height:18px"></div>';
    h += '<div class="ladder">' + ORDER.map(key => {
      const st = stepOf(key), m = STEP[key];
      return `<button type="button" class="${st.status}" data-go="${key}"><span class="n">${st.status === 'done' ? '✓' : m.n}</span><span><b>${m.title}</b><span class="sum">${esc(sumLine(key, st.result))}</span></span>${pill(st)}</button>`;
    }).join('') + '</div>';
    h += '<p class="photo-credit">Field photographs supplied by Bohemian Renewables.</p></div>';
    return h;
  }

  // ---------- inputs per step ----------
  const INPUTS = {
    data() {
      return card('What to check', fText('data', 'folder', 'Folder with the logger files', { pick: 'folder', help: 'One mast per folder. NRG SymphoniePRO (.rld or .txt), NRG Symphonie text exports (.txt from Symphonie Data Retriever), Campbell TOA5 (.dat), a Nomad2 export or a WindPRO time series export. ZIP, RAR, 7z and tar archives of these files are opened as they are: nothing needs unpacking first.' })
        + fText('name', 'name', 'Name on the report')
        + `<details class="more"${ok(S.project.site.lat) || ok(S.project.site.elevation_m) || Number(S.project.data.direction_offset_deg) || ok(S.project.data.shear_alpha) ? ' open' : ''}><summary>Mast position, elevation, vane and shear</summary>
           <div class="row2">${fNum('site', 'lat', 'Latitude', { placeholder: 'from the logger' })}${fNum('site', 'lon', 'Longitude', { placeholder: 'from the logger' })}</div>
           ${fNum('site', 'elevation_m', 'Ground elevation, m', { placeholder: 'from the logger', help: 'Fill these in only to replace what the logger files say, or when they say nothing.' })}
           <div class="row2">${fNum('data', 'direction_offset_deg', 'Vane correction, degrees', { min: -360, max: 360 })}${fNum('data', 'shear_alpha', 'Shear exponent to use', { min: -0.2, max: 0.8, placeholder: 'measured' })}</div>
           <p class="hint">The vane correction is added to every wind direction from the wind map onwards, for a vane mounted off north. Leave the shear exponent empty to use the measured one.</p></details>`
        + channelsEditor());
    },
    longterm() {
      const src = S.project.longterm.source;
      return card('Reference data', `<div class="choice" role="radiogroup" aria-label="Where the reference comes from">
          <label><input type="radio" name="lt-src" value="power" data-s="longterm" data-k="source" data-rerender="1"${src !== 'file' ? ' checked' : ''}><span><b>Download from NASA POWER</b><br><small style="color:var(--muted)">MERRA-2 wind speed at 50 m for the mast's position. Needs the internet.</small></span></label>
          <label><input type="radio" name="lt-src" value="file" data-s="longterm" data-k="source" data-rerender="1"${src === 'file' ? ' checked' : ''}><span><b>Use files I already have</b><br><small style="color:var(--muted)">NASA POWER point files in JSON.</small></span></label></div>
          ${src === 'file' ? fText('longterm', 'monthly_file', 'Monthly file', { pick: 'file' }) + fText('longterm', 'daily_file', 'Daily file for the current year', { pick: 'file', help: 'Optional.' }) : ''}`);
    },
    map() {
      const m = S.project.map;
      return card('How to map the wind', fSelect('map', 'engine', 'Method', S.app.engines.map(e => [e.key, e.name]), { rerender: true })
        + (m.engine === 'atlas' ? fText('map', 'gwa_dir', 'Folder with the Global Wind Atlas files', { pick: 'folder', help: 'GeoTIFF wind speed layers for the area, from globalwindatlas.info.' }) : '')
        + `<div class="row2">${fNum('map', 'height_m', 'Height above ground, m', { min: 10, max: 300 })}${fNum('map', 'half_km', 'Distance from the mast, km', { min: 2, max: 30, help: 'Each way.' })}</div>
           <details class="more"><summary>Grid and roughness</summary><div class="row2">${fNum('map', 'res_m', 'Cell size, m', { min: 30, max: 500, help: '50 for narrow ridges, 100 for open ground.' })}${fNum('map', 'z0_m', 'Roughness length, m', { min: 0.0002, max: 2, help: '0.03 grass, 0.05 farmland, 0.3 bush.' })}</div></details>`);
    },
    layout() {
      const L = S.project.layout, custom = !S.app.turbines.some(t => t.key === L.turbine);
      const opts = S.app.turbines.map(t => [t.key, t.key]).concat([['custom', 'Another size']]);
      const fileRow = (kind, label, help) => {
        const cur = L[kind + '_file'];
        return `<div class="field"><span class="lab">${label}</span>${cur ? `<div class="with-btn"><input type="text" value="${esc(cur)}" disabled aria-label="${label}"><button class="btn" type="button" data-act="detach" data-kind="${kind}">Remove</button></div>` : `<div class="with-btn"><input id="at-${kind}" type="text" spellcheck="false" placeholder="GeoJSON, KML, KMZ or CSV" aria-label="${label}"><button class="btn" type="button" data-pick="file" data-for="at-${kind}" data-attach="${kind}" data-title="${esc(label)}">Browse</button></div>`}<small>${help}</small></div>`;
      };
      const B = S.project.buildings, busy = job && job.status === 'running';
      const half = S.project.buildings_half_km;
      const reach = ok(half) ? `within ${num(half, Number.isInteger(Number(half)) ? 0 : 1)} km of the mast` : 'round the search area';
      const buildingsRow = `<div class="field"><span class="lab">Buildings from open data</span>${B ? `<div class="with-btn"><input type="text" value="${esc(`${num(B.n, 0)} buildings, ${day(B.fetched)}`)}" disabled aria-label="Buildings from open data"><button class="btn" type="button" data-act="detach" data-kind="buildings">Remove</button></div>` : ''}
        <div class="links" style="margin:8px 0 6px"><button class="btn" type="button" data-run="buildings"${busy ? ' disabled' : ''}>${B ? 'Get them again' : 'Get buildings'}</button></div>
        <small>Downloads the buildings mapped in OpenStreetMap and those Microsoft detected in satellite and aerial imagery, ${reach}, and merges them. It needs the internet, and a first download can take several minutes. Every building counts as a home.</small></div>`;
      const losses = L.losses || [];
      const lossSum = 100 * (1 - losses.reduce((a, x) => a * (1 - x.pct / 100), 1));
      return card('Turbines', fSelect('layout', 'turbine', 'Turbine size', opts, { rerender: true, help: 'Generic turbines of each size. No manufacturer data is used.' })
          + (custom ? `<div class="row2">${fNum('layout', 'rated_kw', 'Rated power, kW', { min: 500 })}${fNum('layout', 'rotor_m', 'Rotor diameter, m', { min: 40 })}</div>` : '')
          + `<div class="row2">${fNum('layout', 'hub_m', 'Hub height, m', { min: 40, max: 250 })}${fNum('layout', 'n_turbines', 'Number of turbines', { min: 1, max: 150, step: 1 })}</div>`
          + fileRow('positions', 'Turbine positions of your own', L.positions_file ? 'The layout is taken as given: the number of turbines, the spacing and the fine-tuning are not used. Remove the file to have a layout designed.' : 'Optional. One point per turbine, to work out the energy of a layout you already have.'))
        + card('Your constraints', fileRow('exclusions', 'Exclusion zones, roads and lines', 'Polygons are kept clear; lines get a buffer each side.')
          + buildingsRow
          + fileRow('receptors', B ? 'Homes of your own, on top' : 'Homes and other receptors', B ? 'One point per home, for example placemarks from Google Earth. Each counts, and stands for a fetched building within 30 m. A point or an outline named "not a home" takes fetched buildings out.' : 'One point per home. Also used for noise and shadow flicker.')
          + fileRow('boundary', 'Site boundary', 'Turbines stay inside it.')
          + `<details class="more" open><summary>Buffers and limits</summary>
             <div class="row2">${fNum('layout', 'rules.receptor_setback_m', 'Setback from homes, m', { min: 0 })}${fNum('layout', 'rules.exclusion_buffer_m', 'Buffer round zones, m', { min: 0 })}</div>
             <div class="row2">${fNum('layout', 'rules.line_buffer_m', 'Buffer along lines, m', { min: 0 })}${fNum('layout', 'rules.slope_max_pct', 'Steepest ground, %', { min: 1, max: 60 })}</div>
             <div class="row2">${fNum('layout', 'rules.radius_km', 'Search within, km of mast', { min: 1, max: 30 })}${fNum('layout', 'rules.min_mean_ws', 'Lowest wind speed, m/s', { placeholder: 'no limit', min: 0 })}</div></details>`)
        + card('Spacing, wakes and losses', `<div class="row2">${fNum('layout', 'cross_d', 'Across the wind, rotors', { min: 1.5, max: 15 })}${fNum('layout', 'along_d', 'Along the wind, rotors', { min: 1.5, max: 20 })}</div>
            ${fNum('layout', 'link_m', 'Keep every turbine within, m of another', { placeholder: 'no limit', min: 0, help: 'Keeps the wind farm in one piece for roads and cables.' })}
            ${fSelect('layout', 'wake', 'Wake model', S.app.wake_models.map(w => [w.key, w.name]))}
            <label class="check"><input type="checkbox" data-s="layout" data-k="refine"${L.refine ? ' checked' : ''}><span>Fine-tune positions against wakes (slower)</span></label>
            <details class="more"><summary>Losses other than wakes: ${num(lossSum, 1)} %</summary>${losses.map((x, i) => `<div class="field"><label for="loss${i}">${esc(x.item)}, %</label><input id="loss${i}" type="number" step="any" min="0" max="99" value="${x.pct}" data-loss="${i}"><small>${esc(x.basis)}</small></div>`).join('')}</details>`);
    },
    environment() {
      return card('Limits to screen against', `<div class="row2">${fNum('environment', 'sound_power_dba', 'Sound power per turbine, dB(A)', { min: 85, max: 120 })}${fNum('environment', 'noise_limit_dba', 'Noise level at homes, dB(A)', { min: 20, max: 80 })}</div>
        <div class="row2">${fNum('environment', 'flicker_hours_limit', 'Flicker, hours a year', { min: 0 })}${fNum('environment', 'flicker_minutes_limit', 'Flicker, minutes a day', { min: 0 })}</div>
        ${fNum('environment', 'year', 'Year for the sun positions', { min: 1950, max: 2099, step: 1 })}
        <p class="hint">Set the limits to what applies at the site. The defaults are common screening values, not a statement of any country's rules.</p>`);
    },
    pack() {
      const env = stepOf('environment');
      return card('What goes in', list(['Turbine coordinates in latitude and longitude and in UTM, with ground elevation and blade tip height', 'The layout as GeoJSON and as KML for Google Earth',
        'The record of siting rules and the area each removed', env.status === 'done' ? 'Noise and shadow flicker tables and maps' : 'Noise and shadow flicker are left out until step 5 is done and up to date',
        'A summary page to print or send']));
    },
  };

  function channelsEditor() {
    const ch = S.project.data.channels || {};
    const rows = Object.entries(ch).map(([port, v]) => [port, v.height_m, v.direction_offset_deg]).concat([['', '', '']]);
    return `<details class="more"${Object.keys(ch).length ? ' open' : ''}><summary>Sensor heights and vane offsets, sensor by sensor</summary>
      ${rows.map((r, i) => `<div class="row3 chrow"><div class="field"><label for="ch-p${i}">Port or channel</label><input id="ch-p${i}" type="text" data-ch="port" value="${esc(r[0])}" placeholder="A4 or CH7" autocomplete="off"></div>
        <div class="field"><label for="ch-h${i}">Height, m</label><input id="ch-h${i}" type="number" step="any" min="0" data-ch="height" value="${ok(r[1]) ? r[1] : ''}"></div>
        <div class="field"><label for="ch-o${i}">Vane offset, °</label><input id="ch-o${i}" type="number" step="any" data-ch="offset" value="${ok(r[2]) ? r[2] : ''}"></div></div>`).join('')}
      <p class="hint">For files that carry no sensor heights (SecondWind Nomad2), and to put right a height or a vane's north offset that the files give wrongly or not at all (NRG Symphonie text exports, WindPRO exports). Name the sensor as the file does: a port such as A4, a channel such as CH7, or V1 and D1 for a WindPRO export. Empty the name to take a line out.</p></details>`;
  }

  function runCard(key, st) {
    const m = STEP[key], running = job && job.status === 'running';
    const blocked = st.status === 'blocked';
    const first = blocked ? [] : (st.redo || []);
    const ns = first.map(k => STEP[k].n);      // the steps' names hold "and" themselves: several are named by number
    const hint = blocked ? `Run first: ${esc(st.needs.join(', ').toLowerCase())}.`
      : first.length === 1 ? `Runs step ${ns[0]} (${esc(STEP[first[0]].title.toLowerCase())}) again first: it is out of date.`
      : first.length ? `Runs steps ${ns.slice(0, -1).join(', ')} and ${ns[ns.length - 1]} again first: they are out of date.`
      : st.result ? `Last run ${esc(when(st.result.at))}.` : '';
    return `<div class="card" id="run-card"><button class="btn primary wide" type="button" data-run="${key}"${first.length ? ` data-first="${first.join(',')}"` : ''}${blocked || running ? ' disabled' : ''}>${m.verb}</button>${hint ? `<p class="hint">${hint}</p>` : ''}</div>`;
  }

  function intakeCard() {
    const d = S.project.data, st = S.project.intake || {};
    const kind = st.state === 'stale' ? 'near' : st.state === 'error' ? 'over' : 'ok';
    const status = st.last_check ? `<p style="margin-top:10px;font-size:13.5px">${state(kind, st.message || 'Checked.')}</p><p class="hint">Last look ${esc(when(st.last_check))}${st.last_record ? `; newest record ${esc(when(st.last_record))}` : ''}.</p>` : '';
    return card('Daily intake', `<label class="check"><input type="checkbox" id="watch-on"${st.watching ? ' checked' : ''}><span>Look in this folder for new logger files and check them as they arrive</span></label>
      <div class="row2"><div class="field"><label for="watch-min">Look every, minutes</label><input id="watch-min" type="number" min="5" max="1440" step="5" value="${d.watch_minutes}"></div>
      <div class="field"><label for="watch-stale">Alert after, hours without data</label><input id="watch-stale" type="number" min="1" step="1" value="${d.stale_hours}"></div></div>
      ${status}<p class="hint">It watches the folder while Bohemian Wind is open. It does not collect files from the logger: point the logger's own software, or a synced folder, at this folder.</p>`);
  }

  // ---------- results per step ----------
  const RESULTS = {
    data(r) {
      const findings = r.findings.filter(f => f !== S.app.disclaimer);
      const flagCols = ['missing', 'conflict', 'maintenance', 'no_signal', 'range', 'inconsistent', 'stuck', 'spike'];
      const used = flagCols.filter(c => (r.flags || []).some(f => f[c] > 0));
      const flags = (r.flags || []).length ? table(['Sensor', 'Valid, %'].concat(used.map(c => c.replace('_', ' '))),
        r.flags.map(f => [esc(f.sensor), num(f['valid_%'], 1)].concat(used.map(c => f[c] ? num(f[c], 0) : ''))), [1].concat(used.map((_, i) => i + 2))) : '';
      const monthly = r.monthly || [];
      return `<div class="tiles">
          ${tile('Mean wind speed', `${num(r.mean_ws, 2)} <small>m/s</small>`, `at ${num(r.top_height_m, 0)} m, all valid records`)}
          ${tile('Valid data', `${num(r.recovery_pct, 1)} <small>%</small>`, `${num(r.records, 0)} records at the top height`)}
          ${tile('Period', `${num(r.months_usable, 0)} <small>months</small>`, `${day(r.period[0])} to ${day(r.period[1])}`)}
          ${tile('Shear exponent', ok(r.shear_alpha) ? num(r.shear_alpha, 3) : '–', esc(r.shear_basis || '') + (ok(S.project.data.shear_alpha) ? `; ${num(S.project.data.shear_alpha, 2)} entered is used` : ''))}
          ${tile('Turbulence at 15 m/s', ok(r.ti15) ? num(r.ti15, 3) : '–', 'representative intensity')}
          ${tile('Air density', ok(r.density) ? `${num(r.density, 3)} <small>kg/m³</small>` : '–', 'from temperature and pressure')}
        </div>
        ${r.shear_warning ? card(`${ICON.warn}Unusual shear`, `<p class="note">${esc(r.shear_warning)}</p>`, 'warnbox') : ''}
        ${card(`Findings <small>${findings.length}</small>`, findings.length ? list(findings, 'findings') : '<p class="note">Nothing to report.</p>')}
        <div class="maps2">
          ${card(`Where the wind comes from <small>${num(r.top_height_m, 0)} m, as logged</small>`, r.no_direction ? '<p class="note">No direction was measured.</p>' : roseChart(r.rose)
            + (Number(S.project.data.direction_offset_deg) ? `<p class="hint">The wind map and the layout turn these directions by ${num(S.project.data.direction_offset_deg, 0)}° (the vane correction).</p>` : '')
            + `<details class="more"><summary>Show the numbers</summary>${table(['From', 'Share of time, %', 'Mean, m/s'], r.rose.map(s => [`${Math.round(s.centre)}° ${compass(s.centre)}`, num(100 * s.freq, 1), num(s.mean, 2)]), [1, 2])}</details>`)}
          ${card('By height', table(['Height, m', 'Mean, m/s', 'Mean of monthly means, m/s', 'Valid, %', 'Weibull A', 'k'],
            r.heights.map(h => [num(h.height_m, 0), num(h.mean_ws, 2), num(h.momm_ws, 2), num(h.recovery_pct, 1), num(h.weibull_A, 2), num(h.weibull_k, 2)]), [0, 1, 2, 3, 4, 5])
            + (monthly.length ? `<details class="more"><summary>Monthly means at ${num(r.top_height_m, 0)} m</summary>${table(['Month', 'Mean, m/s', 'Valid, %'], monthly.map(m => [monthName(m.month), num(m.mean, 2), num(100 * m.share, 0)]), [1, 2])}</details>` : ''))}
        </div>
        ${flags ? card('Records flagged, by sensor', flags + '<p class="hint">A flagged record is left out of the statistics. The full list of periods is in the Excel summary.</p>') : ''}
        ${card('Files of this run', `<div class="links">${r.report ? `<a class="btn" href="${fileUrl(r.report)}" target="_blank" rel="noopener">Open the full report</a>` : ''}${r.excel ? `<a class="btn" href="${fileUrl(r.excel)}">Download the Excel summary</a>` : ''}<button class="btn" type="button" data-act="reveal" data-path="${esc(r.run_dir)}">Open the run folder</button></div>
          <p class="hint">${esc(r.format)}. Checks version ${esc(r.version)}.</p>`)}`;
    },
    longterm(r) {
      const rows = r.monthly.map(m => ({ label: monthName(m.month), values: [m.mast, m.reference] }));
      const left = (r.reference_months_left_out || []).map(m => monthName(m.month));
      return `<div class="tiles">
          ${tile('Long-term mean wind speed', `${num(r.long_term, 2)} <small>m/s at ${num(r.height_m, 0)} m</small>`, `measured ${num(r.measured, 2)} m/s over ${r.months} months`, true)}
          ${tile('Long-term factor', num(r.factor, 3), r.factor < 1 ? 'the campaign was windier than usual' : r.factor > 1 ? 'the campaign was calmer than usual' : 'the campaign was typical')}
          ${tile('Monthly correlation', `r = ${num(r.r, 2)}`, 'mast against reference')}
          ${tile('Reference years', `${r.lt_years[0]} to ${r.lt_years[1]}`, 'the long-term period')}
        </div>
        ${(r.cautions || []).length ? card(`${ICON.warn}${r.indicative ? 'Indicative only' : 'Check before relying on this'}`, list(r.cautions), 'warnbox') : ''}
        ${card('Mast and reference, month by month', lineChart(rows, [{ name: `Mast, ${num(r.height_m, 0)} m`, colour: 'var(--series-1)' }, { name: 'Reference, 50 m', colour: 'var(--series-2)' }], 'Monthly mean wind speed, m/s')
          + `<details class="more"><summary>Show the numbers</summary>${table(['Month', 'Mast, m/s', 'Reference, m/s'], r.monthly.map(m => [monthName(m.month), num(m.mast, 2), num(m.reference, 2)]), [1, 2])}</details>`)}
        ${card('How the factor was made', list([`Method: ${r.method}.`, `Reference over the measured months ${num(r.reference_concurrent, 2)} m/s; over ${r.lt_years[0]} to ${r.lt_years[1]} ${num(r.reference_long_term, 2)} m/s.`,
          'One reference and one monthly method. A second reference and a daily or hourly method would test it.'].concat(left.length ? [`Reference months left out because they break the usual mast-to-reference ratio: ${left.join(', ')}.`] : [])
          .concat(r.vane_check ? [`Vane north check: the wind direction at ${num(r.vane_check.height_m, 0)} m runs ${r.vane_check.median_deg > 0 ? '+' : ''}${num(r.vane_check.median_deg, 0)}° from the reanalysis direction on ${r.vane_check.days} days with ${num(r.vane_check.min_ws, 0)} m/s or more (the middle half of the days: ${num(r.vane_check.q25_deg, 0)}° to ${num(r.vane_check.q75_deg, 0)}°). Terrain turns the wind, so some tens of degrees are normal.`] : []))
          + `<p class="hint">${esc(r.credit || '')}</p>`)}`;
    },
    map(r, st) {
      return `<div class="tiles">
          ${tile('At the mast', `${num(r.mast_mean_ws, 2)} <small>m/s</small>`, `at ${num(r.height_m, 0)} m`)}
          ${tile('Across the area', `${num(r.min_ws, 1)} to ${num(r.max_ws, 1)} <small>m/s</small>`, `${num(r.area_km2, 0)} km² in ${num(r.cell_m, 0)} m cells`)}
          ${tile('Terrain', `${num(r.elev_min, 0)} to ${num(r.elev_max, 0)} <small>m</small>`, `mast at ${num(r.mast_elev, 0)} m`)}
          ${tile('Steep ground', `${num(r.steep_share_pct, 1)} <small>%</small>`, 'of the area above 30 % slope')}
        </div>
        ${(r.warnings || []).length ? card(`${ICON.warn}Check before relying on this`, list(r.warnings), 'warnbox') : ''}
        <a href="${fileUrl(r.map, st.result.at)}" target="_blank" rel="noopener"><img class="map" src="${fileUrl(r.map, st.result.at)}" alt="Map of mean wind speed around the mast"></a>
        ${card('What the map rests on', list(r.notes) + (r.credits.length ? `<p class="hint">${r.credits.map(esc).join(' ')}</p>` : ''))}
        ${card('', `<div class="links"><a class="btn" href="${fileUrl(r.map_print)}" target="_blank" rel="noopener">Open the print version</a></div>`)}`;
    },
    layout(r, st) {
      const u = r.uncertainty, t = r.turbine;
      const wrap = t => `<span style="white-space:normal">${esc(t)}</span>`;
      const checks = Object.entries(r.wake_checks).map(([k, v]) => [wrap((S.app.wake_models.find(w => w.key === k) || { name: k }).name), num(v.wake_loss_pct, 1), num(v.p50_gwh, 0)]);
      const short = r.n_placed < r.n_requested;
      return `<div class="tiles">
          ${tile('Net energy, P50', `${num(r.p50_gwh, 0)} <small>GWh a year</small>`, `${r.n_placed} × ${esc(t.name.replace('Generic ', ''))}, ${num(r.capacity_mw, 1)} MW`, true)}
          ${tile('Capacity factor', `${num(100 * r.capacity_factor, 1)} <small>%</small>`, `${num(r.full_load_hours, 0)} full-load hours`)}
          ${tile('Wake loss', `${num(r.wake_loss_pct, 1)} <small>%</small>`, esc(r.wake_model === 'niayifar' ? 'Gaussian wake model' : r.wake_model === 'jensen075' ? 'Jensen wake model' : 'TurbOPark wake model'))}
          ${tile('Other losses', `${num(r.other_loss_pct, 1)} <small>%</small>`, 'availability, electrical, performance')}
          ${tile('P90, 10-year average', `${num(u.p90_10_year, 0)} <small>GWh</small>`, `P75 ${num(u.p75_10_year, 0)} GWh`)}
          ${tile('P90, any one year', `${num(u.p90_1_year, 0)} <small>GWh</small>`, `uncertainty ${num(u.sigma_pct['1_year'], 1)} % of energy`)}
          ${tile('Gross energy', `${num(r.gross_gwh, 0)} <small>GWh a year</small>`, 'before wakes and losses')}
          ${tile('Wind at the turbines', `${num(r.mean_free_ws, 2)} <small>m/s</small>`, `mean at ${num(t.hub_m, 0)} m; mast ${num(r.mast_mean_ws, 2)} m/s`)}
        </div>
        ${(r.warnings || []).length ? card(`${ICON.warn}Check before relying on this`, list(r.warnings), 'warnbox') : ''}
        ${short ? card(`${ICON.warn}Fewer turbines than asked for`, `<p class="note">${r.n_placed} of ${r.n_requested} fit in the buildable area with this spacing. Loosen a buffer, widen the search, or ask for fewer.</p>`, 'warnbox') : ''}
        <a href="${fileUrl(r.map, st.result.at)}" target="_blank" rel="noopener"><img class="map" src="${fileUrl(r.map, st.result.at)}" alt="Map of the turbine layout over the wind map, with the buildable area"></a>
        ${card(`Buildable area <small>${num(r.constraints.buildable_km2, 1)} km² left</small>`, table(['Rule', 'Removed, km²', 'Left, km²'], r.constraints.steps.map(s => [esc(s.rule), num(s.removed_km2, 2), num(s.left_km2, 2)]), [1, 2]))}
        ${card('Turbines', table(['', 'Latitude', 'Longitude', 'Ground, m', 'From mast, km', 'Wind, m/s', 'Wake, %', 'Net, MWh/yr', 'CF, %', 'Uncertainty, %'],
          r.positions.map(p => [esc(p.id), num(p.lat, 5), num(p.lon, 5), num(p.ground_elev_m, 0), num(p.dist_mast_km, 1), num(p.free_ws, 2), num(p.wake_loss_pct, 1), num(p.p50_mwh, 0), num(100 * p.capacity_factor, 1), p.sigma_energy_pct == null ? '' : num(p.sigma_energy_pct, 1)]), [1, 2, 3, 4, 5, 6, 7, 8, 9])
          + ((r.beyond_radius || []).length ? `<p class="note" style="margin-top:12px">${r.beyond_radius.length} position${r.beyond_radius.length > 1 ? 's' : ''} of your file ${r.beyond_radius.length > 1 ? 'lie' : 'lies'} more than ${num(r.rules.radius_km, 0)} km from the mast and ${r.beyond_radius.length > 1 ? 'are' : 'is'} left out: ${r.beyond_radius.slice(0, 12).map(b => esc(b.name)).join(', ')}${r.beyond_radius.length > 12 ? ' and others' : ''}.</p>` : '')
          + `<div class="links" style="margin-top:12px"><a class="btn" href="${fileUrl(r.csv)}">Download the coordinates</a><a class="btn" href="${fileUrl(r.map_print)}" target="_blank" rel="noopener">Open the print map</a></div>`)}
        <div class="maps2">
          ${card('How sure is it', table(['Source of uncertainty', 'On energy, %'], Object.entries(u.terms_energy_pct).map(([k, v]) => [`<span style="white-space:normal">${esc(k)}</span>`, num(v, 1)]).concat([['Year-to-year wind, one year', num(u.iav_speed_pct * u.energy_per_speed, 1)]]), [1])
            + `<p class="hint">Combined: ${num(u.sigma_pct.long_term, 1)} % for the long-term average. One per cent more wind gives ${num(u.energy_per_speed, 2)} % more energy here.</p>`
            + (u.by_position ? `<p class="hint">Carrying the mast's wind to the turbines depends on where each stands: ${num(u.by_position.rule.base_pct, 0)} % next to the mast, ${num(u.by_position.rule.per_km_pct, 0)} % more per km away, ${num(u.by_position.rule.per_50m_height_pct, 0)} % per 50 m of ground height above or below the mast, and a quarter of the speed-up the wind map gives the position. Here from ${num(u.by_position.horizontal_range_pct_ws[0], 1)} to ${num(u.by_position.horizontal_range_pct_ws[1], 1)} % of the wind speed, ${num(u.by_position.horizontal_pct_ws, 1)} % weighted by energy. Hub at ${num(u.by_position.hub_m, 0)} m, top anemometer at ${num(u.by_position.top_m, 0)} m. Developer assumptions, not values from a standard.</p>` : ''))}
          ${card('Cross-checks', table(['Wake model', 'Wake loss, %', 'P50, GWh'], [[wrap(r.wake_model_name + ' (used)'), num(r.wake_loss_pct, 1), num(r.p50_gwh, 0)]].concat(checks), [1, 2])
            + `<p class="note" style="margin-top:12px">If no turbine were windier than the mast, P50 would be ${num(r.mast_wind_ceiling.p50_gwh, 0)} GWh (${num(r.mast_wind_ceiling.change_pct, 1)} %). That difference rests on the flow model alone.</p>`
            + (r.refinement.moves.length ? `<p class="note">Fine-tuning moved ${r.refinement.moves.length} turbine${r.refinement.moves.length > 1 ? 's' : ''} for ${num(r.refinement.gain_pct, 2)} % more energy.</p>` : ''))}
        </div>
        ${card('What the estimate rests on', list(r.notes) + `<p class="hint">${esc(r.estimate_note)}</p>`)}`;
    },
    environment(r, st) {
      const nz = r.noise, fl = r.flicker;
      const nRows = nz.receptors.slice(0, 12).map(x => [esc(x.name), num(x.level_dba, 1), num(x.level_plus_uncertainty_dba, 1), esc(x.nearest_turbine), num(x.distance_m, 0),
        x.over_limit ? state('over', 'Over') : x.over_limit_with_uncertainty ? state('near', 'Within, over with allowance') : state('ok', 'Within')]);
      const shown = fl.receptors.filter(x => x.hours_per_year > 0).slice(0, 12);
      const fRows = shown.map(x => [esc(x.name), num(x.hours_per_year, 1), num(x.max_minutes_per_day, 0), x.days_with_flicker, esc(x.turbines.join(', ')), x.over_limit ? state('over', 'Over') : state('ok', 'Within')]);
      const na = r.not_assessed, cautions = r.cautions || [];
      const gaps = (cautions.length ? card(`${ICON.warn}The screening is incomplete`, list(cautions), 'warnbox') : '')
        + (na ? card(`Homes not assessed <small>${num(na.n, 0)} of ${num(na.of, 0)}</small>`,
          `<p class="note">Outside the terrain of the wind map, the nearest ${num(na.nearest_turbine_m / 1000, 1)} km from a turbine. ${na.within_reach
            ? `${num(na.within_reach, 0)} of them ${na.within_reach === 1 ? 'is' : 'are'} near enough for noise over the level used or for shadow flicker.`
            : `All of them are more than ${num(r.reach_m / 1000, 1)} km from every turbine, beyond the reach of noise over the level used and of shadow flicker.`}</p>
           <div class="links" style="margin-top:12px"><a class="btn" href="${fileUrl(na.csv, st.result.at)}">Download the list</a></div>`) : '');
      return `${gaps}<div class="tiles">
          ${tile('Homes over the noise level', `${num(nz.over_limit, 0)} <small>of ${num(nz.n_receptors, 0)}</small>`, `${num(nz.over_limit_with_uncertainty, 0)} with a ${num(nz.uncertainty_db, 0)} dB allowance`)}
          ${tile('Highest noise level', `${num(nz.highest_dba, 1)} <small>dB(A)</small>`, `level used: ${num(nz.limit_dba, 0)} dB(A)`)}
          ${tile('Homes over a flicker limit', `${num(fl.over_limit, 0)} <small>of ${num(fl.n_receptors, 0)}</small>`, `${num(fl.with_any_flicker, 0)} could see any flicker`)}
          ${tile('Most flicker, worst case', `${num(fl.highest_hours, 0)} <small>h a year</small>`, `longest day ${num(fl.highest_minutes_day, 0)} min`)}
          ${tile('Cost of stopping for flicker', `${num(fl.shutdown_upper_bound_pct, 2)} <small>%</small>`, 'of operating time, at most')}
        </div>
        <div class="maps2">
          <a href="${fileUrl(r.noise_map, st.result.at)}" target="_blank" rel="noopener"><img class="map" src="${fileUrl(r.noise_map, st.result.at)}" alt="Map of turbine noise with the homes"></a>
          <a href="${fileUrl(r.flicker_map, st.result.at)}" target="_blank" rel="noopener"><img class="map" src="${fileUrl(r.flicker_map, st.result.at)}" alt="Map of homes within range of shadow flicker"></a>
        </div>
        ${card(`Noise at homes <small>the ${nRows.length} highest of ${num(nz.n_receptors, 0)}</small>`, table(['Home', 'Level, dB(A)', 'With allowance', 'Nearest turbine', 'Distance, m', `Against ${num(nz.limit_dba, 0)} dB(A)`], nRows, [1, 2, 4])
          + `<div class="links" style="margin-top:12px"><a class="btn" href="${fileUrl(r.noise_csv)}">Download every home</a></div><p class="hint">${esc(nz.method)}.</p>`)}
        ${card(`Shadow flicker at homes <small>${shown.length ? `the ${shown.length} highest of ${fl.with_any_flicker} affected` : 'none affected'}</small>`, (shown.length ? table(['Home', 'Hours a year', 'Longest day, min', 'Days', 'Turbines', 'Against the limits'], fRows, [1, 2, 3]) : '<p class="note">No home is within range.</p>')
          + `<div class="links" style="margin-top:12px"><a class="btn" href="${fileUrl(r.flicker_csv)}">Download every home</a></div><p class="hint">${esc(fl.method)}.</p>`)}
        ${card('Read these as a screening', list(['The flicker figures are the astronomical worst case: the sun always shines, the rotor always turns and faces the sun, and nothing stands in between. Real flicker is a fraction of it.',
          'The noise figures use one sound power level for every turbine and ignore screening by hills and buildings, which errs on the loud side. There is no background noise survey.',
          'A permit needs the studies the authority asks for, with the chosen turbine’s data.']))}`;
    },
    pack(r) {
      return `${card('The pack is ready', `<div class="links"><a class="btn primary" href="${fileUrl(r.index)}" target="_blank" rel="noopener">Open the summary page</a><button class="btn" type="button" data-act="reveal" data-path="${esc(r.folder)}">Open the pack folder</button></div>
          ${r.with_environment ? '' : `<p class="hint">Noise and shadow flicker are not in this pack: ${r.environment_out_of_date ? 'step 5 was out of date when it was built. Run step 5 again, then build the pack again.' : 'step 5 was not done.'}</p>`}`)}
        ${card(`Files <small>${r.files.length + 1}</small>`, table(['File', 'What it holds'], [[`<a href="${fileUrl(r.index)}" target="_blank" rel="noopener">index.html</a>`, 'Summary page to print or send']].concat(
          r.files.map(f => [`<a href="${fileUrl(f.file)}" target="_blank" rel="noopener">${esc(f.file.split('/').pop())}</a>`, `<span style="white-space:normal">${esc(f.what)}</span>`]))))}
        ${card('Before it goes out', list(['Turbine positions are indicative. Say so when you share them.', 'The pack supports a permit application; it does not replace the studies a permit needs.', 'Check the buffers and limits against the rules that apply at the site.']))}`;
    },
  };

  function buildingsCard() {
    const b = S.project.buildings;
    if (!b) return '';
    const src = b.sources, row = (name, s, n) => [name, s.ok ? num(s.n, 0) : 'not reached', s.ok ? num(n, 0) : '–'];
    const rows = [row('OpenStreetMap', src.osm, b.n_osm), row("Microsoft's footprints", src.microsoft, b.n_added_from_microsoft)];
    return card(`Buildings from open data <small>${num(b.n, 0)} within ${num(b.half_km, 0)} km</small>`,
      table(['Source', 'Buildings it holds', 'Taken into the project'], rows, [1, 2])
      + `<p class="hint">Every OpenStreetMap building is kept. A Microsoft footprint is added when OpenStreetMap has no building within 30 m of it. Fetched ${esc(day(b.fetched))}.</p>`
      + (b.map ? `<a href="${fileUrl(b.map, b.at)}" target="_blank" rel="noopener"><img class="map" style="margin-top:12px" src="${fileUrl(b.map, b.at)}" alt="Map of the buildings by source: OpenStreetMap, Microsoft's footprints only, and your own file"></a>` : '')
      + `<p class="hint">Neither source is a survey: they list buildings of every kind, and the footprints are machine detections. Check against imagery or on site, and put what is missing in a file of your own.</p>`
      + `<p class="hint">${(b.attribution || []).map(esc).join(' ')}</p>`)
      + ((b.warnings || []).length ? card(`${ICON.warn}Where the sources disagree`, list(b.warnings), 'warnbox') : '');
  }

  function resultsFor(key, st) {
    const running = job && job.status === 'running';
    const now = running ? (job.current || job.steps[0]) : null;
    let h = '';
    if (running && (pageOf(now) === key && (job.current || job.steps.length === 1))) return workingPanel();
    if (running) h += card('', `<p class="note">Working on another step: ${esc(STEP[now].title.toLowerCase())}.</p>`);
    if (lastError && pageOf(lastError.step) === key) h += errorPanel();
    else if (lastError && (st.redo || []).includes(lastError.step)) {      // an earlier step, run first for this one, did not finish
      h += `<section class="card alert"><h2>${ICON.cross}${esc(STEP[lastError.step].title)} did not finish</h2><p class="note">${esc(lastError.message)}</p>
        <p class="hint">It is run before this step, so this step was not run. <a href="#" data-go="${lastError.step}">Open step ${STEP[lastError.step].n}</a> for the working log.</p></section>`;
    }
    if (key === 'layout') h += buildingsCard();
    if (st.result) {
      if (st.status === 'stale') h += card(`${ICON.warn}Out of date`, `<p class="note">${esc(st.why || 'Something this step works from changed after it ran.')} What is shown below is the earlier result. Run this step again to bring it in line.</p>`, 'warnbox');
      h += RESULTS[key](st.result, st);
    } else if (!(lastError && pageOf(lastError.step) === key) && !(key === 'layout' && S.project.buildings)) {
      const m = STEP[key];
      h += `<div class="empty"><img src="/static/img/${m.photo}" alt=""><div><h2>${m.empty[0]}</h2><p>${m.empty[1]}</p></div></div>`;
    }
    return h;
  }

  function viewStep(key) {
    const st = stepOf(key), m = STEP[key];
    return `<div class="page"><header><div><div class="step">Step ${m.n} of 6</div><h1>${m.title}</h1><p>${m.why}</p></div>${pill(st)}</header>
      <div class="cols"><div class="inputs">${INPUTS[key](st)}${runCard(key, st)}${key === 'data' ? intakeCard() : ''}</div>
      <div class="results" id="results">${resultsFor(key, st)}</div></div></div>`;
  }

  function renderMain(keepScroll) {
    const y = window.scrollY;
    uid = 0;
    main.innerHTML = !S.project ? viewStart() : view === 'home' ? viewHome() : viewStep(view);
    if (keepScroll) window.scrollTo(0, y); else window.scrollTo(0, 0);
  }
  function render(keepScroll) { renderRail(); renderFoot(); renderMain(keepScroll); document.title = (S.project ? S.project.name + ' – ' : '') + 'Bohemian Wind'; }

  // ---------- runs ----------
  async function runSteps(steps) {
    lastError = null;
    try { job = await api('/api/run', { steps }); } catch (e) { toast(e.message, true); return; }
    logLines = [];
    render(true);
    poll();
  }

  async function poll() {
    if (!job) return;
    let j;
    try { j = await api(`/api/job?id=${job.id}&since=${logLines.length}`); } catch (e) { toast(e.message, true); return; }
    if (j.status === 'gone') { job = null; S = await api('/api/state'); render(true); return; }
    logLines.push(...j.log);
    const moved = j.current !== job.current || j.done.length !== job.done.length;
    job = j;
    if (j.status === 'running') {
      if (moved) { try { S = await api('/api/state'); } catch (e) { /* keep going */ } render(true); }
      else {
        const box = $('#w-log');
        if (box) { box.innerHTML = logLines.slice(-60).map(l => `<div>${esc(l)}</div>`).join(''); box.scrollTop = box.scrollHeight; }
      }
      setTimeout(poll, 600);
      return;
    }
    S = await api('/api/state');
    if (j.status === 'error') {
      const failed = j.steps[j.done.length] || j.steps[0];
      lastError = { step: failed, message: j.error, log: logLines.slice(-14) };
      toast(`${STEP[failed].title} did not finish.`, true);
    } else toast(j.steps.length > 1 ? `All ${j.steps.length} steps finished in ${num(j.seconds, 0)} s.` : `${STEP[j.steps[0]].title} finished.`);
    job = null;
    render(true);
  }

  // ---------- events ----------
  async function act(fn) { try { await fn(); } catch (e) { toast(e.message, true); } }

  // A setting can put this step and the later ones out of date. The fields are left alone, so typing is not
  // interrupted; the status, the run button and the results are drawn again.
  function refreshStatus() {
    renderRail();
    if (!S.project || !STEP[view] || view === 'buildings') return;
    const st = stepOf(view), old = main.querySelector('.page > header .pill'), runBox = $('#run-card'), res = $('#results');
    if (old) old.outerHTML = pill(st);
    if (runBox) runBox.outerHTML = runCard(view, st);
    if (res && !(job && job.status === 'running')) res.innerHTML = resultsFor(view, st);
  }

  async function saveSettings(section, values, redraw) {
    try { S = await api('/api/settings', { section, values }); }
    catch (e) { toast(e.message, true); render(true); return; }      // the field shows what is stored again
    if (redraw) render(true); else refreshStatus();
  }

  document.addEventListener('click', e => {
    const go = e.target.closest('[data-go]');
    if (go) {
      e.preventDefault();
      if (go.getAttribute('aria-disabled') === 'true') { toast('Open or create a project first.'); return; }
      view = go.dataset.go; render();
      main.focus({ preventScroll: true });
      return;
    }
    const run = e.target.closest('[data-run]');
    if (run) { runSteps((run.dataset.first ? run.dataset.first.split(',') : []).concat(run.dataset.run)); return; }
    const pick = e.target.closest('[data-pick]');
    if (pick) {
      act(async () => {
        const input = document.getElementById(pick.dataset.for);
        const r = await api('/api/pick', { kind: pick.dataset.pick, title: pick.dataset.title || 'Choose' });
        if (r.error) { toast(r.error, true); input.focus(); return; }
        if (!r.path) return;
        input.value = r.path;
        if (pick.dataset.attach) { S = await api('/api/attach', { kind: pick.dataset.attach, path: r.path }); render(true); }
        else input.dispatchEvent(new Event('change', { bubbles: true }));
      });
      return;
    }
    const a = e.target.closest('[data-act]');
    if (!a) return;
    const what = a.dataset.act;
    if (what === 'demo') act(async () => { a.disabled = true; toast('Preparing the demo project.'); S = await api('/api/project', { action: 'demo' }); view = 'home'; lastError = null; render(); });
    else if (what === 'new') act(async () => {
      S = await api('/api/project', { action: 'new', name: $('#np-name').value, folder: $('#np-folder').value, lat: $('#np-lat').value, lon: $('#np-lon').value, elevation_m: $('#np-elev').value });
      view = 'data'; lastError = null; render();
    });
    else if (what === 'open') act(async () => { S = await api('/api/project', { action: 'open', folder: $('#op-folder').value }); view = 'home'; lastError = null; render(); });
    else if (what === 'open-recent') act(async () => { S = await api('/api/project', { action: 'open', folder: a.dataset.folder }); view = 'home'; lastError = null; render(); });
    else if (what === 'close') act(async () => { S = await api('/api/project', { action: 'close' }); view = 'home'; lastError = null; render(); });
    else if (what === 'runall') runSteps(ORDER);
    else if (what === 'reveal') act(() => api('/api/reveal', { path: a.dataset.path }));
    else if (what === 'detach') act(async () => { S = await api('/api/settings', { section: 'layout', values: { [a.dataset.kind + '_file']: '' } }); render(true); });
  });

  main.addEventListener('keydown', e => {
    if (e.key !== 'Enter') return;
    const m = /^at-(\w+)$/.exec(e.target.id || '');
    if (m && e.target.value.trim()) act(async () => { S = await api('/api/attach', { kind: m[1], path: e.target.value.trim() }); render(true); });
    if (e.target.id === 'op-folder') $('[data-act="open"]').click();
  });

  main.addEventListener('change', e => {
    const el = e.target;
    if (el.id === 'watch-on' || el.id === 'watch-min' || el.id === 'watch-stale') {
      act(async () => {
        try { S = await api('/api/watch', { on: $('#watch-on').checked, minutes: $('#watch-min').value, stale_hours: $('#watch-stale').value }); }
        finally { render(true); }
        if ($('#watch-on') && $('#watch-on').checked) toast('Daily intake is on: the folder is being watched.');
      });
      return;
    }
    if (el.dataset.ch) {
      const channels = {};
      main.querySelectorAll('.chrow').forEach(row => {
        const port = row.querySelector('[data-ch="port"]').value.trim().toUpperCase();
        const h = row.querySelector('[data-ch="height"]').value, o = row.querySelector('[data-ch="offset"]').value;
        if (port && (h !== '' || o !== '')) channels[port] = { height_m: h === '' ? null : Number(h), direction_offset_deg: o === '' ? null : Number(o) };
      });
      saveSettings('data', { channels }, true);
      return;
    }
    if (el.dataset.loss !== undefined) {
      const losses = (S.project.layout.losses || []).map((x, i) => i === Number(el.dataset.loss) ? { ...x, pct: el.value === '' ? 0 : Number(el.value) } : x);
      saveSettings('layout', { losses }, true);
      return;
    }
    if (!el.dataset.s) return;
    let v = el.type === 'checkbox' ? el.checked : el.value;
    if (el.dataset.t === 'num') v = el.value === '' ? null : Number(el.value);
    const values = setPath({}, el.dataset.k, v);
    if (el.dataset.s === 'layout' && el.dataset.k === 'turbine') {
      const t = S.app.turbines.find(x => x.key === v);
      if (t) Object.assign(values, { rated_kw: t.rated_kw, rotor_m: t.rotor_m, hub_m: t.hub_m });
    }
    saveSettings(el.dataset.s, values, !!el.dataset.rerender);
  });

  // tooltips: hover and keyboard focus show the same thing
  function showTip(el, x, y) {
    tip.innerHTML = el.dataset.tip;      // built by this file from escaped values
    tip.hidden = false;
    const w = tip.offsetWidth, h = tip.offsetHeight;
    tip.style.left = Math.max(8, Math.min(window.innerWidth - w - 8, x + 14)) + 'px';
    tip.style.top = Math.max(8, Math.min(window.innerHeight - h - 8, y - h - 12)) + 'px';
  }
  document.addEventListener('mousemove', e => { const el = e.target.closest && e.target.closest('[data-tip]'); if (el) showTip(el, e.clientX, e.clientY); else tip.hidden = true; });
  document.addEventListener('focusin', e => { const el = e.target.closest && e.target.closest('[data-tip]'); if (el) { const b = el.getBoundingClientRect(); showTip(el, b.left + b.width / 2, b.top + 8); } });
  document.addEventListener('focusout', () => { tip.hidden = true; });

  // keep the daily-intake status fresh while idle
  setInterval(async () => {
    if (job || !S || !S.project || document.hidden || !(S.project.intake || {}).watching) return;
    if (document.activeElement && ['INPUT', 'SELECT'].includes(document.activeElement.tagName)) return;
    try { S = await api('/api/state'); render(true); } catch (e) { /* the next tick tries again */ }
  }, 30000);

  // Started from the Start menu, the application stops a few seconds after its last window is closed: tell it.
  // (A reload says goodbye too, and is back before the application has stopped.)
  window.addEventListener('pagehide', () => {
    try { navigator.sendBeacon('/api/bye', new Blob([JSON.stringify({ token: TOKEN, page: PAGE })], { type: 'application/json' })); } catch (e) { /* nothing to tell */ }
  });

  // ---------- start ----------
  (async () => {
    try { S = await api('/api/state'); } catch (e) { main.innerHTML = `<p class="loading">${esc(e.message)}</p>`; return; }
    if (S.job) { job = S.job; logLines = []; }
    render();
    if (job) poll();
  })();
})();
