'use client';

import { useEffect, useState, type FormEvent } from 'react';

const taskLabels: Record<string,string> = {active:'Active',blocked:'Blocked',ready_for_review:'Ready for review',done:'Done'};

type Criterion = { id: string; description: string; mandatory: boolean; depends_on: number[] };
type Attempt = { criterion_id: string; branch: string; head: string | null; fingerprint: string; exit_code: number | null };
type Objective = {
  id: string; status: string; version: number; original_request: string; expected_outcome: string;
  criteria: Criterion[]; scope: string[]; constraints: string[]; dependencies: string[];
  progress: {
    completedCriteria: string[]; outstandingCriteria: string[]; blockedCriteria: string[];
    verifiedEvidence: string[]; blockers: string[]; nextAction: string | null; attempts: Attempt[];
    active: { criterion_id: string; agent: string; branch: string; worktree: string }[];
    checkpoints: { criterion_id: string; decision: string; evidence: string; invalidated_at: string | null }[];
  };
};

export default function Objectives({ projectId }: { projectId: string }) {
  const base = '/api/projects/' + projectId + '/objectives';
  const [objectives, setObjectives] = useState<Objective[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [edit, setEdit] = useState<string | null>(null);

  async function request(path = '', method = 'GET', data?: object) {
    const response = await fetch(base + path, {
      method, ...(data ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) } : {}),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Objective request failed.');
    return result;
  }

  useEffect(() => {
    let closed = false;
    const refresh = () => void request().then(
      rows => { if (!closed) setObjectives(rows); },
      failure => { if (!closed) setError(failure.message); },
    );
    refresh();
    const timer = setInterval(refresh, 5000);
    return () => { closed = true; clearInterval(timer); };
  }, [base]);

  async function mutate(operation: () => Promise<void>) {
    setBusy(true);
    setError('');
    try {
      await operation();
      setObjectives(await request());
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Objective request failed.');
    } finally {
      setBusy(false);
    }
  }

  function save(event: FormEvent<HTMLFormElement>, objective?: Objective) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    const value = (key: string) => String(values.get(key) || '');
    const lines = (key: string) => value(key).split('\n').map(line => line.trim()).filter(Boolean);
    void mutate(async () => {
      const definition = {
        expected_outcome: value('expected'),
        criteria: lines('criteria').map((line, index) => {
          const previous = objective?.criteria[index];
          return { ...(previous ?? { mandatory: true, depends_on: [] }), description: line };
        }),
        scope: lines('scope'), constraints: lines('constraints'), dependencies: lines('dependencies'),
      };
      if (objective) {
        await request('/' + objective.id, 'PUT', { ...definition, version: objective.version, reason: value('reason') });
        setEdit(null);
      } else {
        await request('', 'POST', { ...definition, original_request: value('original') });
        form.reset();
      }
    });
  }

  function record(event: FormEvent<HTMLFormElement>, objective: Objective, attempt: Attempt) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    void mutate(async () => {
      await request('/' + objective.id + '/evidence', 'POST', {
        version: objective.version, criterion_id: attempt.criterion_id, fingerprint: attempt.fingerprint,
        evidence: values.get('evidence'), decision: values.get('decision'),
      });
      form.reset();
    });
  }

  function definitionForm(objective?: Objective) {
    return <form onSubmit={event => save(event, objective)}>
      {!objective && <label>Original request<textarea name="original" required maxLength={30000} /></label>}
      <label>Expected outcome<textarea name="expected" required maxLength={2000} defaultValue={objective?.expected_outcome} /></label>
      <label>Mandatory acceptance criteria, one per line<textarea name="criteria" required defaultValue={objective?.criteria.map(item => item.description).join('\n')} /></label>
      <label>Scope, one item per line<textarea name="scope" defaultValue={objective?.scope.join('\n')} /></label>
      <label>Constraints, one per line<textarea name="constraints" defaultValue={objective?.constraints.join('\n')} /></label>
      <label>Dependencies, one per line<textarea name="dependencies" defaultValue={objective?.dependencies.join('\n')} /></label>
      {objective && <label>Reason for amendment<input name="reason" required maxLength={2000} /></label>}
      <button disabled={busy}>{objective ? 'Approve new contract version' : 'Create task'}</button>
    </form>;
  }

  return <section className="panel" aria-label="Objective governance">
    <h2>Task acceptance</h2>
    {error && <p role="alert" className="error">{error}</p>}
    <details><summary>Define a feature task</summary>{definitionForm()}</details>
    {objectives.map(objective => <article key={objective.id} className="panel">
      <h3>{objective.expected_outcome}</h3>
      <p>{taskLabels[objective.status] || objective.status} · Version {objective.version} · Objective ID <code>{objective.id}</code></p>
      <details><summary>Original request</summary><p style={{ whiteSpace: 'pre-wrap' }}>{objective.original_request}</p></details>
      {objective.progress.nextAction && <p>Next action · {objective.progress.nextAction}</p>}
      {objective.progress.blockers.map((blocker, index) => <p key={index} className="error">{blocker}</p>)}
      <ol>{objective.criteria.map(criterion => {
        const progress = objective.progress;
        const verified = progress.completedCriteria.includes(criterion.id);
        const blocked = progress.blockedCriteria.includes(criterion.id);
        const active = progress.active.find(item => item.criterion_id === criterion.id);
        const attempt = progress.attempts.find(item => item.criterion_id === criterion.id);
        const checkpoint = progress.checkpoints.find(item => item.criterion_id === criterion.id);
        return <li key={criterion.id}>
          <p><code>{criterion.id}</code> · {criterion.description} · {verified ? 'Verified' : blocked ? 'Blocked' : active ? 'Assigned' : 'Outstanding'}</p>
          {active && <p>{active.agent} · <code>{active.branch}</code> · {active.worktree}</p>}
          {checkpoint && <p>{checkpoint.invalidated_at ? 'Stale evidence' : 'Evidence'} · {checkpoint.evidence}</p>}
          {attempt && !active && <details><summary>Record evidence for {criterion.id}</summary>
            <form onSubmit={event => record(event, objective, attempt)}>
              <p><code>{attempt.branch}</code> · Exit {attempt.exit_code ?? 'pending'}</p>
              <label>Observed evidence<textarea name="evidence" required maxLength={2000} /></label>
              <label>Decision<select name="decision"><option value="verified">Verified outcome</option><option value="blocked">Blocked</option></select></label>
              <button disabled={busy}>Record decision</button>
            </form>
          </details>}
        </li>;
      })}</ol>
      <button className="secondary" onClick={() => setEdit(edit === objective.id ? null : objective.id)}
        disabled={objective.progress.active.length > 0}>Amend contract</button>
      {edit === objective.id && definitionForm(objective)}
    </article>)}
  </section>;
}
