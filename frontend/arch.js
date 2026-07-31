/* Architecture diagram — mirrors docs/architecture.svg: three lanes
   (SERVE / DATA & CACHE / SCHEDULED). Nodes carry live health from
   /api/health; edges are explicit polylines (single source of truth —
   tests/test_arch_geometry.py parses NODES/EDGES from this file and
   verifies no edge crosses another or passes through an unrelated node).
   Flow lines animate via CSS dash offset (off under reduced motion). */

'use strict';

const Arch = (() => {
  const NS = 'http://www.w3.org/2000/svg';
  const el = (tag, attrs = {}) => {
    const n = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    return n;
  };

  /* viewBox 1000x470. Lanes: A y=64 (serve), B y=224 (data & cache),
     C y=364 (scheduled). All positions mirror docs/architecture.svg. */
  const NODES = [
    { id: 'browser', x: 34,  y: 64,  w: 130, h: 56, label: 'Browser SPA', sub: '11 tabs, no framework', health: null },
    { id: 'cf',      x: 240, y: 64,  w: 134, h: 56, label: 'CloudFront', sub: 'OAC + CSP/HSTS', health: null, acc: true },
    { id: 'apigw',   x: 450, y: 64,  w: 134, h: 56, label: 'API Gateway', sub: 'JWT authorizer', health: null },
    { id: 'lambda',  x: 660, y: 64,  w: 130, h: 56, label: 'Lambda API', sub: 'named queries only', health: 'api_lambda', acc: true },

    { id: 'cognito', x: 93,  y: 224, w: 134, h: 56, label: 'Cognito', sub: 'admin-only + PKCE', health: null },
    { id: 's3lake',  x: 240, y: 224, w: 134, h: 56, label: 'S3 data lake', sub: 'Kiro telemetry + policy', health: 'prompt_logs' },
    { id: 'athena',  x: 450, y: 224, w: 134, h: 56, label: 'Athena + Glue', sub: 'governance views', health: 'user_reports' },
    { id: 's3cache', x: 660, y: 224, w: 130, h: 56, label: 'S3 cache', sub: 'materialized, ~1.4s loads', health: 'policy_registry', acc: true },

    { id: 'dora',    x: 240, y: 364, w: 134, h: 56, label: 'dora-sync λ', sub: 'GitHub PRs, hourly', health: null },
    { id: 'warmer',  x: 450, y: 364, w: 134, h: 56, label: 'cache warmer λ', sub: 'all tabs, every 15 min', health: null },
    { id: 'scanner', x: 660, y: 364, w: 240, h: 56, label: 'governance scanner λ', sub: 'keywords · after-hours · budget', health: 'identity_mapping' },
    { id: 'sns',     x: 806, y: 296, w: 112, h: 48, label: 'SNS', sub: 'alerts out', health: null, acc: true },
  ];

  /* Edges as explicit polylines: { pts: [[x,y]...], kind } — kinds map to
     CSS classes (flow = blue user path, flow2 = green data/auth path,
     alert = orange alert path). */
  const EDGES = [
    { pts: [[164, 92], [240, 92]],   kind: 'flow' },   // browser -> cf
    { pts: [[374, 92], [450, 92]],   kind: 'flow' },   // cf -> apigw
    { pts: [[584, 92], [660, 92]],   kind: 'flow' },   // apigw -> lambda
    { pts: [[275, 120], [275, 160], [160, 160], [160, 224]], kind: 'flow2' }, // cf -> cognito (auth)
    { pts: [[725, 120], [725, 224]], kind: 'flow' },   // lambda -> s3 cache
    { pts: [[374, 252], [450, 252]], kind: 'flow2' },  // s3 lake -> athena
    { pts: [[584, 252], [660, 252]], kind: 'flow2' },  // athena -> s3 cache
    { pts: [[307, 364], [307, 280]], kind: 'flow2' },  // dora-sync -> s3 lake
    { pts: [[517, 364], [517, 280]], kind: 'flow2' },  // warmer -> athena
    { pts: [[780, 364], [780, 320], [806, 320]], kind: 'alert' }, // scanner -> sns
  ];

  const LANES = [
    { y: 52,  label: 'SERVE (user path — never waits on Athena)' },
    { y: 212, label: 'DATA & CACHE' },
    { y: 352, label: 'SCHEDULED (EventBridge)' },
  ];

  function render(container, health) {
    const box = document.createElement('div');
    box.className = 'chart-box arch';
    const svg = el('svg', { viewBox: '0 0 1000 470', role: 'img' });

    const defs = el('defs');
    const marker = el('marker', {
      id: 'arrow', viewBox: '0 0 8 8', refX: 7, refY: 4,
      markerWidth: 7, markerHeight: 7, orient: 'auto-start-reverse',
    });
    marker.appendChild(el('path', { d: 'M0,0 L8,4 L0,8 z', fill: 'var(--text-muted)' }));
    defs.appendChild(marker);
    svg.appendChild(defs);

    for (const lane of LANES) {
      const t = el('text', { x: 24, y: lane.y, class: 'arch-lane' });
      t.textContent = lane.label;
      svg.appendChild(t);
    }

    for (const e of EDGES) {
      const d = e.pts.map((p, i) => `${i ? 'L' : 'M'}${p[0]},${p[1]}`).join(' ');
      svg.appendChild(el('path', {
        d, class: `arch-edge arch-${e.kind}`, 'marker-end': 'url(#arrow)',
      }));
    }

    const tooltip = document.getElementById('tooltip');
    for (const n of NODES) {
      const g = el('g', { class: 'arch-node', tabindex: 0 });
      const status = n.health && health ? health[n.health] : null;
      const ok = status ? status.ok : null;
      g.appendChild(el('rect', {
        x: n.x, y: n.y, width: n.w, height: n.h, rx: 10,
        class: 'arch-rect' + (n.acc ? ' arch-acc' : '') + (ok === false ? ' arch-bad' : ''),
      }));
      const midY = n.y + n.h / 2;
      const t1 = el('text', { x: n.x + n.w / 2, y: midY - 4, 'text-anchor': 'middle', class: 'arch-label' });
      t1.textContent = n.label;
      const t2 = el('text', { x: n.x + n.w / 2, y: midY + 14, 'text-anchor': 'middle', class: 'arch-sub' });
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

  // exposed for the offline geometry test
  return { render, _layout: { NODES, EDGES } };
})();
