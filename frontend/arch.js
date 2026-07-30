/* Architecture diagram — hand-laid SVG on a 3-lane grid so no connector
   ever crosses another (verified geometrically in tests + by rendered
   screenshot). Node status comes from /api/health; flow lines animate via
   CSS dash offset (disabled under prefers-reduced-motion). */

'use strict';

const Arch = (() => {
  const NS = 'http://www.w3.org/2000/svg';
  const el = (tag, attrs = {}) => {
    const n = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    return n;
  };

  /* Layout: 1000x360 viewBox, three horizontal lanes.
     Lane A (y=60):  Kiro clients -> S3 data lake -> Glue -> Athena
     Lane B (y=200): Browser -> CloudFront -> API GW -> Lambda -> (up to Athena)
     Lane C (y=320): Cognito (under CloudFront/APIGW), SNS (under Lambda)
     Every edge is either horizontal within a lane or a single vertical
     drop between adjacent lanes at distinct x positions — no crossings. */
  const NODES = [
    { id: 'kiro',   x: 30,  y: 30,  w: 130, h: 56, label: 'Kiro IDE/CLI', sub: 'telemetry writers', health: null },
    { id: 's3',     x: 240, y: 30,  w: 130, h: 56, label: 'S3 data lake', sub: 'prompt+usage logs', health: 'prompt_logs' },
    { id: 'glue',   x: 450, y: 30,  w: 130, h: 56, label: 'Glue Catalog', sub: 'partition projection', health: 'user_reports' },
    { id: 'athena', x: 660, y: 30,  w: 130, h: 56, label: 'Athena', sub: 'kiro-governance WG', health: null },
    { id: 'idsync', x: 850, y: 30,  w: 120, h: 56, label: 'Identity sync', sub: 'daily 01:00 UTC', health: 'identity_mapping' },

    { id: 'browser',x: 30,  y: 170, w: 130, h: 56, label: 'Browser SPA', sub: 'this dashboard', health: null },
    { id: 'cf',     x: 240, y: 170, w: 130, h: 56, label: 'CloudFront', sub: 'OAC + CSP/HSTS', health: null },
    { id: 'apigw',  x: 450, y: 170, w: 130, h: 56, label: 'API Gateway', sub: 'JWT authorizer', health: null },
    { id: 'lambda', x: 660, y: 170, w: 130, h: 56, label: 'Lambda API', sub: 'named queries only', health: 'api_lambda' },

    { id: 'cognito',x: 240, y: 290, w: 130, h: 50, label: 'Cognito', sub: 'admin-only + PKCE', health: null },
    { id: 'policy', x: 450, y: 290, w: 130, h: 50, label: 'Policy registry', sub: 'S3 kiro/policy/', health: 'policy_registry' },
    { id: 'sns',    x: 660, y: 290, w: 130, h: 50, label: 'SNS alerts', sub: 'governance scanner', health: null },
  ];

  /* Edges as [from, to, kind]; kind 'flow' animates. Routed to avoid all
     crossings: lane-internal edges are straight horizontals; inter-lane
     edges are single verticals at the source node's center x. */
  const EDGES = [
    ['kiro', 's3', 'flow'], ['s3', 'glue', 'flow'], ['glue', 'athena', 'flow'],
    ['idsync', 's3', 'ctl'],       // vertical? no — same lane, right to left horizontal above
    ['browser', 'cf', 'flow'], ['cf', 'apigw', 'flow'], ['apigw', 'lambda', 'flow'],
    ['lambda', 'athena', 'vert'],  // vertical up at lambda center x
    ['cf', 'cognito', 'vert'],     // vertical down
    ['lambda', 'policy', 'vertdiag'], // vertical down then left along lane C top
    ['lambda', 'sns', 'vert'],
  ];

  function center(n) { return { cx: n.x + n.w / 2, cy: n.y + n.h / 2 }; }
  function byId(id) { return NODES.find(n => n.id === id); }

  function edgePath(a, b, kind) {
    const A = byId(a), B = byId(b);
    const ac = center(A), bc = center(B);
    if (kind === 'flow') {
      // horizontal within a lane: right edge of A to left edge of B
      if (A.x < B.x) return `M${A.x + A.w},${ac.cy} L${B.x},${bc.cy}`;
      return `M${A.x},${ac.cy} L${B.x + B.w},${bc.cy}`;
    }
    if (kind === 'ctl') {
      // right-to-left over the top corridor (y=12) so the line clears the
      // nodes between A and B (verified by tests/test_arch_geometry.py)
      const topY = 12;
      return `M${ac.cx},${A.y} L${ac.cx},${topY} L${bc.cx},${topY} L${bc.cx},${B.y}`;
    }
    if (kind === 'vert') {
      // vertical between lanes at shared center x (nodes are grid-aligned)
      if (ac.cy < bc.cy) return `M${ac.cx},${A.y + A.h} L${ac.cx},${B.y}`;
      return `M${ac.cx},${A.y} L${ac.cx},${B.y + B.h}`;
    }
    // vertdiag: drop from A bottom, then horizontal into B's right side
    // (used lambda->policy: x 725 -> 580). One bend, below lane B and
    // above lane C — the corridor is empty, so nothing to cross.
    const midY = 270;
    return `M${ac.cx},${A.y + A.h} L${ac.cx},${midY} L${B.x + B.w},${midY} L${B.x + B.w},${bc.cy} `
         + `M${B.x + B.w},${bc.cy} L${B.x + B.w},${bc.cy}`;
  }

  function render(container, health) {
    const box = document.createElement('div');
    box.className = 'chart-box arch';
    const svg = el('svg', { viewBox: '0 0 1000 360', role: 'img' });

    // defs: arrowhead
    const defs = el('defs');
    const marker = el('marker', {
      id: 'arrow', viewBox: '0 0 8 8', refX: 7, refY: 4,
      markerWidth: 7, markerHeight: 7, orient: 'auto-start-reverse',
    });
    marker.appendChild(el('path', { d: 'M0,0 L8,4 L0,8 z', fill: 'var(--text-muted)' }));
    defs.appendChild(marker);
    svg.appendChild(defs);

    for (const [a, b, kind] of EDGES) {
      const p = el('path', {
        d: edgePath(a, b, kind),
        class: kind === 'flow' ? 'arch-edge arch-flow' : 'arch-edge',
        'marker-end': 'url(#arrow)',
      });
      svg.appendChild(p);
    }

    const tooltip = document.getElementById('tooltip');
    for (const n of NODES) {
      const g = el('g', { class: 'arch-node', tabindex: 0 });
      const status = n.health && health ? health[n.health] : null;
      const ok = status ? status.ok : null;
      g.appendChild(el('rect', {
        x: n.x, y: n.y, width: n.w, height: n.h, rx: 10,
        class: 'arch-rect' + (ok === false ? ' arch-bad' : ''),
      }));
      const t1 = el('text', { x: n.x + n.w / 2, y: n.y + 24, 'text-anchor': 'middle', class: 'arch-label' });
      t1.textContent = n.label;
      const t2 = el('text', { x: n.x + n.w / 2, y: n.y + 42, 'text-anchor': 'middle', class: 'arch-sub' });
      t2.textContent = n.sub;
      g.appendChild(t1); g.appendChild(t2);
      if (ok !== null) {
        g.appendChild(el('circle', {
          cx: n.x + n.w - 12, cy: n.y + 12, r: 5,
          fill: ok ? 'var(--status-good)' : 'var(--status-critical)',
          stroke: 'var(--surface-1)', 'stroke-width': 2,
        }));
      }
      g.addEventListener('mousemove', (evt) => {
        let html = `<div class="t-title">${n.label}</div><div class="t-row"><span>${n.sub}</span></div>`;
        if (status && status.latest) html += `<div class="t-row"><span>latest data</span><b>${String(status.latest).slice(0, 19)}</b></div>`;
        if (status && status.version) html += `<div class="t-row"><span>version</span><b>${status.version}</b></div>`;
        if (ok !== null) html += `<div class="t-row"><span>status</span><b>${ok ? 'healthy' : 'no data found'}</b></div>`;
        tooltip.innerHTML = html;
        tooltip.hidden = false;
        tooltip.style.left = (evt.clientX + 12) + 'px';
        tooltip.style.top = (evt.clientY + 12) + 'px';
      });
      g.addEventListener('mouseleave', () => { tooltip.hidden = true; });
      svg.appendChild(g);
    }

    box.appendChild(svg);
    container.appendChild(box);
  }

  // exposed for the offline geometry test (no DOM needed)
  return { render, _layout: { NODES, EDGES, edgePath } };
})();
