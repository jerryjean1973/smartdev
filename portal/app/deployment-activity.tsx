'use client';

import {useEffect, useState} from 'react';
import styles from './deployment-console.module.css';
import SmdConnections from './smd-connections';

type Job = {
  id: string;
  project_name: string;
  project_manifest: {id: string; name: string; repository: string; revision: string}[] | null;
  release_revision: string | null;
  target_tenant: string | null;
  queued_at: string;
  instance_id: string | null;
  reconciled_at: string | null;
  status: 'queued' | 'processing' | 'needs_attention' | 'failed' | 'deployed';
  note: string | null;
};

const labels = {
  queued: 'Queued',
  processing: 'In progress',
  needs_attention: 'Needs attention',
  failed: 'Failed',
  deployed: 'Deployed',
};

export default function DeploymentActivity() {
  const [jobs, setJobs] = useState<Job[]>();
  const [error, setError] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    async function refresh() {
      try {
        const response = await fetch('/api/admin/smd-deploy-tasks', {cache: 'no-store', signal: controller.signal});
        if (!response.ok) throw Error();
        const result = await response.json();
        if (!Array.isArray(result)) throw Error();
        if (!controller.signal.aborted) {
          setJobs(result);
          setError(false);
        }
      } catch {
        if (!controller.signal.aborted) setError(true);
      }
    }
    void refresh();
    const timer = setInterval(refresh, 4000);
    return () => {
      clearInterval(timer);
      controller.abort();
    };
  }, []);

  return <section className={styles.deploymentActivity} aria-labelledby="deployment-activity-title">
    <h3 id="deployment-activity-title">Deployment activity</h3>
    {error && <p role="status">Could not refresh deployment activity. Retrying automatically.</p>}
    {!error && !jobs?.length && <p>{jobs ? 'No deployment requests yet.' : 'Loading deployment activity…'}</p>}
    {!!jobs?.length && <ul>
      {jobs.map(job => {
        const percent = job.status === 'deployed' ? 100 : job.instance_id ? 50 : job.status === 'processing' ? 25 : 0;
        const names = job.project_manifest?.map(project => project.name).join(', ') || job.project_name;
        return <li key={job.id}>
          <details>
            <summary><strong>{names}</strong> · <span>{job.reconciled_at ? 'Reconciled' : labels[job.status]}</span> ·{' '}
              <time dateTime={job.queued_at}>{new Date(job.queued_at).toLocaleString()}</time></summary>
            {job.note && <p>{job.note}</p>}
            <p>Task <code>{job.id}</code></p>
            {job.release_revision && <p>SMD release <code>{job.release_revision}</code></p>}
            {job.target_tenant && <p>Tenant {job.target_tenant}</p>}
            {job.project_manifest?.map(project => <p key={project.id}>
              {project.repository} · <code>{project.revision}</code>
            </p>)}
            {job.status === 'deployed' && <SmdConnections taskId={job.id}
              projectNames={Object.fromEntries((job.project_manifest ?? []).map(project => [project.id, project.name]))} />}
          </details>
          {!job.reconciled_at && <div>
            <progress aria-label={names + ' deployment progress'} max={100} value={percent} /> {percent}%
          </div>}
        </li>;
      })}
    </ul>}
  </section>;
}
