'use client';

import { useEffect, useState, type FormEvent } from 'react';

type Note = {
  id: string;
  project_id: string;
  title: string;
  body: string;
  provenance: string;
  scope: string;
  state: string;
  created_at: string;
  superseded: boolean;
};

type Execution = {
  id: string;
  branch: string;
  started_at: string;
  finished_at: string | null;
  exit_code: number | null;
  changed_files: string[];
  head_after: string | null;
  context_note_ids: string[];
};

export default function Memory({ projectId }: { projectId: string }) {
  const [executions, setExecutions] = useState<Execution[]>([]);
  const [notes, setNotes] = useState<Note[]>([]);
  const [query, setQuery] = useState('');
  const [shared, setShared] = useState(false);
  const [error, setError] = useState('');
  const [previous, setPrevious] = useState<Note | null>(null);
  const endpoint = '/api/projects/' + projectId + '/memory';

  async function load() {
    const response = await fetch(endpoint + '?' + new URLSearchParams({ q: query, shared: String(shared) }));
    if (!response.ok) throw new Error('Memory search failed.');
    setNotes(await response.json());
  }

  useEffect(() => {
    void fetch(endpoint).then(async response => {
      if (!response.ok) throw new Error();
      setNotes(await response.json());
    }).catch(() => setError('Could not load project memory.'));
    const refreshHistory = () => void fetch(endpoint.replace(/memory$/, 'executions'))
      .then(async response => { if (response.ok) setExecutions(await response.json()); })
      .catch(() => setError('Could not load execution history.'));
    refreshHistory();
    const timer = setInterval(refreshHistory, 5000);
    return () => clearInterval(timer);
  }, [endpoint]);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    setError('');
    try {
      const response = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...Object.fromEntries(new FormData(form)), supersedes: previous?.id }),
      });
      if (!response.ok) {
        const result = await response.json();
        throw new Error(typeof result.detail === 'string' ? result.detail : 'Check note fields and remove credentials.');
      }
      form.reset();
      setPrevious(null);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not save memory.');
    }
  }

  return <section className="panel">
    <h2>Memory &amp; Context</h2>
    <form onSubmit={event => {
      event.preventDefault();
      setError('');
      void load().catch(() => setError('Memory search failed.'));
    }}>
      <label>Search decisions, fixes, and references<input value={query} maxLength={300} onChange={event => setQuery(event.target.value)} /></label>
      <label><span><input className="checkbox" type="checkbox" checked={shared} onChange={event => setShared(event.target.checked)} /> Include verified shared notes</span></label>
      <button className="secondary">Search</button>
    </form>
    {error && <p role="alert" className="error">{error}</p>}
    {notes.map(note => <article className="panel" key={note.id} id={`note-${note.id}`}>
      <h3>{note.title}</h3>
      <p>{note.scope} · {note.state} · {note.superseded ? 'Superseded · ' : ''}{new Date(note.created_at).toLocaleString()}</p>
      <p>{note.body}</p>
      <p>Evidence · {note.provenance}</p>
      {!note.superseded && note.project_id === projectId && note.scope !== 'agent' && <button className="secondary" onClick={() => setPrevious(note)}>Revise or invalidate</button>}
    </article>)}
    {executions.length > 0 && <details>
      <summary>Recent Codex executions</summary>
      {executions.map(run => <article key={run.id} className="panel">
        <h3>{run.branch}</h3>
        <p>{new Date(run.started_at).toLocaleString()} · {run.finished_at ? `Exit status ${run.exit_code}` : 'No outcome recorded'}</p>
        {run.head_after && <p>HEAD · <code>{run.head_after}</code></p>}
        {run.changed_files?.length > 0 && <details><summary>Changed files</summary>
          <ul>{run.changed_files.map(name => <li key={name}><code>{name}</code></li>)}</ul>
        </details>}
        {run.context_note_ids.length > 0 && <details><summary>Context note IDs</summary>
          <ul>{run.context_note_ids.map(id => <li key={id}>{notes.some(note => note.id === id) ? <a href={`#note-${id}`}>{notes.find(note => note.id === id)?.title}</a> : <code>{id}</code>}</li>)}</ul>
        </details>}
      </article>)}
    </details>}
    <form onSubmit={save} key={previous?.id ?? 'new'}>
      <h3>{previous ? 'Revise memory' : 'Add memory'}</h3>
      <label>Title<input name="title" required maxLength={160} defaultValue={previous?.title} /></label>
      <label>Summary<textarea name="body" required maxLength={4000} defaultValue={previous?.body} /></label>
      <label>Evidence reference<input name="provenance" required maxLength={300} defaultValue={previous?.provenance}
        aria-describedby="memory-provenance-format" /></label>
      <p id="memory-provenance-format">For a checkout file, use <code>repo:path#sha256=digest</code> with its current SHA-256 digest.</p>
      <label>Visibility<select name="scope" defaultValue={previous?.scope ?? 'project'}><option value="project">Project only</option><option value="shared">Share when verified</option></select></label>
      <label>State<select name="state">{['proposed','implemented','tested','verified','failed','reverted','invalidated'].map(state => <option key={state}>{state}</option>)}</select></label>
      <button>Save memory</button>
      {previous && <button type="button" className="secondary" onClick={() => setPrevious(null)}>Cancel</button>}
    </form>
  </section>;
}
