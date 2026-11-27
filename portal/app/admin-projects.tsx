'use client';

import {useEffect, useState, type ReactNode, type FormEvent} from 'react';

type Project = {
  id: string;
  name: string;
  repository: string;
  workspace_url: string | null;
  archived: boolean;
  status: string;
};

import ComputeConsole from './compute-console';
import {Download, Rocket, Server} from 'lucide-react';
import styles from './deployment-console.module.css';

const endpoint = '/api/admin/project-tenants';
const deploymentsEndpoint = '/api/admin/smd-deploy-tasks';

type Offer = {
  offer_token: string;
  release_revision: string;
  dns_zone: string;
  monthly_cost_usd: number;
  target_tenant: string | null;
  projects: {id: string; name: string; repository: string; revision: string}[];
};

export default function AdminProjects<T extends Project>({projects, activeTab, signOut, onAsk}: {
  projects: T[];
  activeTab?: 'deployments' | 'tenants';
  signOut?: ReactNode; onAsk?:()=>void;
}) {
  const [localTab, setTab] = useState<'deployments' | 'tenants'>('deployments');
  const tab = activeTab ?? localTab;
  const [assignments, setAssignments] = useState<Record<string, string>>();
  const [tenant, setTenant] = useState('');
  const [targetOverride, setTargetOverride] = useState<string | null>(null);
  const [deploymentProjectIds, setDeploymentProjectIds] = useState<string[]>([]);
  const [revisions, setRevisions] = useState<Record<string, string>>({});
  const [review, setReview] = useState<{offer: Offer; selection: string}>();
  const [deploying, setDeploying] = useState(false);
  const [deploymentMessage, setDeploymentMessage] = useState('');
  const [projectId, setProjectId] = useState('');
  const [name, setName] = useState('');
  const [message, setMessage] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const response = await fetch(endpoint, {cache: 'no-store', signal: controller.signal});
        if (!response.ok) throw Error();
        setAssignments(await response.json());
      } catch {
        if (!controller.signal.aborted) setMessage('Tenant assignments are unavailable.');
      }
    }
    void load();
    return () => controller.abort();
  }, []);

  const ordered = [...projects].sort((a, b) => a.name.localeCompare(b.name));
  const tenants = [...new Set(Object.values(assignments ?? {}))].sort((a, b) => a.localeCompare(b));
  const visible = ordered.filter(project => !tenant || assignments?.[project.id] === tenant);

  const deployable = ordered.filter(project => !project.archived);
  const selected = deployable.filter(project => deploymentProjectIds.includes(project.id));

  const selectedTenants = new Set(selected.map(project => assignments?.[project.id] ?? ''));
  const currentTarget = selectedTenants.size === 1 ? [...selectedTenants][0] : '';
  const target = targetOverride ?? currentTarget;
  const targetBlocker = !assignments ? 'Tenant assignments are unavailable.'
    : selectedTenants.size > 1 ? 'Selected projects must share a current tenant.'
    : target !== currentTarget ? 'Select the projects’ current tenant.' : '';
  const draft = {
    projects: selected.map(project => ({
      id: project.id,
      revision: revisions[project.id]?.trim().toLowerCase() || null,
    })),
    target_tenant: target || null,
  };
  const selection = JSON.stringify(draft);
  const offer = review?.selection === selection ? review.offer : undefined;
  const invalidRevision = draft.projects.some(project => project.revision && !/^[0-9a-f]{40}$/.test(project.revision));

  async function submitDeployment() {
    if (!selected.length || targetBlocker || invalidRevision || deploying) return;
    setDeploying(true);
    setDeploymentMessage('');
    try {
      const body = offer ? {
        offer_token: offer.offer_token,
        projects: offer.projects.map(({id, revision}) => ({id, revision})),
        target_tenant: offer.target_tenant,
      } : draft;
      const response = await fetch(deploymentsEndpoint + (offer ? '' : '/offer'), {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body),
      });
      const result = await response.json().catch(() => null);
      if (!response.ok || !result) {
        throw Error(typeof result?.detail === 'string' ? result.detail : 'Could not submit the deployment request.');
      }
      if (offer) {
        setReview(undefined);
        setDeploymentMessage('Deployment queued. Follow its progress under Deployment activity.');
      } else {
        setReview({offer: result, selection});
      }
    } catch (failure) {
      if (offer) setReview(undefined);
      setDeploymentMessage(failure instanceof Error ? failure.message : 'Could not submit the deployment request.');
    } finally {
      setDeploying(false);
    }
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!projectId || saving) return;
    setSaving(true);
    setMessage('');
    try {
      const response = await fetch(endpoint + '/' + projectId, {
        method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({tenant_name: name}),
      });
      if (!response.ok) throw Error();
      const {tenant_name} = await response.json();
      setAssignments(current => {
        const next = {...current};
        if (tenant_name) next[projectId] = tenant_name;
        else delete next[projectId];
        return next;
      });
      if (tenant && tenant !== tenant_name) setTenant('');
      setMessage('Tenant assignment saved.');
    } catch {
      setMessage('Could not save the tenant assignment.');
    } finally {
      setSaving(false);
    }
  }

  const deploymentForm=(
    <section id="deployment-console" className={styles.console} aria-labelledby="deployment-console-title">
      <header className={styles.header}>
        <div className={styles.title}><Server size={19} aria-hidden /><h2 id="deployment-console-title">Deployment configuration</h2></div>
        <div className={styles.targetActions}>
          <label className={styles.picker}>Target tenant
            <select value={target} disabled={!assignments || !selected.length}
              onChange={event => setTargetOverride(event.target.value === currentTarget ? null : event.target.value)}>
              <option value="">{selectedTenants.size > 1 ? 'Multiple current tenants' : 'Current · Unassigned'}</option>
              {tenants.map(value => <option key={value} value={value}>{value === currentTarget ? value + ' · Current' : value}</option>)}
            </select>
          </label>
          <button type="button" disabled={!selected.length || !!targetBlocker || invalidRevision || deploying}
            onClick={() => void submitDeployment()}>
            <Rocket size={16} aria-hidden /> {deploying ? 'Submitting…' : offer ? 'Deploy SMD' : 'Review deployment'}
          </button>
        </div>
      </header>
      <div className={styles.tableScroll}>
        <table className={styles.projectTable}>
          <thead><tr>
            <th scope="col"><input type="checkbox" aria-label="Select all active projects"
              checked={deployable.length > 0 && selected.length === deployable.length} disabled={!deployable.length}
              onChange={event => {
                setTargetOverride(null);
                setDeploymentProjectIds(event.target.checked ? deployable.map(project => project.id) : []);
              }} /></th>
            <th scope="col">Project</th><th scope="col">Git commit</th><th scope="col">Tenant</th><th scope="col">Workspace</th>
          </tr></thead>
          <tbody>{visible.map(project => {
            const included = deploymentProjectIds.includes(project.id) && !project.archived;
            return <tr key={project.id}>
            <td><input type="checkbox" aria-label={'Include ' + project.name} disabled={project.archived}
              checked={included}
              onChange={event => {
                const checked = event.target.checked;
                setTargetOverride(null);
                setDeploymentProjectIds(ids => checked ? [...ids, project.id] : ids.filter(id => id !== project.id));
              }} /></td>
            <th scope="row"><span className={styles.projectName}>
              {project.name}{project.archived ? <small> · Archived</small> : <a
                aria-label={'Download SMD stack for ' + project.name} title="Download SMD stack (Docker Compose)"
                href={'/api/admin/projects/' + project.id + '/smd-export'} download>
                <Download size={16} aria-hidden />
              </a>}
            </span></th>
            <td>
              <input aria-label={'Git commit for ' + project.name} value={revisions[project.id] ?? ''}
                placeholder="Default branch HEAD" maxLength={40} disabled={!included}
                onChange={event => {
                  const revision = event.target.value;
                  setRevisions(current => ({...current, [project.id]: revision}));
                }} />
            </td>
            <td>{assignments ? assignments[project.id] || 'Unassigned' : '—'}</td>
            <td>{project.workspace_url ? <a href={project.workspace_url} target="_blank" rel="noreferrer">Open workspace</a> : '—'}</td>
          </tr>;})}</tbody>
        </table>
      </div>
      <section aria-label="Deployment review">
        {selected.length === 0 && <p>Select the projects to include in the SMD environment.</p>}
        {selected.length > 0 && targetBlocker && <p role="status">{targetBlocker}</p>}
        {invalidRevision && <p role="status">Enter a full 40-character Git commit or leave it blank to resolve the default branch.</p>}
        {offer && <>
          <p>One Vultr VM · Atlanta · 4 vCPUs · 8 GB RAM · Ubuntu 24.04 · USD {offer.monthly_cost_usd}/month</p>
          <p>SMD release <code>{offer.release_revision}</code> · DNS zone <code>{offer.dns_zone}</code></p>
          <ul>{offer.projects.map(project => <li key={project.id}>
            {project.name} · {project.repository} · <code>{project.revision}</code>
          </li>)}</ul>
        </>}
        {deploymentMessage && <p role="status">{deploymentMessage}</p>}
      </section>
    </section>
  );
  return <section className={tab==='deployments'?styles.computeHost:"wb-content panel"} aria-label="Command Center administration" style={{overflow: 'auto'}}>
    {!activeTab && <div className={styles.adminTabs} role="tablist" aria-label="Command Center administration"
      onKeyDown={event => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        const next = event.key === 'Home' ? 'deployments' : event.key === 'End' ? 'tenants'
          : tab === 'deployments' ? 'tenants' : 'deployments';
        setTab(next);
        event.currentTarget.querySelector<HTMLButtonElement>('#admin-tab-' + next)?.focus();
      }}>
      <button id="admin-tab-deployments" type="button" role="tab" aria-controls="admin-panel-deployments"
        aria-selected={tab === 'deployments'} tabIndex={tab === 'deployments' ? 0 : -1}
        onClick={() => setTab('deployments')}>Deployments</button>
      <button id="admin-tab-tenants" type="button" role="tab" aria-controls="admin-panel-tenants"
        aria-selected={tab === 'tenants'} tabIndex={tab === 'tenants' ? 0 : -1}
        onClick={() => setTab('tenants')}>Tenants</button>
    </div>}
    <div id="admin-panel-deployments" role="tabpanel" aria-label={activeTab ? "Deployments" : undefined} aria-labelledby={activeTab ? undefined : "admin-tab-deployments"} hidden={tab !== 'deployments'}>
    {tab==='deployments' && <ComputeConsole deploymentForm={deploymentForm} signOut={signOut} onAsk={onAsk}/>}
    </div>
    <div id="admin-panel-tenants" role="tabpanel" aria-label={activeTab ? "Tenants" : undefined} aria-labelledby={activeTab ? undefined : "admin-tab-tenants"} hidden={tab !== 'tenants'}>
    <h2>Tenant administration</h2>
    <label>Tenant
      <select value={tenant} onChange={event => setTenant(event.target.value)}>
        <option value="">All tenants</option>
        {tenants.map(value => <option key={value}>{value}</option>)}
      </select>
    </label>
    <form onSubmit={save}>
      <fieldset disabled={!assignments || saving}>
        <legend>Assign a tenant</legend>
        <label>Project
          <select value={projectId} onChange={event => {
            setProjectId(event.target.value);
            setName(assignments?.[event.target.value] ?? '');
          }}>
            <option value="">Select project</option>
            {ordered.map(project => <option key={project.id} value={project.id}>{project.name}</option>)}
          </select>
        </label>
        <label>Tenant name <input list="cc-tenant-names" value={name} maxLength={80} onChange={event => setName(event.target.value)} /></label>
        <datalist id="cc-tenant-names">{tenants.map(value => <option key={value} value={value} />)}</datalist>
        <button disabled={!projectId}>Save assignment</button>
        <button type="button" disabled={!projectId} onClick={() => setName('')}>Clear tenant name</button>
      </fieldset>
    </form>
    {message && <p role="status">{message}</p>}
    </div>
  </section>;
}
