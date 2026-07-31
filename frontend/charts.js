/* Minimal dependency-free SVG chart layer.
   Follows the dataviz method: recessive hairline grid, 2px lines with ≥8px
   hover targets, 4px rounded bar data-ends anchored to the baseline, 2px
   surface gaps between adjacent fills, categorical hues in fixed slot order
   (never cycled — >8 folds to "Other"), donut capped at 3 slices + Other
   (all-pairs validation cap), one axis per chart, crosshair+tooltip on
   line/bar, per-arc tooltip on donut, legend for ≥2 series only. */

'use strict';

const Charts = (() => {
  const NS = 'http://www.w3.org/2000/svg';
  const SERIES = ['--series-1','--series-2','--series-3','--series-4',
                  '--series-5','--series-6','--series-7','--series-8'];
  const W = 560, H = 260, M = { top: 12, right: 16, bottom: 28, left: 44 };
  const tooltipEl = () => document.getElementById('tooltip');

  const cssVar = (name) =>
    getComputedStyle(document.documentElement).getPropertyValue(name).trim() || '#888';
  const seriesColor = (i) => `var(${SERIES[Math.min(i, SERIES.length - 1)]})`;

  const el = (tag, attrs = {}) => {
    const node = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    return node;
  };

  /* All data-derived strings pass through esc() before entering tooltip HTML —
     telemetry values (usernames, team names, keywords) are untrusted. */
  const esc = (s) => String(s).replace(/[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  const fmt = (v) => {
    const n = Number(v);
    if (!isFinite(n)) return '—';
    if (Math.abs(n) >= 1e6) return (n / 1e6).toFixed(1) + 'M';
    if (Math.abs(n) >= 1e4) return (n / 1e3).toFixed(1) + 'k';
    return Number.isInteger(n) ? n.toLocaleString() : n.toFixed(2);
  };

  function showTooltip(evt, html) {
    const t = tooltipEl();
    t.innerHTML = html;
    t.hidden = false;
    const pad = 12;
    let x = evt.clientX + pad, y = evt.clientY + pad;
    const r = t.getBoundingClientRect();
    if (x + r.width > innerWidth - 8) x = evt.clientX - r.width - pad;
    if (y + r.height > innerHeight - 8) y = evt.clientY - r.height - pad;
    t.style.left = x + 'px'; t.style.top = y + 'px';
  }
  function hideTooltip() { tooltipEl().hidden = true; }

  function scaffold(container) {
    const box = document.createElement('div');
    box.className = 'chart-box';
    const svg = el('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img' });
    box.appendChild(svg);
    container.appendChild(box);
    return svg;
  }

  function axes(svg, yTicks, yScale, xLabels, xPos) {
    for (const t of yTicks) {
      const y = yScale(t);
      svg.appendChild(el('line', { x1: M.left, x2: W - M.right, y1: y, y2: y, class: 'gridline' }));
      const label = el('text', { x: M.left - 6, y: y + 3, 'text-anchor': 'end' });
      label.textContent = fmt(t);
      svg.appendChild(label);
    }
    svg.appendChild(el('line', {
      x1: M.left, x2: W - M.right, y1: H - M.bottom, y2: H - M.bottom, class: 'axis-line',
    }));
    const maxLabels = 6;
    const step = Math.max(1, Math.ceil(xLabels.length / maxLabels));
    xLabels.forEach((lab, i) => {
      if (i % step !== 0 && i !== xLabels.length - 1) return;
      const label = el('text', { x: xPos(i), y: H - M.bottom + 16, 'text-anchor': 'middle' });
      label.textContent = String(lab).slice(5); // yyyy-mm-dd -> mm-dd
      svg.appendChild(label);
    });
  }

  function niceTicks(max) {
    if (max <= 0) return [0, 1];
    const raw = max / 4;
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 5, 10].map(s => s * mag).find(s => s >= raw);
    const ticks = [];
    for (let t = 0; t <= max + step * 0.001; t += step) ticks.push(t);
    return ticks;
  }

  /* rows: [[date, value], ...] */
  function line(container, rows, { name = '', unit = '' } = {}) {
    if (!rows.length) return empty(container);
    const svg = scaffold(container);
    const vals = rows.map(r => Number(r[1]) || 0);
    const max = Math.max(...vals, 1);
    const ticks = niceTicks(max);
    const yMax = ticks[ticks.length - 1];
    const x = (i) => rows.length === 1
      ? (M.left + W - M.right) / 2
      : M.left + (i / (rows.length - 1)) * (W - M.left - M.right);
    const y = (v) => H - M.bottom - (v / yMax) * (H - M.top - M.bottom);
    axes(svg, ticks, y, rows.map(r => r[0]), x);

    const d = vals.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join('');
    svg.appendChild(el('path', { d, fill: 'none', stroke: seriesColor(0), 'stroke-width': 2, 'stroke-linejoin': 'round' }));

    // invisible wide hit band per point + crosshair
    const cross = el('line', { y1: M.top, y2: H - M.bottom, class: 'gridline', 'stroke-dasharray': '3 3', visibility: 'hidden' });
    svg.appendChild(cross);
    const dot = el('circle', { r: 4, fill: seriesColor(0), stroke: cssVar('--surface-1'), 'stroke-width': 2, visibility: 'hidden' });
    svg.appendChild(dot);
    const band = (W - M.left - M.right) / Math.max(rows.length - 1, 1);
    rows.forEach((r, i) => {
      const hit = el('rect', {
        x: x(i) - band / 2, y: M.top, width: band, height: H - M.top - M.bottom,
        fill: 'transparent',
      });
      hit.addEventListener('mousemove', (evt) => {
        cross.setAttribute('x1', x(i)); cross.setAttribute('x2', x(i));
        cross.setAttribute('visibility', 'visible');
        dot.setAttribute('cx', x(i)); dot.setAttribute('cy', y(vals[i]));
        dot.setAttribute('visibility', 'visible');
        showTooltip(evt, `<div class="t-title">${esc(r[0])}</div><div class="t-row"><span>${esc(name)}</span><b>${fmt(vals[i])}${unit}</b></div>`);
      });
      hit.addEventListener('mouseleave', () => {
        cross.setAttribute('visibility', 'hidden');
        dot.setAttribute('visibility', 'hidden');
        hideTooltip();
      });
      svg.appendChild(hit);
    });
  }

  /* rows: [[label, value], ...] — vertical bars, time-ish x axis */
  function bar(container, rows, { name = '', color = 0 } = {}) {
    if (!rows.length) return empty(container);
    const svg = scaffold(container);
    const vals = rows.map(r => Number(r[1]) || 0);
    const ticks = niceTicks(Math.max(...vals, 1));
    const yMax = ticks[ticks.length - 1];
    const slot = (W - M.left - M.right) / rows.length;
    const bw = Math.max(Math.min(slot - 2, 40), 2);            // 2px surface gap between bars
    const x = (i) => M.left + i * slot + slot / 2;
    const y = (v) => H - M.bottom - (v / yMax) * (H - M.top - M.bottom);
    axes(svg, ticks, y, rows.map(r => r[0]), x);

    rows.forEach((r, i) => {
      const h = Math.max(H - M.bottom - y(vals[i]), 0);
      const rx = Math.min(4, bw / 2);
      // rounded top (data end), square base anchored to the baseline
      const path = h <= rx
        ? el('rect', { x: x(i) - bw / 2, y: y(vals[i]), width: bw, height: h, fill: seriesColor(color) })
        : el('path', {
            d: `M${x(i) - bw / 2},${H - M.bottom} v${-(h - rx)} q0,${-rx} ${rx},${-rx} h${bw - 2 * rx} q${rx},0 ${rx},${rx} v${h - rx} z`,
            fill: seriesColor(color),
          });
      path.addEventListener('mousemove', (evt) =>
        showTooltip(evt, `<div class="t-title">${esc(r[0])}</div><div class="t-row"><span>${esc(name)}</span><b>${fmt(vals[i])}</b></div>`));
      path.addEventListener('mouseleave', hideTooltip);
      svg.appendChild(path);
    });
  }

  /* rows: [[category, value], ...] — horizontal bars for categorical rollups.
     Single-measure chart: bars share slot-1 color; identity lives in row labels
     (color-by-rank is an anti-pattern). */
  function hbar(container, rows, { name = '' } = {}) {
    if (!rows.length) return empty(container);
    const top = rows.slice(0, 10);
    const rowH = 26, chartH = M.top + top.length * rowH + 8;
    const box = document.createElement('div');
    box.className = 'chart-box';
    const svg = el('svg', { viewBox: `0 0 ${W} ${chartH}`, role: 'img' });
    box.appendChild(svg);
    container.appendChild(box);

    const vals = top.map(r => Number(r[1]) || 0);
    const max = Math.max(...vals, 1);
    const labelW = 150;
    const x = (v) => labelW + (v / max) * (W - labelW - M.right - 60);

    top.forEach((r, i) => {
      const cy = M.top + i * rowH + rowH / 2;
      const label = el('text', { x: labelW - 8, y: cy + 4, 'text-anchor': 'end' });
      label.textContent = String(r[0]).length > 22 ? String(r[0]).slice(0, 21) + '…' : r[0];
      svg.appendChild(label);
      const w = Math.max(x(vals[i]) - labelW, 1);
      const rx = Math.min(4, w / 2);
      const barEl = el('path', {
        d: `M${labelW},${cy - 8} h${w - rx} q${rx},0 ${rx},${rx} v${16 - 2 * rx} q0,${rx} ${-rx},${rx} h${-(w - rx)} z`,
        fill: seriesColor(0),
      });
      barEl.addEventListener('mousemove', (evt) =>
        showTooltip(evt, `<div class="t-title">${esc(r[0])}</div><div class="t-row"><span>${esc(name)}</span><b>${fmt(vals[i])}</b></div>`));
      barEl.addEventListener('mouseleave', hideTooltip);
      svg.appendChild(barEl);
      const valLabel = el('text', { x: x(vals[i]) + 6, y: cy + 4, class: 'direct-label' });
      valLabel.textContent = fmt(vals[i]);
      svg.appendChild(valLabel);
    });
  }

  /* rows: [[category, value], ...] — donut, ≤3 slices + Other (all-pairs cap). */
  function donut(container, rows, { name = '' } = {}) {
    rows = rows.filter(r => Number(r[1]) > 0);
    if (!rows.length) return empty(container);
    let slices = rows.slice(0, 3);
    const rest = rows.slice(3).reduce((s, r) => s + Number(r[1]), 0);
    if (rest > 0) slices = [...slices, ['Other', rest]];

    const size = 210, cx = size / 2, cy = size / 2, R = 88, r0 = 54;
    const total = slices.reduce((s, r) => s + Number(r[1]), 0);
    const box = document.createElement('div');
    box.className = 'chart-box';
    box.style.display = 'flex';
    box.style.alignItems = 'center';
    box.style.gap = '18px';
    const svg = el('svg', { viewBox: `0 0 ${size} ${size}`, role: 'img', style: 'max-width:min(300px, 45%)' });
    box.appendChild(svg);

    const MUTED = 'var(--text-muted)';    // "Other" is not a series — neutral ink
    let angle = -Math.PI / 2;
    slices.forEach((s, i) => {
      const frac = Number(s[1]) / total;
      const a2 = angle + frac * 2 * Math.PI;
      const large = frac > 0.5 ? 1 : 0;
      const p = (a, rad) => [cx + rad * Math.cos(a), cy + rad * Math.sin(a)];
      const [x1, y1] = p(angle, R), [x2, y2] = p(a2, R);
      const [x3, y3] = p(a2, r0), [x4, y4] = p(angle, r0);
      const isOther = s[0] === 'Other' && i === slices.length - 1 && rest > 0;
      const arc = el('path', {
        d: `M${x1},${y1} A${R},${R} 0 ${large} 1 ${x2},${y2} L${x3},${y3} A${r0},${r0} 0 ${large} 0 ${x4},${y4} z`,
        fill: isOther ? MUTED : seriesColor(i),
        stroke: 'var(--surface-1)', 'stroke-width': 2,   // 2px surface gap between segments
      });
      const pct = (frac * 100).toFixed(1);
      arc.addEventListener('mousemove', (evt) =>
        showTooltip(evt, `<div class="t-title">${esc(s[0])}</div><div class="t-row"><span>${esc(name)}</span><b>${fmt(s[1])} (${pct}%)</b></div>`));
      arc.addEventListener('mouseleave', hideTooltip);
      svg.appendChild(arc);
      angle = a2;
    });

    const legend = document.createElement('div');
    legend.className = 'legend';
    legend.style.flexDirection = 'column';
    legend.style.gap = '6px';
    slices.forEach((s, i) => {
      const isOther = s[0] === 'Other' && rest > 0 && i === slices.length - 1;
      const item = document.createElement('span');
      const sw = document.createElement('span');
      sw.className = 'swatch';
      sw.style.background = isOther ? cssVar('--text-muted') : seriesColor(i);
      item.appendChild(sw);
      item.appendChild(document.createTextNode(`${s[0]} — ${fmt(s[1])}`));
      legend.appendChild(item);
    });
    box.appendChild(legend);
    container.appendChild(box);
  }

  function table(container, columns, rows, opts = {}) {
    const wrap = document.createElement('div');
    wrap.className = 'table-scroll';
    const t = document.createElement('table');
    t.className = 'data';
    const thead = document.createElement('thead');
    const hr = document.createElement('tr');
    for (const c of columns) {
      const th = document.createElement('th');
      th.textContent = c;
      hr.appendChild(th);
    }
    thead.appendChild(hr);
    t.appendChild(thead);
    const tbody = document.createElement('tbody');
    for (const row of rows) {
      const tr = document.createElement('tr');
      row.forEach((cell, i) => {
        const td = document.createElement('td');
        if (opts.pills && opts.pills[i]) {
          const span = document.createElement('span');
          span.className = 'pill ' + (opts.pills[i](cell) || '');
          span.textContent = cell ?? '—';
          td.appendChild(span);
        } else {
          td.textContent = cell ?? '—';   // textContent only — no HTML injection
          if (!isNaN(Number(cell)) && cell !== null && cell !== '') td.className = 'num';
        }
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    }
    t.appendChild(tbody);
    wrap.appendChild(t);
    container.appendChild(wrap);
    if (!rows.length) empty(container, 'No matching records in this window.');
  }

  function tile(container, label, value, flag) {
    const v = document.createElement('div');
    v.className = 'value';
    v.textContent = value;
    const l = document.createElement('div');
    l.className = 'label';
    l.textContent = label;
    container.appendChild(l);
    container.appendChild(v);
    if (flag) {
      const f = document.createElement('div');
      f.className = 'flag ' + flag.kind;
      f.textContent = flag.text;
      container.appendChild(f);
    }
  }

  function empty(container, msg = 'No data in this window.') {
    const d = document.createElement('div');
    d.className = 'empty';
    d.textContent = msg;
    container.appendChild(d);
  }

  return { line, bar, hbar, donut, table, tile, empty };
})();
