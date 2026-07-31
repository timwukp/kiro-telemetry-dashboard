/* Introduction tab — 5-scene narrated tour (~5 min).
   Audio: Amazon Polly MP3s under /intro-audio/{lang}/{scene}.mp3 (same
   origin, served by CloudFront). Captions from intro-captions.json.
   Scenes are lightweight SVG/CSS animations that advance when each
   scene's audio ends. No third-party code. */

'use strict';

const Intro = (() => {
  const SCENES = ['s1_problem', 's2_solution', 's3_architecture', 's4_governance', 's5_tour'];
  const SCENE_TITLES = {
    s1_problem: 'The problem',
    s2_solution: 'The solution',
    s3_architecture: 'How it works',
    s4_governance: 'Governance & guardrails',
    s5_tour: 'Tour of the views',
  };
  const LANG_ORDER = ['en', 'zh', 'yue', 'ja', 'ko'];

  let captions = null;      // loaded once
  let state = null;         // per-render session

  async function loadCaptions() {
    if (captions) return captions;
    const res = await fetch('intro-captions.json');
    captions = await res.json();
    return captions;
  }

  /* ---------------- scene visuals (pure DOM/SVG, animated via CSS) ---- */

  const NS = 'http://www.w3.org/2000/svg';
  const el = (tag, attrs = {}) => {
    const n = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    return n;
  };
  const nodeBox = (svg, x, y, w, h, label, sub, acc) => {
    const g = el('g', { class: 'intro-node' + (acc ? ' acc' : '') });
    g.appendChild(el('rect', { x, y, width: w, height: h, rx: 10 }));
    const t1 = el('text', { x: x + w / 2, y: y + h / 2 - 4, 'text-anchor': 'middle', class: 'intro-label' });
    t1.textContent = label;
    g.appendChild(t1);
    if (sub) {
      const t2 = el('text', { x: x + w / 2, y: y + h / 2 + 14, 'text-anchor': 'middle', class: 'intro-sub' });
      t2.textContent = sub;
      g.appendChild(t2);
    }
    svg.appendChild(g);
    return g;
  };

  function sceneProblem(stage) {
    const svg = el('svg', { viewBox: '0 0 800 400' });
    const qs = [
      ['Who is using it?', 60, 60], ['What does it cost?', 430, 60],
      ['Is it faster?', 60, 150], ['Any secrets leaking?', 430, 150],
    ];
    qs.forEach(([q, x, y], i) => {
      const g = nodeBox(svg, x, y, 310, 60, q, '', i === 3);
      g.style.animation = `intro-pop .5s ease-out ${i * 1.8}s both`;
    });
    const s3 = nodeBox(svg, 250, 280, 300, 80, 'S3: raw telemetry', 'thousands of .json.gz files');
    s3.style.animation = 'intro-pop .6s ease-out 7.5s both';
    for (let i = 0; i < 12; i++) {
      const f = el('rect', {
        x: 265 + (i % 6) * 45, y: 330 + Math.floor(i / 6) * 14,
        width: 34, height: 9, rx: 2, class: 'intro-file',
      });
      f.style.animation = `intro-fade .4s ease-out ${8 + i * 0.25}s both`;
      svg.appendChild(f);
    }
    stage.appendChild(svg);
  }

  function sceneSolution(stage) {
    const svg = el('svg', { viewBox: '0 0 800 400' });
    const mid = nodeBox(svg, 290, 160, 220, 80, 'This dashboard', 'serverless · AWS-native', true);
    mid.style.animation = 'intro-pop .6s ease-out .3s both';
    const tabs = ['Overview', 'Usage', 'Security', 'Quality', 'Productivity',
                  'Cost', 'Budget', 'DORA', 'Policy', 'Architecture'];
    tabs.forEach((t, i) => {
      const ang = (i / tabs.length) * 2 * Math.PI - Math.PI / 2;
      const x = 400 + Math.cos(ang) * 260 - 55;
      const y = 200 + Math.sin(ang) * 150 - 18;
      const g = nodeBox(svg, x, y, 110, 36, t, '');
      g.style.animation = `intro-pop .4s ease-out ${1.5 + i * 0.5}s both`;
      const line = el('line', {
        x1: 400 + Math.cos(ang) * 130, y1: 200 + Math.sin(ang) * 75,
        x2: 400 + Math.cos(ang) * 200, y2: 200 + Math.sin(ang) * 115,
        class: 'intro-wire',
      });
      line.style.animation = `intro-fade .4s ease-out ${1.3 + i * 0.5}s both`;
      svg.appendChild(line);
    });
    stage.appendChild(svg);
  }

  function sceneArchitecture(stage) {
    // reuse the real diagram module on a fresh container, then animate lanes in
    const wrap = document.createElement('div');
    wrap.className = 'intro-arch';
    Arch.render(wrap, {});          // no health data in tour mode
    wrap.querySelectorAll('svg > g, svg > path').forEach((n, i) => {
      n.style.animation = `intro-fade .5s ease-out ${Math.min(i * 0.55, 14)}s both`;
    });
    stage.appendChild(wrap);
  }

  function sceneGovernance(stage) {
    const svg = el('svg', { viewBox: '0 0 800 400' });
    nodeBox(svg, 40, 40, 220, 70, 'Sensitive keywords', 'password · secret · api_key', true)
      .style.animation = 'intro-pop .5s ease-out .5s both';
    nodeBox(svg, 40, 140, 220, 70, 'After-hours activity', 'outside business hours')
      .style.animation = 'intro-pop .5s ease-out 3s both';
    nodeBox(svg, 40, 240, 220, 70, 'Audit trail', 'prompt text: investigator-only')
      .style.animation = 'intro-pop .5s ease-out 6s both';
    const scanner = nodeBox(svg, 330, 140, 180, 70, 'Scanner λ', 'daily checks');
    scanner.style.animation = 'intro-pop .5s ease-out 9s both';
    const sns = nodeBox(svg, 580, 140, 170, 70, 'SNS alerts', 'email · budget >80%', true);
    sns.style.animation = 'intro-pop .5s ease-out 11s both';
    [['M260,175 L330,175'], ['M510,175 L580,175']].forEach(([d], i) => {
      const p = el('path', { d, class: 'intro-wire flow' });
      p.style.animation = `intro-fade .4s ease-out ${9.5 + i * 2}s both`;
      svg.appendChild(p);
    });
    nodeBox(svg, 330, 280, 420, 80, 'Policy registry', 'MCP allowlist · steering · DORA repos — versioned, admin-only writes')
      .style.animation = 'intro-pop .6s ease-out 16s both';
    stage.appendChild(svg);
  }

  function sceneTour(stage) {
    const svg = el('svg', { viewBox: '0 0 800 400' });
    const items = [
      ['Overview', '8 KPIs'], ['Usage', 'DAU · new users'], ['Security', 'alerts · audit'],
      ['Quality', 'lengths · models'], ['Productivity', 'AI lines · ROI'],
      ['Cost', 'by team/tier'], ['Budget', 'burn · forecast'], ['DORA', 'merge speed'],
      ['Policy', 'admin registry'],
    ];
    items.forEach(([t, sub], i) => {
      const x = 45 + (i % 3) * 250, y = 40 + Math.floor(i / 3) * 105;
      const g = nodeBox(svg, x, y, 220, 78, t, sub, i === 4);
      g.style.animation = `intro-pop .45s ease-out ${i * 2.6}s both`;
    });
    const cta = el('text', { x: 400, y: 385, 'text-anchor': 'middle', class: 'intro-label' });
    cta.textContent = 'Open source · deploy with one script · the data is live';
    cta.style.animation = 'intro-fade .8s ease-out 24s both';
    svg.appendChild(cta);
    stage.appendChild(svg);
  }

  const SCENE_BUILDERS = {
    s1_problem: sceneProblem, s2_solution: sceneSolution,
    s3_architecture: sceneArchitecture, s4_governance: sceneGovernance,
    s5_tour: sceneTour,
  };

  /* ---------------- player ---------------- */

  function fmtTime(s) {
    if (!isFinite(s)) return '–:––';
    return `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
  }

  function render(container) {
    state = { lang: null, idx: 0, audio: null, playing: false };

    const shell = document.createElement('div');
    shell.className = 'intro-shell';
    container.appendChild(shell);

    loadCaptions().then((caps) => {
      // ---- start screen: language picker ----
      const start = document.createElement('div');
      start.className = 'intro-start';
      const h = document.createElement('h3');
      h.textContent = 'Choose your narration language · 選擇語言 · 言語を選択 · 언어 선택';
      start.appendChild(h);
      const row = document.createElement('div');
      row.className = 'intro-langs';
      for (const lang of LANG_ORDER) {
        if (!caps[lang]) continue;
        const b = document.createElement('button');
        b.className = 'primary intro-lang-btn';
        b.textContent = caps[lang].name;
        b.addEventListener('click', () => { start.remove(); play(shell, caps, lang); });
        row.appendChild(b);
      }
      start.appendChild(row);
      const note = document.createElement('p');
      note.className = 'sub';
      note.textContent = 'Voice-over by Amazon Polly · ~5 minutes · 5 scenes';
      start.appendChild(note);
      shell.appendChild(start);
    }).catch(() => {
      shell.textContent = 'Failed to load the tour assets. Retry shortly.';
    });
  }

  function play(shell, caps, lang) {
    state.lang = lang;
    state.idx = 0;

    const stage = document.createElement('div');
    stage.className = 'intro-stage';
    const caption = document.createElement('div');
    caption.className = 'intro-caption';
    const bar = document.createElement('div');
    bar.className = 'intro-bar';

    const btn = document.createElement('button');
    btn.className = 'primary';
    btn.textContent = '⏸';
    btn.setAttribute('aria-label', 'Play/pause');
    const title = document.createElement('span');
    title.className = 'intro-title';
    const timer = document.createElement('span');
    timer.className = 'sub intro-timer';
    const dots = document.createElement('span');
    dots.className = 'intro-dots';
    SCENES.forEach((s, i) => {
      const d = document.createElement('button');
      d.className = 'intro-dot';
      d.setAttribute('aria-label', `Scene ${i + 1}: ${SCENE_TITLES[s]}`);
      d.addEventListener('click', () => go(i));
      dots.appendChild(d);
    });
    const langBtn = document.createElement('button');
    langBtn.textContent = caps[lang].name + ' ▾';
    langBtn.addEventListener('click', () => {
      stop();
      shell.replaceChildren();
      render(shell.parentElement === null ? shell : shell.parentElement) || location.reload();
    });

    bar.append(btn, title, dots, timer, langBtn);
    shell.append(stage, caption, bar);

    btn.addEventListener('click', () => {
      if (!state.audio) return;
      if (state.audio.paused) { state.audio.play(); btn.textContent = '⏸'; }
      else { state.audio.pause(); btn.textContent = '▶'; }
    });

    function stop() {
      if (state.audio) { state.audio.pause(); state.audio = null; }
    }

    function go(idx) {
      if (idx >= SCENES.length) {           // finished
        stop();
        title.textContent = 'Tour complete — explore the tabs above';
        caption.textContent = '';
        btn.textContent = '▶';
        btn.onclick = () => { shell.replaceChildren(); state.idx = 0; play(shell, caps, lang); };
        return;
      }
      state.idx = idx;
      const scene = SCENES[idx];
      stop();
      stage.replaceChildren();
      SCENE_BUILDERS[scene](stage);
      title.textContent = `${idx + 1}/${SCENES.length} · ${SCENE_TITLES[scene]}`;
      caption.textContent = caps[lang].scenes[scene];
      caption.scrollTop = 0;
      [...dots.children].forEach((d, i) => d.classList.toggle('active', i === idx));

      const audio = new Audio(`intro-audio/${lang}/${scene}.mp3`);
      state.audio = audio;
      audio.addEventListener('timeupdate', () => {
        timer.textContent = `${fmtTime(audio.currentTime)} / ${fmtTime(audio.duration)}`;
      });
      audio.addEventListener('ended', () => go(idx + 1));
      audio.addEventListener('error', () => {
        caption.textContent += '  [audio unavailable — advancing in 20s]';
        setTimeout(() => { if (state.idx === idx) go(idx + 1); }, 20000);
      });
      audio.play().then(() => { btn.textContent = '⏸'; }).catch(() => {
        btn.textContent = '▶';   // autoplay blocked: user presses play
      });
    }

    go(0);
  }

  return { render };
})();
