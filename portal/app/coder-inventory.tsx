'use client';

import {useEffect, useState} from 'react';

type Inventory = {
  workspaces: {id: string; name: string; owner: string; template: string; status: string; outdated: boolean; url: string}[];
  agents: {id: string; name: string; workspace: string; status: string; workspace_url: string}[];
  templates: {id: string; name: string; organization: string; active_version: string}[];
  total_workspaces: number;
  native_url: string;
};

export default function CoderInventory() {
  const [inventory, setInventory] = useState<Inventory>();
  const [error, setError] = useState(false);
  const [search, setSearch] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    async function refresh() {
      try {
        const response = await fetch('/api/admin/coder-inventory', {signal: controller.signal, cache: 'no-store'});
        if (!response.ok) throw Error();
        const data: Inventory = await response.json();
        if (!controller.signal.aborted) {
          setInventory(data);
          setError(false);
        }
      } catch {
        if (!controller.signal.aborted) setError(true);
      }
    }
    void refresh();
    const timer = setInterval(refresh, 15000);
    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, []);

  const visible = inventory?.workspaces.filter(workspace =>
    [workspace.name, workspace.owner, workspace.template].join(' ').toLowerCase().includes(search.trim().toLowerCase()));
  return <section className="wb-content panel" aria-label="Coder administration" style={{overflow: 'auto'}}>
    <h2>Coder administration</h2>
    {error && <p role="alert">Coder inventory is unavailable. Retrying automatically.</p>}
    {!inventory && !error && <p role="status">Loading Coder inventory…</p>}
    {inventory && <>
      <h3>Workspaces</h3>
      <label>Search workspaces <input type="search" value={search} onChange={event => setSearch(event.target.value)} /></label>
      <p>{visible?.length} shown · {inventory.total_workspaces} total</p>
      <table>
        <thead><tr><th scope="col">Workspace</th><th scope="col">Owner</th><th scope="col">Template</th><th scope="col">Status</th></tr></thead>
        <tbody>{visible?.map(row => <tr key={row.id}>
          <th scope="row"><a href={row.url}>{row.name}</a></th><td>{row.owner}</td><td>{row.template}</td>
          <td>{row.status}{row.outdated ? ' · Update available' : ''}</td>
        </tr>)}</tbody>
      </table>
      <h3>Templates</h3>
      <ul>{inventory.templates.map(item => <li key={item.id}>{item.name} · {item.organization}</li>)}</ul>
      <h3>Agents</h3>
      <ul>{inventory.agents.map(agent => <li key={agent.id}>
        <a href={agent.workspace_url}>{agent.workspace}</a> · {agent.name} · {agent.status}
      </li>)}</ul>
      <h3>Admin settings</h3>
      <a href={inventory.native_url + '/deployment/general'}>Open Coder settings</a>
    </>}
  </section>;
}
