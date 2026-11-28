'use client';

import DeployKeys from './deploy-keys';
import DnsInspection from './dns-inspection';
import { useEffect, useRef, useState, type FormEvent } from 'react';

type Connection = {status: string; message?: string; items?: string[]; checked_at?: string};
type Provider = {
  id: string; name: string; category: string; documentation: string; setup_url: string; account: string;
  permission: string; steps: string[]; verification: string; capability: string;
  version: number; paste: boolean; authorize?: string; connection: Connection;
  completed: {step: number; completed_at: string}[];
};
type Role = {scope:string; role:string; provider:string; model:string|null};
type ModelCheck = {project_id:string|null; provider:string; model:string; status:string; usage:Record<string,number>|null; checked_at:string};
type CodingAgent = {selected:'codex_cli'|'claude_code'; codex_cli:boolean; claude_code:boolean};
type Activity = {id: number; provider: string; operation: string; actor: string; status: string; occurred_at: string};
const label = (value: string) => value.toLowerCase().replace(/_/g, ' ');

async function api<T>(path = '', init?: RequestInit): Promise<T> {
  const response = await fetch('/api/integrations' + path, {...init, cache: 'no-store'});
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Integration request failed.');
  return result;
}

export default function Integrations({onClose, projectId, repository}: {onClose: () => void; projectId?:string; repository?:string}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [history, setHistory] = useState<Activity[]>([]);
  const [settings, setSettings] = useState<{roles:Role[]; checks:ModelCheck[]; coding_agent:CodingAgent|null}>({roles:[], checks:[], coding_agent:null});
  const [model, setModel] = useState('');
  const [role, setRole] = useState('prompter');
  const [projectScoped, setProjectScoped] = useState(false);
  const [query, setQuery] = useState('');
  const [selection, setSelection] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const provider = providers.find(item => item.id === selection);

  async function refresh() {
    const settingPath='/ai/settings'+(projectId?'?project_id='+encodeURIComponent(projectId):'');
    const [directory, activity, ai] = await Promise.all([api<Provider[]>(), api<Activity[]>('/activity'), api<{roles:Role[];checks:ModelCheck[];coding_agent:CodingAgent|null}>(settingPath)]);
    setSettings(ai);
    setProviders(directory);
    setHistory(activity);
    setLoaded(true);
  }

  useEffect(() => {
    dialog.current?.showModal();
    void refresh().catch(() => setMessage('Could not load integrations.'));
  }, []);

  async function action(operation: () => Promise<{message?: string}>) {
    setBusy(true);
    setMessage('');
    try {
      const result = await operation();
      setMessage(result.message ?? '');
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Integration request failed.');
    } finally {
      setBusy(false);
    }
  }

  function connect(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const credential = new FormData(form).get('credential');
    form.reset();
    void action(() => api<Connection>('/' + selection + '/credential', {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({credential}),
    }));
  }

  const visible = providers.filter(item => (item.name + ' ' + item.category + ' ' + item.capability).toLowerCase().includes(query.trim().toLowerCase()));
  return <dialog ref={dialog} className="panel integration-dialog" onClose={onClose} aria-labelledby="integration-title">
    <header className="row">
      <h2 id="integration-title">Integrations</h2>
      <button type="button" onClick={() => dialog.current?.close()}>Close</button>
    </header>
    {message && <p role="status">{message}</p>}
    {projectId && settings.coding_agent && <section aria-label="Coding agent">
      <h3>Coding agent</h3>
      <p>The selected agent handles new Taskboard work, replies, direct prompts, and MCP requests. An in-flight execution keeps its admitted agent; switching providers starts a fresh session on the next prompt.</p>
      <label>Coding agent<select value={settings.coding_agent.selected} disabled={busy}
        onChange={event=>{
          const selected=event.target.value as 'codex_cli'|'claude_code';
          if (!settings.coding_agent?.[selected]) return;
          void action(async()=>{
            await api('/ai/roles/coder',{method:'PUT',headers:{'Content-Type':'application/json'},
              body:JSON.stringify({provider:selected,model:null,project_id:projectId})});
            return {message:(selected==='codex_cli'?'Codex':'Claude Code')+' will run new project work.'};
          });
        }}>
        <option value="codex_cli" disabled={!settings.coding_agent.codex_cli}>Codex{settings.coding_agent.codex_cli?'':' · set DEV OPENAI_API_KEY first'}</option>
        <option value="claude_code" disabled={!settings.coding_agent.claude_code}>Claude Code{settings.coding_agent.claude_code?'':' · set DEV ANTHROPIC_API_KEY first'}</option>
      </select></label>
      <p>Configuration · Codex {settings.coding_agent.codex_cli?'key present':'not configured'} · Claude Code {settings.coding_agent.claude_code?'key present':'not configured'}</p>
    </section>}
    {provider ? <section>
      <button disabled={busy} onClick={() => {setSelection(''); setMessage('');}}>All integrations</button>
      <h3>{provider.name}</h3>
      <p>Verification status · {label(provider.connection.status)}</p>
      {provider.connection.message && <p>{provider.connection.message}</p>}
      {provider.connection.checked_at && <p>Last check · {new Date(provider.connection.checked_at).toLocaleString()}</p>}
      <p>{provider.capability || 'Guided setup'}</p>
      {provider.connection.items && provider.connection.items.length > 0 && <details>
        <summary>Discovered resources ({provider.connection.items.length})</summary>
        <ul>{provider.connection.items.map((item, index) => <li key={index}>{item}</li>)}</ul>
      </details>}
      {provider.authorize && <p><a href={provider.authorize} target="_blank" rel="noreferrer">Authorize GitHub in Coder</a></p>}
      {provider.id==='github' && projectId && repository && <DeployKeys key={projectId} projectId={projectId} repository={repository} />}
      {provider.id==='cloudflare' && provider.connection.status==='CONNECTED' && <DnsInspection />}
      {provider.paste && <form key={provider.id} onSubmit={connect}>
        <label>API key or token <input type="password" name="credential" autoComplete="off" minLength={8} maxLength={4096} required /></label>
        <button disabled={busy}>Validate and save credential</button>
      </form>}
      {(provider.paste || provider.id === 'github') && <button type="button" disabled={busy}
        onClick={() => void action(() => api<Connection>('/' + selection + '/verify', {method: 'POST'}))}>Verify connection</button>}
      {provider.paste && provider.connection.status !== 'NOT_CONNECTED' && <button type="button" disabled={busy}
        onClick={() => {
          if (window.confirm('Remove all stored versions of this marketplace credential?'))
            void action(() => api<Connection>('/' + selection + '/credential', {method: 'DELETE'}));
        }}>Disconnect</button>}
      {['openai','anthropic'].includes(provider.id) && <details>
        <summary>Inference and agent roles</summary>
        <p>Coding agent · {settings.coding_agent?.selected==='claude_code'?'Claude Code':'Codex'} (selected per project above)</p>
        <label>Model<select value={model} onChange={event=>setModel(event.target.value)}>
          <option value="">Select a discovered model</option>
          {provider.connection.items?.map(item=><option key={item}>{item}</option>)}
        </select></label>
        {projectId && <label><span><input className="checkbox" type="checkbox" checked={projectScoped}
          onChange={event=>setProjectScoped(event.target.checked)} /> Apply to the selected project</span></label>}
        <button disabled={busy || !model || provider.connection.status !== 'CONNECTED'}
          onClick={()=>void action(()=>api<Connection>('/'+provider.id+'/verify-model',{
            method:'POST',headers:{'Content-Type':'application/json'},
            body:JSON.stringify({model,project_id:projectScoped?projectId:null}),
          }))}>Run inference check</button>
        <label>Agent role<select value={role} onChange={event=>setRole(event.target.value)}>
          {['prompter','challenger','reviewer'].map(item=><option key={item}>{item}</option>)}
        </select></label>
        <button disabled={busy || !model || provider.connection.status !== 'CONNECTED'}
          onClick={()=>void action(async()=>{
            await api('/ai/roles/'+role,{method:'PUT',headers:{'Content-Type':'application/json'},
              body:JSON.stringify({provider:provider.id,model,project_id:projectScoped?projectId:null})});
            return {message:'Role assignment saved.'};
          })}>Save role assignment</button>
        <ul>{settings.roles.filter(entry=>entry.provider===provider.id && (entry.scope==='global' || entry.scope===projectId)).map(entry=>
          <li key={entry.scope+entry.role}>{entry.role} · {entry.model} · {entry.scope==='global'?'Global':'Selected project'}</li>)}</ul>
        <ul>{settings.checks.filter(entry=>entry.provider===provider.id && (!entry.project_id || entry.project_id===projectId)).slice(0,5).map((entry,index)=>
          <li key={index}>{entry.model} · {label(entry.status)} · {new Date(entry.checked_at).toLocaleString()}
            {entry.usage && ' · '+entry.usage.input_tokens+' input / '+entry.usage.output_tokens+' output tokens'}</li>)}</ul>
      </details>}
      <h4>Setup guide · version {provider.version}</h4>
      <p>Account · {provider.account}</p><p>Permission · {provider.permission}</p>
      {provider.setup_url && <p><a href={provider.setup_url} target="_blank" rel="noreferrer">Open provider credential settings</a></p>}
      <p><a href={provider.documentation} target="_blank" rel="noreferrer">Official documentation</a></p>
      <ol>{provider.steps.map((step, index) => {
        const completed = provider.completed.find(value => value.step === index + 1);
        return <li key={step}><label>
          <span><input className="checkbox" type="checkbox" disabled={busy} checked={Boolean(completed)}
            onChange={event => {
              const checked = event.target.checked;
              void action(async () => { await api('/' + selection + '/guide/' + (index + 1), {
                method: 'PUT', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({version: provider.version, completed: checked}),
              }); return {}; });
            }} /> {step}</span>
          {completed && <small>Marked complete {new Date(completed.completed_at).toLocaleString()}</small>}
        </label></li>;
      })}</ol>
      <p>{provider.verification}</p>
    </section> : <section>
      <label>Search providers<input value={query} onChange={event => setQuery(event.target.value)} /></label>
      {!loaded && !message && <p role="status">Loading integrations…</p>}
      {loaded && !visible.length && <p>No providers match your search.</p>}
      <ul className="integration-directory">{visible.map(item => <li key={item.id}>
        <button type="button" onClick={() => {setSelection(item.id); setModel(''); setMessage('');}}>{item.name}</button>
        <span>{item.category} · {label(item.connection.status)} · {item.capability || 'Guided setup'}</span>
      </li>)}</ul>
    </section>}
    {history.length > 0 && <details>
      <summary>Recent integration activity</summary>
      <ul>{history.filter(item => !selection || item.provider === selection).map(item => <li key={item.id}>
        {providers.find(entry => entry.id === item.provider)?.name || item.provider} · {item.operation} · {label(item.status)} · {item.actor} · {new Date(item.occurred_at).toLocaleString()}
      </li>)}</ul>
    </details>}
  </dialog>;
}
