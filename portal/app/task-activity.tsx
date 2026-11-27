'use client';

import {useEffect, useState} from 'react';
import {ChevronRight} from 'lucide-react';

type Entry = {id: string; kind?: 'message' | 'action'; label: string; status: string; at?: string};
type Stream = {running?: boolean; status?: string; actions: Entry[]};
type Feed = {workspace?: (Stream & {start: number; end: number}) | null; host?: Stream | null; askCc?: Stream | null};
const labels: Record<string, string> = {running: 'In progress', completed: 'Done', failed: 'Failed'};

export function ThinkingIcon() {
  return <picture className="wb-thinking"><source media="(prefers-reduced-motion: reduce)" srcSet="/thinking-still.gif" />
    <img src="/thinking.gif" width={28} height={16} alt="" /></picture>;
}

export default function TaskActivity({projectId, taskId, requestId, running, queued, expanded, onExpand}: {
  projectId: string; taskId: string; requestId?: string; running: boolean; queued: boolean; expanded: boolean; onExpand: (open: boolean) => void;
}) {
  const [feed, setFeed] = useState<Feed>();
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (!expanded) return;
    const controller = new AbortController();
    const url = '/api/projects/' + projectId + (requestId ? '/mcp-tasks/'+requestId : '/tasks/'+taskId) + '/activity';
    let timer: ReturnType<typeof setTimeout>;
    let lastEnd = 0;
    const history = new Map<string, Entry>();
    async function page(before?: number): Promise<Feed> {
      const response = await fetch(url + (before ? '?before=' + before : ''), {
        cache: 'no-store', signal: AbortSignal.any([controller.signal, AbortSignal.timeout(12000)]),
      });
      if (!response.ok) throw Error();
      return response.json();
    }
    async function refresh() {
      try {
        const latest = await page();
        if (!controller.signal.aborted) {
          setFeed(current => ({...latest, workspace: current?.workspace ?? latest.workspace}));
        }
        let batch = latest.workspace;
        const pages = batch ? [batch.actions] : [];
        while (batch && batch.start > lastEnd) {
          const previous = (await page(batch.start)).workspace;
          if (!previous || previous.start >= batch.start || previous.end !== batch.start) throw Error();
          pages.push(previous.actions);
          batch = previous;
        }
        // Older pages are inserted first. Edits to an item retain its original position.
        for (const entries of pages.reverse()) for (const entry of entries) history.set(entry.id, entry);
        if (latest.workspace) {
          lastEnd = latest.workspace.end;
          latest.workspace.actions = [...history.values()];
        }
        if (!controller.signal.aborted) {setFeed(latest); setError('');}
      } catch {
        if (!controller.signal.aborted) setError('Could not load the complete work history.');
      }
      if (!controller.signal.aborted && (running || queued)) timer = setTimeout(refresh, 2000);
    }
    void refresh();
    return () => {controller.abort(); clearTimeout(timer);};
  }, [expanded, projectId, taskId, requestId, running, queued, retry]);

  const streams = [
    {name: 'Codex', data: feed?.host},
    {name: 'Coder workspace', data: feed?.workspace},
    {name: 'Ask CC', data: feed?.askCc},
  ];
  const hasEntries = streams.some(({data}) => data?.actions.length);
  return <details className="wb-work-history" id={'work-' + (requestId || taskId)} open={expanded} onToggle={event => onExpand(event.currentTarget.open)}>
    <summary>
      <ChevronRight className="wb-work-chevron" size={15} aria-hidden="true" />
      {running && <ThinkingIcon />}
      <span>{running ? 'Processing' : queued ? 'Queued' : 'Work messages'}</span>
      {running && <span className="wb-work-caption">Work messages</span>}
    </summary>
    {expanded && <div className="wb-work-feed">
      {error && <p role="status">{error} <button type="button" onClick={() => setRetry(n => n + 1)}>Retry</button></p>}
      {!feed && !error && <p role="status">Loading work messages…</p>}
      {feed && streams.map(({name, data}) => data?.actions.length ? <section key={name} aria-label={name + ' work messages'}>
        <h4>{name}</h4>
        <ol>{data.actions.map(entry => <li key={entry.id} data-kind={entry.kind ?? 'action'}>
          <div>{entry.label}</div>
          <small>{entry.kind !== 'message' && (entry.status === 'running' && !(data.running || data.status === 'running') ?
            'Interrupted' : labels[entry.status])}
            {entry.at && <time dateTime={entry.at}>{new Date(entry.at).toLocaleString(undefined, {month:'short',day:'numeric',hour:'numeric',minute:'2-digit'})}</time>}
          </small>
        </li>)}</ol>
      </section> : null)}
      {feed && !hasEntries && !error && <p>{running ? 'Waiting for the first work message…' : 'No work messages recorded.'}</p>}
      {feed?.workspace === null && <p>Coder workspace history is unavailable.</p>}
      {feed?.askCc === null && <p>Ask CC history is unavailable.</p>}
    </div>}
  </details>;
}
