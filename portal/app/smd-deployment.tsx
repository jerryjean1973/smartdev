'use client';

import {useEffect, useState} from 'react';
import {createPortal} from 'react-dom';
import {Rocket} from 'lucide-react';
import SmdConnections from './smd-connections';

type Deployment = {
  status: 'queued' | 'processing' | 'deployed' | 'failed' | 'needs_attention';
  instance_id: string | null;
  observed_revision: string | null;
  note: string | null;
  release_revision: string | null;
  monthly_cost_usd: number | null;
  id: string;
};

type Status = {
  deployment: Deployment | null;
  reconciled_deployment: {id: string; reconciled_at: string} | null;
  configured: boolean;
  offer_token: string | null;
  monthly_cost_usd: number;
  release_revision: string | null;
  configuration_note: string | null;
};

const labels: Record<Deployment['status'], string> = {
  queued: 'Queued',
  processing: 'In progress',
  deployed: 'Deployed',
  failed: 'Deployment failed',
  needs_attention: 'Needs attention',
};

const descriptions: Record<Deployment['status'], string> = {
  queued: 'Waiting for the automatic deployment runner.',
  processing: 'The runner is working on this deployment.',
  deployed: 'Deployment finished.',
  failed: 'The runner stopped after an error.',
  needs_attention: 'Deployment is paused until the reported issue is resolved.',
};

export default function SmdDeployment({projectId, projectName, compact = false, actionContainer, versionContainer, blocker}: {projectId: string; projectName: string; compact?: boolean; actionContainer?: HTMLElement | null; versionContainer?: HTMLElement | null; blocker?: string}) {
  const endpoint = '/api/projects/' + projectId + '/development/smd';
  const [status, setStatus] = useState<Status>();
  const [showProgress, setShowProgress] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState('');
  const [loadError, setLoadError] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    setStatus(undefined);
    setShowProgress(false);
    setError('');
    async function refresh() {
      try {
        const response = await fetch(endpoint, {cache: 'no-store', signal: controller.signal});
        if (!response.ok) throw Error();
        const next: Status = await response.json();
        if (!controller.signal.aborted) {
          setStatus(previous => {
            if (previous?.deployment && !next.deployment
                && next.reconciled_deployment?.id !== previous.deployment.id) return previous;
            return next;
          });
          setLoadError('');
        }
      } catch {
        if (!controller.signal.aborted) setLoadError('Could not load deployment status.');
      }
    }
    void refresh();
    const timer = setInterval(refresh, 4000);
    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, [endpoint]);

  async function deploy() {
    if (blocker || !status?.offer_token || status.deployment || sending) return;
    setSending(true);
    setError('');
    try {
      const response = await fetch(endpoint, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({offer_token: status.offer_token}),
      });
      const result = await response.json().catch(() => null);
      if (!response.ok || !result) {
        throw Error(typeof result?.detail === 'string' ? result.detail : 'Could not queue deployment.');
      }
      setStatus(previous => previous ? {...previous, deployment: result} : previous);
      setShowProgress(true);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Could not queue deployment.');
    } finally {
      setSending(false);
    }
  }

  const job = status?.deployment;
  const blockedWithActiveWork = !job && !!blocker && !!status?.configured && !!status?.offer_token;
  const percent = job?.status === 'deployed' && job.instance_id && job.observed_revision ? 100
    : job?.instance_id ? 50 : job?.status === 'processing' ? 25 : blockedWithActiveWork ? 10 : 0;
  const cost = job ? job.monthly_cost_usd : status?.monthly_cost_usd;
  const revision = job ? job.release_revision : status?.release_revision;
  const disabledReason = job ? labels[job.status] : blocker || loadError ||
    (status && !status.configured ? 'Release installer is not configured.' : !status ? 'Checking deployment availability…' : '');
  const deployButton = <div className="smd-deploy-action"><button type="button" disabled={!!job || !!blocker || !status?.offer_token || sending} onClick={() => void deploy()}>
        <Rocket size={16} aria-hidden /> {sending ? 'Queuing…' : 'Deploy SMD'}
      </button>
      {disabledReason && <div className="smd-deploy-reason"><span role="status">{projectName} · {disabledReason}</span>
        {job?.status === 'needs_attention' && job.note && <details><summary>Why?</summary><p><strong>{projectName}</strong><br />{job.note}</p></details>}
      </div>}
    </div>;
  const versionSelect = <select aria-label="Release version" value={revision ?? ''} disabled>
        <option value={revision ?? ''}>{revision || 'No release version configured'}</option>
      </select>;
  return <section aria-label="SMD deployment">
    {!compact && <h3>SMD deployment</h3>}
    <p>Vultr · Atlanta · 4 vCPUs · 8 GB RAM · Ubuntu 24.04</p>
    {cost != null && <p>VM plan · USD ${cost}/month</p>}
    {versionContainer ? createPortal(versionSelect, versionContainer) : !compact && <label>Release version {versionSelect}</label>}
    {job ? <div role="status">
      <p>{labels[job.status]}</p>
      {(showProgress || job.status === 'queued' || job.status === 'processing') && <div data-running={job.status === 'processing'}>
        <strong>{percent}%</strong>
        <progress aria-label="Deployment progress" value={percent} max={100} />
      </div>}
      <p>{job.note || descriptions[job.status]}</p>
      {job.instance_id && <p>Vultr instance <code>{job.instance_id}</code></p>}
      {job.observed_revision && <p>Running revision <code>{job.observed_revision}</code></p>}
      <p>Task <code>{job.id}</code></p>
      {job.status === 'deployed' && <SmdConnections taskId={job.id} />}
    </div> : <>
      {blockedWithActiveWork && <div role="status" data-running="true">
        <p>Unblock work in progress</p>
        <div>
          <strong>{percent}%</strong>
          <progress aria-label="Unblock progress" value={percent} max={100} />
        </div>
        <p>Fix work is underway to clear this blocker before a live deployment can start.</p>
      </div>}
      {status?.reconciled_deployment && <p role="status">
        The legacy request was reconciled. Review the offer and click Deploy SMD to start a new request.
      </p>}
      {status?.configuration_note && <p role="status">{status.configuration_note}</p>}
      {blocker && <p role="status">{blocker}</p>}
    </>}
    {actionContainer ? createPortal(deployButton, actionContainer) : deployButton}
    {(error || loadError) && <p role="alert">{error || loadError}</p>}
  </section>;
}
