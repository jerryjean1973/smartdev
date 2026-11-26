'use client';

import {useEffect, useState} from 'react';

const options = [
  ['public_git_read', 'Allow public Git reads'],
  ['local_mig', 'Run local MIG'],
  ['local_bripe', 'Run local BRIPe'],
] as const;

type Option = typeof options[number][0];
type Selection = Partial<Record<Option, boolean>>;
type ReviewedPlan = {destination: Record<string, unknown>;
  default_host_image: {version: string; digest: string; vultr_snapshot_id: string} | null;
  destination_label: string; downloadable: boolean;
  portal_origin: string; expires_at: string};

type Installation = {id: string; name: string; destination_label: string | null; status: string | null; operation_id: string | null;
  result: {receipt: {vultr_instance_id?: string; resource_id?: string}} | null};

export default function HostInstallations() {
  const [projects, setProjects] = useState<Installation[]>([]);
  const [selected, setSelected] = useState<Record<string, Selection>>({});
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [review, setReview] = useState<Record<string, ReviewedPlan>>({});

  async function refresh() {
    const response = await fetch('/api/host-installations', {cache: 'no-store'});
    if (!response.ok) throw new Error('Host installation inventory is unavailable.');
    setProjects(await response.json());
  }

  useEffect(() => {
    const update = () => void refresh().catch(cause => setError(cause.message));
    update();
    const timer = setInterval(update, 5000);
    return () => clearInterval(timer);
  }, []);

  const payload = projects.filter(project =>
    (!project.status || project.status === 'expired') && selected[project.id]
  ).map(project => ({project_id: project.id, ...selected[project.id]}));

  async function submit(path: string, data: object) {
    const response = await fetch('/api/host-installations' + path, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(data),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => null);
      throw new Error(typeof data?.detail === 'string' ? data.detail : 'Host installation request failed.');
    }
    return response;
  }

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError('');
    try {
      await action();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Host installation request failed.');
    } finally {
      setBusy(false);
    }
  }

  function clearReview() {
    setReview({});
  }

  function reviewPlans() {
    clearReview();
    void run(async () => {
      setReview(Object.fromEntries(await Promise.all(payload.map(async project =>
        [project.project_id, await (await submit('/preflight', project)).json()] as const
      ))));
    });
  }

  function download(project: typeof payload[number]) {
    void run(async () => {
      const response = await submit('/download', project);
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a');
      link.href = url;
      link.download = `smartdev-${project.project_id}.zip`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
  }

  return <details className="panel">
    <summary>Project host installations</summary>
    {error && <p role="alert">{error}</p>}
    {!projects.length && <p>No main projects are configured for host installation.</p>}
    {projects.map(project => {
      const selection = selected[project.id];
      return <fieldset key={project.id} disabled={busy}>
        <legend>{project.name}</legend>
        {project.destination_label && <p>{project.destination_label}</p>}
        {project.status && <p>Status {project.status}
          {project.operation_id && <> · Operation <code>{project.operation_id}</code></>}
          {project.result?.receipt.vultr_instance_id &&
            <> · Managed host <code>{project.result.receipt.vultr_instance_id}</code></>}
          {project.result?.receipt.resource_id &&
            <> · Resource <code>{project.result.receipt.resource_id}</code></>}
        </p>}
        {(!project.status || project.status === 'expired') && <>
          <label><input className="checkbox" type="checkbox" checked={!!selection} onChange={event => {
            clearReview();
            setSelected(event.target.checked ? {[project.id]: {}} : {});
          }} /> Select {project.name}</label>
          {selection && options.map(([option, label]) => <label key={option}>
            <input className="checkbox" type="checkbox" checked={selection[option] ?? false}
              onChange={event => {
                clearReview();
                const checked = event.target.checked;
                setSelected(current => ({...current, [project.id]: {...current[project.id], [option]: checked}}));
              }} /> {label}
          </label>)}
        </>}
      </fieldset>;
    })}
    {!!projects.length && <button type="button" disabled={busy || !payload.length} onClick={reviewPlans}>
      Review installation plans
    </button>}
    {Object.keys(review).length > 0 && <section aria-label="Installation preflight">
      {payload.map(project => {
        const plan = review[project.project_id];
        if (!plan) return null;
        const details = plan.destination;
        return <div key={project.project_id}>
          <h3>{projects.find(item => item.id === project.project_id)?.name}</h3>
          <dl>
            <div><dt>Destination</dt><dd>{plan.destination_label}</dd></div>
            {Object.entries(details).map(([label, value]) =>
              <div key={label}><dt>{label.replaceAll('_', ' ')}</dt><dd>{String(value)}</dd></div>
            )}
            {plan.default_host_image && <>
              <div><dt>Default image version</dt><dd>{plan.default_host_image.version}</dd></div>
              <div><dt>Default image digest</dt><dd><code>{plan.default_host_image.digest}</code></dd></div>
              <div><dt>Vultr snapshot</dt><dd>{plan.default_host_image.vultr_snapshot_id}</dd></div>
            </>}
            <div><dt>Portal</dt><dd>{plan.portal_origin}</dd></div>
            <div><dt>Grant expires</dt><dd>{new Date(plan.expires_at).toLocaleString()}</dd></div>
          </dl>
          {!plan.downloadable &&
            <p>Host provisioning is disabled. This review does not queue an installation.</p>}
          {plan.downloadable &&
            <button type="button" disabled={busy} onClick={() => download(project)}>Docker Download</button>}
        </div>;
      })}
    </section>}
  </details>;
}
