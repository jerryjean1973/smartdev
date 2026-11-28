(async () => {
  const status = document.getElementById('context-status');
  const output = document.getElementById('context-sources');
  function section(title, values, empty = 'None discovered') {
    const heading = document.createElement('h2');
    heading.textContent = title;
    const list = document.createElement(values.length ? 'ul' : 'p');
    if (!values.length) list.textContent = empty;
    for (const value of values) {
      const item = document.createElement('li');
      item.textContent = value;
      list.append(item);
    }
    output.append(heading, list);
  }
  try {
    const response = await fetch('project-context');
    if (!response.ok) throw new Error('Context unavailable');
    const index = await response.json();
    status.textContent = 'Project source index saved · ' + new Date(index.refreshed_at).toLocaleString();
    if (index.truncated) status.textContent += ' · Scan limit reached';
    const sources = kind => Object.keys(index.sources).filter(path => index.sources[path].kind === kind);
    section('Instructions discovered', sources('instructions'));
    section('Skills discovered', [
      ...sources('skills'),
      ...Object.entries(index.installed_skills).flatMap(([scope, names]) => names.map(name => scope + ' · ' + name)),
    ]);
    section('Documentation indexed', sources('documentation'));
    section('Source structure', [...index.stack, ...index.source_roots.map(path => path + '/')]);
    section('Build and test sources', sources('manifests'));
    section('Deployment configuration', sources('deployment'));
    section('Git checkout', [index.git.branch, index.git.head?.slice(0, 12)].filter(Boolean));
    section('Native MCP configuration', [
      ...sources('codex_config'),
      ...(index.mcp || []).map(server => server.name + (server.enabled ? ' · Enabled' : ' · Disabled')),
    ], index.mcp === null ? 'Coding-agent MCP inventory unavailable' : 'No server configuration reported');
    const note = document.createElement('p');
    note.textContent = 'The selected coding agent verifies connected tools during execution.';
    output.append(note);
    if (index.stale.length) section('Changed or removed since the previous refresh', index.stale);
  } catch {
    status.textContent = 'Project context is unavailable. Reopen this view to retry.';
  }
})();
