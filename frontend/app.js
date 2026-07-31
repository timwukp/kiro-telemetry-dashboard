/* Kiro Telemetry Dashboard — app shell: tabs, range, theme, rendering. */

'use strict';

(() => {
  const state = { tab: 'intro', days: 30, loading: false };

  // Tabs that don't use the /api/{tab}?days=N shape.
  const SPECIAL_PATH = {
    architecture: '/api/health',
    policy: '/api/policy',
  };
  // Tabs that render without any API call.
  const LOCAL_TABS = new Set(['intro']);

  const $ = (sel) => document.querySelector(sel);
  const panel = () => $('#panel');

  /* ---------------- theme ---------------- */
  function initTheme() {
    const saved = sessionStorage.getItem('ktd.theme');
    if (saved) document.documentElement.dataset.theme = saved;
    $('#theme-toggle').addEventListener('click', () => {
      const cur = document.documentElement.dataset.theme
        || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
      const next = cur === 'dark' ? 'light' : 'dark';
      document.documentElement.dataset.theme = next;
      sessionStorage.setItem('ktd.theme', next);
      render();   // charts read CSS vars at draw time
    });
  }

  /* ---------------- data ---------------- */
  const cache = new Map();   // `${tab}:${days}` -> payload
  let inFlight = null;

  async function load() {
    const special = SPECIAL_PATH[state.tab];
    const key = special ? state.tab : `${state.tab}:${state.days}`;
    if (cache.has(key)) return cache.get(key);
    $('#loading').hidden = false;
    const path = special || `/api/${state.tab}?days=${state.days}`;
    try {
      // API Gateway cuts the connection at ~29s while the Lambda keeps
      // running the Athena queries and caches the finished result — so a
      // 503/504 on cold load usually succeeds on retry. Retry up to 3
      // times with backoff before surfacing the error.
      let data, lastErr;
      for (let attempt = 1; attempt <= 4; attempt++) {
        try {
          inFlight = Auth.apiFetch(path);
          data = await inFlight;
          lastErr = null;
          break;
        } catch (err) {
          lastErr = err;
          if (!/API (503|504|500)/.test(err.message) || attempt === 4) throw err;
          $('#loading').querySelector('span').textContent =
            `Athena warming up — retrying (${attempt}/3)…`;
          await new Promise(r => setTimeout(r, 4000 * attempt));
        }
      }
      if (lastErr) throw lastErr;
      // policy is mutable — don't cache it; health caches for the session
      if (data && state.tab !== 'policy') cache.set(key, data);
      return data;
    } finally {
      $('#loading').hidden = true;
      $('#loading').querySelector('span').textContent = 'Querying Athena…';
      inFlight = null;
    }
  }

  function rowsOf(data, key) {
    const r = data?.results?.[key];
    return r ? r.rows : [];
  }

  function card(title, sub, span) {
    const c = document.createElement('div');
    c.className = 'card' + (span ? ' ' + span : '');
    const h = document.createElement('h3');
    h.textContent = title;
    c.appendChild(h);
    if (sub) {
      const p = document.createElement('p');
      p.className = 'sub';
      p.textContent = sub;
      c.appendChild(p);
    }
    panel().appendChild(c);
    return c;
  }

  function tileCard(label, value, flag) {
    const c = document.createElement('div');
    c.className = 'card tile';
    Charts.tile(c, label, value, flag);
    panel().appendChild(c);
  }

  const fmtN = (v) => v == null ? '—' : Number(v).toLocaleString(undefined, { maximumFractionDigits: 1 });

  /* ---------------- per-tab renderers ---------------- */
  const RENDERERS = {
    intro() {
      const c = card('Introduction · narrated tour (~5 min)',
        'What this system is, why it exists, and how it works — animated, with multilingual voice-over (Amazon Polly).',
        'full');
      Intro.render(c);
    },

    architecture(data) {
      const c = card('System architecture', 'Live pipeline — hover a node for status. Data flows left to right.', 'full');
      Arch.render(c, data);
      const facts = card('Component status', 'From /api/health (S3 freshness probes)', 'full');
      const rows = [];
      for (const [name, s] of Object.entries(data)) {
        if (typeof s === 'object' && s !== null && 'ok' in s) {
          rows.push([name.replace(/_/g, ' '), s.ok ? 'OK' : 'MISSING',
                     s.latest || s.modified || '—', s.version ? `v${s.version}` : '']);
        }
      }
      Charts.table(facts, ['Component', 'Status', 'Latest data / modified', 'Version'], rows);
    },

    productivity(data) {
      Charts.line(card('AI-generated code lines per day', 'Chat + inline, from by_user_analytic'),
        rowsOf(data, 'prod_ai_code_lines_daily'), { name: 'lines' });
      Charts.line(card('Inline suggestion acceptance rate', '% accepted of suggestions shown'),
        rowsOf(data, 'prod_inline_acceptance_daily'), { name: '%', unit: '%' });
      Charts.donut(card('AI output by source', 'Where accepted AI code comes from'),
        rowsOf(data, 'prod_output_mix'), { name: 'lines' });
      Charts.hbar(card('Code lines per credit', 'ROI proxy: AI lines produced per credit consumed'),
        rowsOf(data, 'prod_lines_per_credit'), { name: 'lines/credit' });
      Charts.hbar(card('AI code lines by user', 'Top contributors in window'),
        rowsOf(data, 'prod_user_summary'), { name: 'lines' });
    },

    budget(data) {
      const mtd = rowsOf(data, 'budget_mtd')[0] || [];
      const [credits, overage, cap, activeDays, dayOfMonth, daysInMonth] = mtd.map(Number);
      // Linear extrapolation, labeled as such (not ML): mtd / day-of-month * days-in-month
      const forecast = dayOfMonth > 0 ? (credits / dayOfMonth) * daysInMonth : 0;
      const capPct = cap > 0 ? (100 * credits / cap) : 0;
      tileCard('MTD credits', fmtN(credits));
      tileCard('Forecast month-end', fmtN(Math.round(forecast)),
        cap > 0 && forecast > cap ? { kind: 'bad', text: '▲ projected over cap' }
                                  : { kind: 'good', text: 'linear extrapolation' });
      tileCard('Overage cap', fmtN(cap));
      tileCard('Cap consumed', capPct.toFixed(1) + '%',
        capPct > 80 ? { kind: 'bad', text: '⚠ >80% of cap' } : { kind: 'good', text: 'within budget' });
      Charts.bar(card('Daily credit burn', 'Credits consumed per day'),
        rowsOf(data, 'budget_daily_burn'), { name: 'credits' });
      Charts.bar(card('Overage days', 'Days where overage credits were used', ''),
        rowsOf(data, 'budget_overage_days'), { name: 'overage', color: 7 });
      Charts.hbar(card('MTD credits by user', 'Calendar-month spend ranking'),
        rowsOf(data, 'budget_by_user_mtd'), { name: 'credits' });
      Charts.donut(card('MTD credits by tier', 'Subscription tier split'),
        rowsOf(data, 'budget_by_tier_mtd'), { name: 'credits' });
    },

    dora(data) {
      const kpi = rowsOf(data, 'dora_kpis')[0] || [];
      tileCard('Merged PRs', fmtN(kpi[0]));
      tileCard('Median time to merge', kpi[1] != null ? `${fmtN(kpi[1])} h` : '—');
      tileCard('Median lead time', kpi[2] != null ? `${fmtN(kpi[2])} h` : '—',
        { kind: 'good', text: 'first commit → merge' });
      tileCard('Reverts & hotfixes', fmtN(kpi[4]),
        Number(kpi[4]) > 0 ? { kind: 'bad', text: 'failure signal' } : { kind: 'good', text: 'clean' });
      Charts.bar(card('PRs merged per day', 'Deployment-frequency proxy (merged to main)'),
        rowsOf(data, 'dora_prs_merged_daily'), { name: 'PRs' });
      Charts.line(card('Median time to merge', 'Hours from PR open to merge, per day'),
        rowsOf(data, 'dora_time_to_merge_daily'), { name: 'hours', unit: 'h' });
      Charts.hbar(card('Merged PRs by repo', 'Tracked repos (admins add more on the Policy tab)'),
        rowsOf(data, 'dora_by_repo'), { name: 'PRs' });
      Charts.donut(card('AI assistance share', 'From Co-authored-by trailers (kiro / claude / amazon-q / copilot)'),
        rowsOf(data, 'dora_ai_share'), { name: 'PRs' });
      Charts.hbar(card('AI-assisted vs unassisted merge speed', 'Median hours to merge — the ROI question'),
        rowsOf(data, 'dora_ai_vs_speed'), { name: 'median h' });
      Charts.table(
        card('Recent merged PRs', 'Latest 50 in window', 'full'),
        ['Merged', 'Repo', '#', 'Title', 'Author', 'Merge h', 'AI'],
        rowsOf(data, 'dora_recent_prs'),
        { pills: { 6: (v) => v && v !== 'none' ? 'crit' : 'warn' } },
      );
    },

    policy(data) {
      Policy.render(data, { card, panel, reload: () => { render(); } });
    },

    overview(data) {
      const kpi = rowsOf(data, 'overview_kpis')[0] || [];
      const sec = rowsOf(data, 'overview_security_kpis')[0] || [];
      tileCard('Active users', fmtN(kpi[0]));
      tileCard('Credits used', fmtN(kpi[1]));
      tileCard('Total messages', fmtN(kpi[2]));
      tileCard('Overage credits', fmtN(kpi[3]),
        Number(kpi[3]) > 0 ? { kind: 'bad', text: '▲ overage in window' } : { kind: 'good', text: 'within plan' });
      tileCard('Sensitive-keyword hits', fmtN(sec[0]),
        Number(sec[0]) > 0 ? { kind: 'bad', text: '⚠ review audit trail' } : { kind: 'good', text: 'clean' });
      tileCard('After-hours events', fmtN(sec[1]));
      tileCard('Total prompts', fmtN(sec[2]));
      const pctSensitive = sec[2] > 0 ? (100 * sec[0] / sec[2]).toFixed(2) + '%' : '—';
      tileCard('Sensitive rate', pctSensitive);
    },

    usage(data) {
      Charts.line(card('Daily active users', 'Distinct users with activity per day'),
        rowsOf(data, 'usage_daily_active_users'), { name: 'users' });
      Charts.line(card('Daily credits consumed', 'Sum of credits_used per day'),
        rowsOf(data, 'usage_daily_credits'), { name: 'credits' });
      Charts.bar(card('Daily total messages', 'All clients'),
        rowsOf(data, 'usage_daily_messages'), { name: 'messages' });
      Charts.donut(card('Messages by client type', 'Share of total messages'),
        rowsOf(data, 'usage_by_client_type'), { name: 'messages' });
      Charts.bar(card('New users per day', 'From the report’s New_User flag'),
        rowsOf(data, 'usage_new_users_daily'), { name: 'new users', color: 2 });
      Charts.line(card('Automated message share', '% of messages sent by agents/automation vs typed'),
        rowsOf(data, 'usage_auto_share_daily'), { name: '%', unit: '%' });
    },

    security(data) {
      Charts.bar(card('Sensitive-keyword alerts per day', 'Prompts matching the keyword policy'),
        rowsOf(data, 'security_keyword_alerts_daily'), { name: 'alerts', color: 7 });
      Charts.bar(card('After-hours usage per day', 'Prompts outside business hours'),
        rowsOf(data, 'security_after_hours_daily'), { name: 'events', color: 1 });
      Charts.donut(card('Keyword breakdown', 'Which sensitive keywords triggered'),
        rowsOf(data, 'security_keyword_breakdown'), { name: 'hits' });
      Charts.table(
        card('Audit trail',
          data.prompt_text_redacted
            ? 'Sensitive or after-hours prompts (latest 200). Prompt text is investigator-only — ask an admin for access.'
            : 'Sensitive or after-hours prompts (latest 200; prompt text truncated at 500 chars)',
          'full'),
        ['Date', 'User', 'Keyword', 'After-hours', 'Prompt (truncated)'],
        rowsOf(data, 'security_audit_trail'),
        { pills: { 2: (v) => v && v !== 'none' ? 'crit' : 'warn' } },
      );
    },

    quality(data) {
      Charts.line(card('Avg prompt length per day', 'Characters per prompt'),
        rowsOf(data, 'quality_avg_prompt_length'), { name: 'chars' });
      Charts.line(card('Response/prompt ratio', 'AI output volume per unit of prompt'),
        rowsOf(data, 'quality_response_prompt_ratio'), { name: 'ratio' });
      Charts.line(card('Avg response length per day', 'Characters per assistant response'),
        rowsOf(data, 'quality_avg_response_length'), { name: 'chars' });
      Charts.donut(card('Trigger type distribution', 'Chat vs inline maturity signal'),
        rowsOf(data, 'quality_trigger_type'), { name: 'prompts' });
      Charts.donut(card('Model distribution', 'modelId from prompt logs ("auto" = Kiro-selected)'),
        rowsOf(data, 'quality_model_distribution'), { name: 'prompts' });
    },

    cost(data) {
      Charts.donut(card('Credits by subscription tier', 'Where spend concentrates'),
        rowsOf(data, 'cost_by_tier'), { name: 'credits' });
      Charts.line(card('Overage credits trend', 'Credits beyond plan allowance per day'),
        rowsOf(data, 'cost_overage_trend'), { name: 'credits' });
      Charts.hbar(card('Credits by team', 'From the enriched view (UNMAPPED = not in user-project.csv)'),
        rowsOf(data, 'cost_by_team'), { name: 'credits' });
      Charts.hbar(card('Credits by project', 'Cost-allocation rollup'),
        rowsOf(data, 'cost_by_project'), { name: 'credits' });
      Charts.hbar(card('Credits by cost center', 'FinOps chargeback view'),
        rowsOf(data, 'cost_by_cost_center'), { name: 'credits' });
      Charts.hbar(card('Top users by credits', 'Top 20 in window'),
        rowsOf(data, 'cost_top_users'), { name: 'credits' });
    },
  };

  /* ---------------- render loop ---------------- */
  async function render() {
    panel().replaceChildren();
    $('#error-banner').hidden = true;
    if (LOCAL_TABS.has(state.tab)) { RENDERERS[state.tab](); return; }
    let data;
    try {
      data = await load();
    } catch (err) {
      $('#error-banner').hidden = false;
      $('#error-banner').textContent =
        `Failed to load data (${err.message}). Athena may still be warming up — retry shortly.`;
      return;
    }
    if (!data) return;   // logged out mid-flight
    RENDERERS[state.tab](data);
    if (data.errors) {
      $('#error-banner').hidden = false;
      $('#error-banner').textContent =
        'Some panels failed to load: ' + Object.keys(data.errors).join(', ') +
        '. They will retry on next refresh.';
    }
  }

  /* ---------------- wiring ---------------- */
  const PAGE_TITLES = {
    intro: 'Introduction', architecture: 'Architecture', overview: 'Overview',
    usage: 'Usage & Adoption', security: 'Security & Compliance',
    quality: 'Prompt Quality', productivity: 'Productivity',
    cost: 'Cost Governance', budget: 'Budget', dora: 'DORA Metrics',
    policy: 'Policy',
  };

  function initNav() {
    $('#tabs').addEventListener('click', (e) => {
      const btn = e.target.closest('button[data-tab]');
      if (!btn || state.loading) return;
      state.tab = btn.dataset.tab;
      $('#tabs').querySelectorAll('button[data-tab]').forEach(
        (b) => b.classList.toggle('active', b === btn));
      $('#page-title').textContent = PAGE_TITLES[state.tab] || state.tab;
      document.querySelector('.shell').classList.remove('nav-open-mobile');
      $('#nav-scrim').hidden = true;
      render();
    });

    // desktop: collapse to icon rail (persisted per tab-session)
    const shell = document.querySelector('.shell');
    if (sessionStorage.getItem('ktd.nav') === 'rail') shell.classList.add('nav-collapsed');
    $('#nav-collapse').addEventListener('click', () => {
      shell.classList.toggle('nav-collapsed');
      sessionStorage.setItem('ktd.nav',
        shell.classList.contains('nav-collapsed') ? 'rail' : 'full');
    });

    // mobile: drawer
    $('#nav-open').addEventListener('click', () => {
      shell.classList.add('nav-open-mobile');
      $('#nav-scrim').hidden = false;
    });
    $('#nav-scrim').addEventListener('click', () => {
      shell.classList.remove('nav-open-mobile');
      $('#nav-scrim').hidden = true;
    });
    $('#range').addEventListener('click', (e) => {
      const btn = e.target.closest('button[data-days]');
      if (!btn) return;
      state.days = Number(btn.dataset.days);
      for (const b of $('#range').children) b.classList.toggle('active', b === btn);
      render();
    });
    $('#logout-btn').addEventListener('click', () => Auth.logout());
    $('#login-btn').addEventListener('click', () => Auth.login());
  }

  async function main() {
    initTheme();
    initNav();
    try {
      await Auth.completeLoginIfCallback();
    } catch (err) {
      // login-view has its own error element; #error-banner lives inside
      // the (hidden) app view and would be invisible here.
      $('#login-view').hidden = false;
      const box = $('#login-error');
      box.hidden = false;
      box.textContent = err.message;
      return;
    }
    if (Auth.isAuthenticated()) {
      $('#app-view').hidden = false;
      render();
    } else {
      $('#login-view').hidden = false;
    }
  }

  main();
})();
