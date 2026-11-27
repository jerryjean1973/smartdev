'use client';

import { useEffect, useRef, useState, type FormEvent } from 'react';
import Workbench from './workbench';

type Project = {
  id: string;
  name: string;
  repository: string;
  runtime: string;
  status: 'queued' | 'provisioning' | 'created' | 'failed';
  workspace_url: string | null;
  error: string | null;
  archived: boolean;
};

const statusLabels = {
  queued: 'Queued',
  provisioning: 'Requesting workspace',
  created: 'Workspace created',
  failed: 'Request failed',
};

async function request(path: string, method = 'GET', body?: object) {
  return fetch('/api' + path, {
    method,
    ...(body ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}),
  });
}

async function failure(response: Response) {
  const result = await response.json().catch(() => ({}));
  return typeof result.detail === 'string' ? result.detail : `Request failed (HTTP ${response.status})`;
}

export default function Portal() {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [signedIn, setSignedIn] = useState<boolean | null>(null);
  const [error, setError] = useState('');
  const [repositories, setRepositories] = useState<string[]>([]);
  const [githubConnect, setGithubConnect] = useState<string | null>(null);
  const [nextRepoPage, setNextRepoPage] = useState<number | null>(1);
  const [githubConfigured, setGithubConfigured] = useState<boolean | null>(null);
  const [selectedRepos, setSelectedRepos] = useState<string[]>([]);
  const [importMessage, setImportMessage] = useState('');
  const [repository, setRepository] = useState('');
  const [busy, setBusy] = useState(false);
  const projectId = useRef<string | null>(null);

  async function refresh() {
    const response = await request('/projects');
    if (response.status === 401) {
      setSignedIn(false);
      setProjects(null);
      return;
    }
    if (!response.ok) throw new Error(await failure(response));
    setProjects(await response.json());
    setSignedIn(true);
  }

  useEffect(() => {
    const update = () => void refresh().catch(() => setError('The portal API is unavailable.'));
    update();
    const timer = setInterval(update, 4000);
    return () => clearInterval(timer);
  }, []);

  async function action(operation: () => Promise<void>) {
    setError('');
    setBusy(true);
    try {
      await operation();
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'The request failed.');
    } finally {
      setBusy(false);
    }
  }

  async function loadRepositories(page: number) {
    const response = await request('/github/repositories?page=' + page);
    if (!response.ok) throw new Error(await failure(response));
    const result = await response.json();
    setGithubConnect(result.connect);
    setGithubConfigured(result.configured);
    setRepositories(previous => page === 1 ? result.repositories : [...previous, ...result.repositories]);
    setNextRepoPage(result.next);

  }

  async function importSelected() {
    let queued = 0, existing = 0;
    for (let offset = 0; offset < selectedRepos.length; offset += 100) {
      const response = await request('/github/import', 'POST', {repositories: selectedRepos.slice(offset, offset + 100)});
      if (!response.ok) throw new Error(await failure(response));
      const result = await response.json();
      queued += result.queued.length;
      existing += result.existing.length;
      setImportMessage(queued + ' queued · ' + existing + ' already in SmartDev');
    }
    setSelectedRepos([]);
  }

  function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const password = new FormData(form).get('password');
    void action(async () => {
      const response = await request('/login', 'POST', { password });
      if (!response.ok) throw new Error(await failure(response));
      form.reset();
    });
  }

  function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = Object.fromEntries(new FormData(form));
    projectId.current ??= crypto.randomUUID();
    const id = projectId.current;
    void action(async () => {
      const response = await request('/projects/' + id, 'PUT', data);
      if (!response.ok) {
        if (response.status < 500) projectId.current = null;
        throw new Error(await failure(response));
      }
      projectId.current = null;
      setRepository('');
      form.reset();
    });
  }

  function updateProject(id: string, name: string, verb: 'retry' | 'archive') {
    if (verb === 'archive' && !window.confirm(`Archive ${name} in the portal? The Coder workspace will still be accessible in Coder.`)) return;
    void action(async () => {
      const response = await request(`/projects/${id}/${verb}`, 'POST');
      if (!response.ok) throw new Error(await failure(response));
    });
  }

  const signOut = <button className="secondary" disabled={busy} onClick={() => void action(async () => {
    const response = await request('/logout', 'POST');
    if (!response.ok) throw new Error(await failure(response));
  })}>Sign out</button>;

  if (signedIn) return <>
    {error && <p role="alert" className="error">{error}</p>}
    <Workbench projects={projects ?? []} signOut={signOut}
      managementOnly={window.location.hostname === 'smartdev.youkandoit.com'}
      codingOnly={window.location.hostname === 'coder.smartdev.youkandoit.com'
        || /^smd-[a-f0-9]{32}\./.test(window.location.hostname)}
      updateProject={(project, verb) => updateProject(project.id, project.name, verb)}
      createForm={(
      <form className="panel" onSubmit={create}>
        <h2>New project</h2>
        <div className="fields">
          <label>Project name<input name="name" required maxLength={80} /></label>
          <label>Existing GitHub repository <input name="repository" placeholder="owner/repo (optional)" maxLength={140} value={repository} onChange={event => setRepository(event.target.value)} /></label>
          <label>Runtime <select name="runtime"><option value="node">Node.js</option><option value="python">Python</option><option value="other">Other</option></select></label>
        </div>
        <div className="actions">
          <button type="button" className="secondary" disabled={busy} onClick={() => void action(() => loadRepositories(nextRepoPage ?? 1))}>{repositories.length && nextRepoPage ? 'Load more repositories' : 'List GitHub repositories'}</button>
          {githubConfigured && githubConnect && <a href={githubConnect} target="_blank" rel="noreferrer">Connect GitHub in Coder</a>}
          {githubConfigured === false && <p>GitHub App setup is required. Install a GitHub App for the repositories you want to import. SmartDev needs its client ID, installation ID, client secret and private key. Use the Coder callback <code>{githubConnect}/callback</code>.</p>}
          {repositories.length > 0 && <fieldset>
            <legend>Repositories in this installation</legend>
            <button type="button" onClick={() => setSelectedRepos([...repositories])}>Select loaded repositories</button>
            {repositories.map(name => <label key={name}>
              <input className="checkbox" type="checkbox" checked={selectedRepos.includes(name)} onChange={event =>
                setSelectedRepos(current => event.target.checked ? [...current, name] : current.filter(value => value !== name))} /> {name}
            </label>)}
            <button type="button" disabled={busy || !selectedRepos.length} onClick={() => void action(importSelected)}>Import selected repositories</button>
          </fieldset>}
          {importMessage && <p role="status">{importMessage}</p>}
        </div>
        <button disabled={busy}>Create development workspace</button>
      </form>
      )} />
  </>;

  return <main className="sign-in">
    <div className="sign-in-brand"><span className="brand-mark" aria-hidden="true">S</span>SmartDev</div>
    <h1>Development Command Center</h1>
    {error && <p role="alert" className="error">{error}</p>}
    {signedIn === null && <p role="status">Connecting to the portal…</p>}
    {signedIn === false && <form className="panel" onSubmit={login}>
      <h2>Owner sign-in</h2>
      <label>Password<input name="password" type="password" autoComplete="current-password" required maxLength={512} /></label>
      <button disabled={busy}>Sign in</button>
    </form>}
  </main>;
}
