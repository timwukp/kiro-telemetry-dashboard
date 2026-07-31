/* Policy tab — MCP allowlist + global steering management.
   GET /api/policy is visible to all signed-in users; PUT requires the
   Cognito `admins` group (enforced server-side; the UI only hides
   controls, it is not the security boundary).

   Honest scope (also shown in the UI): Kiro has no public admin API.
   This registry distributes config via the officially supported file
   mechanism (~/.kiro/settings/mcp.json, ~/.kiro/steering/) using
   kiro-policy-sync.sh; workspace-level files can override user-level
   ones, so telemetry audit is the compensating control. */

'use strict';

const Policy = (() => {

  function mcpJson(policy) {
    return JSON.stringify({ mcpServers: policy.mcp_allowlist || {} }, null, 2);
  }

  function render(policy, ctx) {
    const { card } = ctx;
    const isAdmin = !!policy.requester_is_admin;

    // ---- scope note ----
    const note = card('How enforcement works', '', 'full');
    const p = document.createElement('p');
    p.className = 'sub';
    p.textContent =
      'Kiro exposes no public admin API. This registry is distributed to developer machines ' +
      'through Kiro’s official file mechanism (~/.kiro/settings/mcp.json and ~/.kiro/steering/) ' +
      'via the kiro-policy-sync.sh client. Workspace-level Kiro config can still override user-level ' +
      'config, so the telemetry audit on the Security tab is the compensating control. ' +
      (policy.updated_at
        ? `Current: v${policy.version}, updated ${policy.updated_at} by ${policy.updated_by}.`
        : 'No policy has been published yet.');
    note.appendChild(p);

    // ---- MCP allowlist editor ----
    const mcpCard = card('MCP server allowlist',
      isAdmin ? 'Kiro mcpServers shape. Edit and publish (admins only).'
              : 'Read-only — you are not in the admins group.', 'full');
    const editor = document.createElement('textarea');
    editor.className = 'policy-editor';
    editor.spellcheck = false;
    editor.value = mcpJson(policy);
    editor.readOnly = !isAdmin;
    mcpCard.appendChild(editor);

    // ---- steering files ----
    const steerCard = card('Global steering files',
      'Markdown files distributed to ~/.kiro/steering/ on developer machines.', 'full');
    const steerList = document.createElement('div');
    steerCard.appendChild(steerList);
    let steering = (policy.steering_files || []).map(f => ({ ...f }));

    function drawSteering() {
      steerList.replaceChildren();
      if (!steering.length) {
        const d = document.createElement('div');
        d.className = 'empty';
        d.textContent = 'No steering files published.';
        steerList.appendChild(d);
      }
      steering.forEach((f, i) => {
        const row = document.createElement('div');
        row.className = 'steer-row';
        const name = document.createElement('input');
        name.value = f.name;
        name.placeholder = 'security-rules.md';
        name.readOnly = !isAdmin;
        name.addEventListener('input', () => { steering[i].name = name.value; });
        const body = document.createElement('textarea');
        body.className = 'policy-editor small';
        body.value = f.content_md;
        body.readOnly = !isAdmin;
        body.spellcheck = false;
        body.addEventListener('input', () => { steering[i].content_md = body.value; });
        row.appendChild(name);
        row.appendChild(body);
        if (isAdmin) {
          const del = document.createElement('button');
          del.textContent = 'Remove';
          del.className = 'ghost-btn';
          del.addEventListener('click', () => { steering.splice(i, 1); drawSteering(); });
          row.appendChild(del);
        }
        steerList.appendChild(row);
      });
      if (isAdmin) {
        const add = document.createElement('button');
        add.textContent = '+ Add steering file';
        add.className = 'ghost-btn';
        add.addEventListener('click', () => {
          steering.push({ name: 'new-policy.md', content_md: '# Team policy\n' });
          drawSteering();
        });
        steerList.appendChild(add);
      }
    }
    drawSteering();

    // ---- Organization mappings (drives Cost Governance rollups) ----
    const orgCard = card('Organization mappings',
      'userid → team / project / cost center. Publishing writes the cost-allocation CSV that the Cost tab joins on. Connectors below are reserved integration points.', 'full');
    const table = document.createElement('table');
    table.className = 'data org-map-table';
    const thead = document.createElement('thead');
    const hr = document.createElement('tr');
    for (const h of ['User ID', 'Team (Jira)', 'Project (repo)', 'Cost center (dept)', '']) {
      const th = document.createElement('th'); th.textContent = h; hr.appendChild(th);
    }
    thead.appendChild(hr); table.appendChild(thead);
    const tbody = document.createElement('tbody');
    table.appendChild(tbody);
    let orgRows = ((policy.org_mappings || {}).rows || []).map(r => ({ ...r }));

    function drawOrg() {
      tbody.replaceChildren();
      orgRows.forEach((row, i) => {
        const tr = document.createElement('tr');
        for (const key of ['userid', 'team', 'project', 'cost_center']) {
          const td = document.createElement('td');
          const inp = document.createElement('input');
          inp.value = row[key] || '';
          inp.readOnly = !isAdmin;
          inp.placeholder = { userid: 'kiro userid', team: 'Platform Engineering',
                              project: 'repo-name', cost_center: 'CC-4501' }[key];
          inp.addEventListener('input', () => { orgRows[i][key] = inp.value; });
          td.appendChild(inp); tr.appendChild(td);
        }
        const act = document.createElement('td');
        if (isAdmin) {
          const del = document.createElement('button');
          del.className = 'ghost-btn'; del.textContent = '✕';
          del.addEventListener('click', () => { orgRows.splice(i, 1); drawOrg(); });
          act.appendChild(del);
        }
        tr.appendChild(act);
        tbody.appendChild(tr);
      });
      if (!orgRows.length) {
        const tr = document.createElement('tr');
        const td = document.createElement('td');
        td.colSpan = 5; td.className = 'empty';
        td.textContent = 'No mappings — Cost tab shows UNMAPPED until rows are published.';
        tr.appendChild(td); tbody.appendChild(tr);
      }
      if (isAdmin) {
        const tr = document.createElement('tr');
        const td = document.createElement('td');
        td.colSpan = 5;
        const add = document.createElement('button');
        add.className = 'ghost-btn'; add.textContent = '+ Add mapping';
        add.addEventListener('click', () => {
          orgRows.push({ userid: '', team: '', project: '', cost_center: '' });
          drawOrg();
        });
        td.appendChild(add); tr.appendChild(td); tbody.appendChild(tr);
      }
    }
    drawOrg();
    orgCard.appendChild(table);

    // reserved connectors — visible so the integration story is explicit
    const conn = document.createElement('div');
    conn.className = 'connectors';
    const connectors = (policy.org_mappings || {}).connectors || {};
    for (const [key, label, hint] of [
      ['jira', 'Jira', 'import team names from boards'],
      ['github', 'GitHub', 'derive projects from repo activity'],
      ['hr', 'HR / Finance', 'import cost centers'],
    ]) {
      const chip = document.createElement('div');
      chip.className = 'connector-chip';
      const dot = document.createElement('span');
      const enabled = !!(connectors[key] || {}).enabled;
      dot.className = 'conn-dot' + (enabled ? ' on' : '');
      const txt = document.createElement('span');
      txt.textContent = `${label} connector — ${enabled ? 'connected' : 'reserved'} · ${hint}`;
      chip.append(dot, txt);
      conn.appendChild(chip);
    }
    orgCard.appendChild(conn);

    // ---- DORA tracked repos ----
    const doraCard = card('DORA tracked repositories',
      'owner/repo per line. The dora-sync Lambda pulls PR data for these hourly.', 'full');
    const repoEditor = document.createElement('textarea');
    repoEditor.className = 'policy-editor small';
    repoEditor.spellcheck = false;
    repoEditor.value = (policy.dora_repos || []).join('\n');
    repoEditor.readOnly = !isAdmin;
    doraCard.appendChild(repoEditor);

    // ---- actions ----
    const actions = card('Distribute', '', 'full');
    const bar = document.createElement('div');
    bar.className = 'policy-actions';

    const download = document.createElement('button');
    download.textContent = 'Download mcp.json';
    download.className = 'ghost-btn';
    download.addEventListener('click', () => {
      const blob = new Blob([editor.value], { type: 'application/json' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'mcp.json';
      a.click();
      URL.revokeObjectURL(a.href);
    });
    bar.appendChild(download);

    const status = document.createElement('span');
    status.className = 'sub';

    if (isAdmin) {
      const publish = document.createElement('button');
      publish.textContent = 'Publish policy';
      publish.className = 'primary';
      publish.addEventListener('click', async () => {
        let allow;
        try {
          const parsed = JSON.parse(editor.value);
          allow = parsed.mcpServers;
          if (typeof allow !== 'object' || allow === null || Array.isArray(allow)) {
            throw new Error('mcpServers must be an object');
          }
        } catch (e) {
          status.textContent = `Invalid JSON: ${e.message}`;
          return;
        }
        status.textContent = 'Publishing…';
        try {
          const repos = repoEditor.value.split('\n').map(s => s.trim()).filter(Boolean);
          const badRepo = repos.find(r => !/^[\w.-]+\/[\w.-]+$/.test(r));
          if (badRepo) { status.textContent = `Invalid repo "${badRepo}" — use owner/repo`; return; }
          const res = await Auth.apiPut('/api/policy', {
            mcp_allowlist: allow,
            steering_files: steering.filter(f => f.name && f.content_md),
            dora_repos: repos,
            org_mappings: {
              rows: orgRows.filter(r => r.userid),
              connectors: (policy.org_mappings || {}).connectors,
            },
          });
          status.textContent = `Published v${res.version} at ${res.updated_at}`;
        } catch (e) {
          status.textContent = `Publish failed: ${e.message}`;
        }
      });
      bar.appendChild(publish);
    }
    bar.appendChild(status);
    actions.appendChild(bar);
  }

  return { render };
})();
