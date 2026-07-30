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
