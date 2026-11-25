'use client';

import { useState } from 'react';

export type BoardTask = {
  id: string;
  display_name?: string | null;
  original_request: string;
  expected_outcome: string;
  status: string;
  progress: {
    completedCriteria: string[];
    outstandingCriteria: string[];
    blockedCriteria: string[];
    verifiedEvidence: string[];
    blockers: string[];
    nextAction: string | null;
    executionSteps?: {
      id: string;
      criterion_id: string;
      agent: string;
      branch: string;
      worktree: string;
      started_at: string;
      finished_at: string | null;
      exit_code: number | null;
    }[];
  };
};

const lanes = [
  {status: 'active', title: 'Active'},
  {status: 'blocked', title: 'Blocked'},
  {status: 'ready_for_review', title: 'Ready for review'},
  {status: 'done', title: 'Done'},
];

export default function TaskBoard({projectId, tasks, error}: {
  projectId: string;
  tasks?: BoardTask[];
  error?: string;
}) {
  const [taskId, setTaskId] = useState<string>();
  const task = tasks?.find(row => row.id === taskId);
  const otherTasks = tasks?.filter(row => !lanes.some(lane => lane.status === row.status)) ?? [];

  return <div className="wb-content wb-task-board">
    <h2>Kanban</h2>
    {error && <p role="alert" className="error">{error}{tasks && ' Showing the last loaded tasks.'}</p>}
    {!tasks && !error && <p role="status">Loading tasks…</p>}
    {tasks?.length === 0 && <p>No tasks on this project’s board.</p>}
    {!!tasks?.length && <div className="wb-task-lanes">
      {[...lanes, ...(otherTasks.length ? [{status: '', title: 'Other'}] : [])].map(lane => {
        const rows = lane.status ? tasks.filter(row => row.status === lane.status) : otherTasks;
        return <section className="panel" key={lane.title}>
          <h3>{lane.title}</h3>
          {rows.length ? <ul>{rows.map(row => <li key={row.id}>
            <button aria-pressed={task?.id === row.id} onClick={() => setTaskId(row.id)}>
              {row.display_name || row.expected_outcome}
            </button>
          </li>)}</ul> : <p>No tasks</p>}
        </section>;
      })}
    </div>}
    {task && <section className="panel" aria-labelledby="kanban-task-title">
      <h3 id="kanban-task-title">{task.display_name || task.expected_outcome}</h3>
      <dl>
        <dt>Project ID</dt><dd><code>{projectId}</code></dd>
        <dt>Task ID</dt><dd><code>{task.id}</code></dd>
        <dt>Status</dt><dd>{lanes.find(lane => lane.status === task.status)?.title ?? task.status}</dd>
        <dt>Original request</dt><dd>{task.original_request}</dd>
        <dt>Expected outcome</dt><dd>{task.expected_outcome}</dd>
        {task.progress.nextAction && <><dt>Next action</dt><dd>{task.progress.nextAction}</dd></>}
      </dl>
      <p>{task.progress.completedCriteria.length} verified · {task.progress.outstandingCriteria.length} outstanding · {task.progress.blockedCriteria.length} blocked</p>
      <h4>Verified evidence</h4>
      {task.progress.verifiedEvidence.length
        ? <ul>{task.progress.verifiedEvidence.map((item, index) => <li key={index}>{item}</li>)}</ul>
        : <p>No verified evidence recorded.</p>}
      {!!task.progress.blockers.length && <>
        <h4>Blockers</h4>
        <ul>{task.progress.blockers.map((item, index) => <li key={index}>{item}</li>)}</ul>
      </>}
      <h4>Recent executions</h4>
      {task.progress.executionSteps?.length ? <ol>{task.progress.executionSteps.map(step => <li key={step.id}>
        <code>{step.id}</code> · {step.criterion_id} · {step.agent}
        <div>{step.branch} · {step.worktree}</div>
        <div>Started {new Date(step.started_at).toLocaleString()}</div>
        <div>{step.finished_at
          ? <>Finished {new Date(step.finished_at).toLocaleString()} · {step.exit_code === null ? 'No exit code recorded' : 'Exit ' + step.exit_code}</>
          : 'Running'}</div>
      </li>)}</ol> : <p>No executions recorded.</p>}
    </section>}
  </div>;
}
