'use client';

import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from 'react';
import {
  Activity,
  ArrowDownUp,
  ArrowRight,
  ArrowUpRight,
  Box,
  Boxes,
  Check,
  CheckCheck,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  Copy,
  Ellipsis,
  ExternalLink,
  FileCheck2,
  Globe,
  Home,
  Layers,
  LayoutGrid,
  ListFilter,
  Menu,
  Plus,
  RefreshCw,
  Search,
  Server,
  Settings,
  ShieldCheck,
  Users,
  X,
} from 'lucide-react';
import s from './compute-console.module.css';
import DeploymentLogin from './deployment-login';

type Tenant = { id: string; name: string };
type App = {
  project_id: string;
  url: string;
  revision: string;
  runtime_status?: 'awaiting_tenant_configuration' | 'runtime_unverified';
};
type Links = { ipv4: string; portal_url: string; coder_url: string; ssh_user: string; apps: App[] };
type Project = { id: string; name: string; repository: string; revision: string; tenant_setup?: boolean };
type Job = {
  id: string;
  project_id: string;
  project_name: string;
  project_manifest: Project[] | null;
  status: string;
  instance_id: string | null;
  release_revision: string | null;
  queued_at: string;
  updated_at: string;
  reconciled_at: string | null;
  region: string | null;
  plan: string | null;
  dns_zone: string | null;
  deployment_hostname: string | null;
  note: string | null;
  installer_sha256: string | null;
  observed_revision: string | null;
  assignment_version: number;
  portal_account: string;
  tenant_id: string | null;
  tenant_name: string | null;
  enrolled_at: string | null;
  verified_at: string | null;
  connections: Links | null;
};
type Entry = {
  at: string;
  kind: string;
  label: string;
  previous_tenant?: string | null;
  next_tenant?: string | null;
};
type History = { events: Entry[]; proof: { public_key: string; signature: string } | null };
type Inventory = { deployments: Job[]; tenants: Tenant[] };
const endpoint = '/api/admin/compute/deployments';
const tabs = ['Overview', 'Applications', 'Access', 'Events', 'Receipts'] as const;
type Tab = (typeof tabs)[number];
const date = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'America/Toronto',
  month: 'short',
  day: 'numeric',
  year: 'numeric',
  hour: 'numeric',
  minute: '2-digit',
});
const states: Record<string, string> = {
  deployed: 'Deployed',
  needs_attention: 'Needs attention',
  failed: 'Failed',
  processing: 'In progress',
  queued: 'Queued',
};
const state = (j: Job) => (j.reconciled_at ? 'Reconciled' : states[j.status] || j.status);
const names = (j: Job) => j.project_manifest?.map((p) => p.name).join(', ') || j.project_name;
function tone(label: string) {
  if (['Deployed', 'Verified', 'Reachable at verification'].includes(label)) return 'green';
  if (['Needs attention', 'Failed', 'Awaiting tenant configuration'].includes(label)) return 'amber';
  return ['Reconciled', 'In progress'].includes(label) ? 'blue' : 'gray';
}
function Chip({ children }: { children: string }) {
  return (
    <span className={s.chip} data-tone={tone(children)}>
      <i />
      {children}
    </span>
  );
}
function readiness(j: Job) {
  const apps = j.connections?.apps || [];
  if (!apps.length) return 'Unverified';
  if (apps.some((a) => a.runtime_status === 'awaiting_tenant_configuration'))
    return 'Awaiting tenant configuration';
  if (apps.some((a) => a.runtime_status === 'runtime_unverified')) return 'Runtime unverified';
  return 'Reachable at verification';
}
function Modal({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null),
    heading = useId();
  useEffect(() => {
    const d = ref.current;
    d?.showModal();
    return () => {
      d?.close();
    };
  }, []);
  return (
    <dialog
      className={s.dialog}
      ref={ref}
      aria-labelledby={heading}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
    >
      <header>
        <h2 id={heading}>{title}</h2>
        <button aria-label="Close dialog" onClick={onClose}>
          <X size={18} />
        </button>
      </header>
      {children}
    </dialog>
  );
}
function CopyValue({
  value,
  short = false,
  label = 'value',
}: {
  value: string;
  short?: boolean;
  label?: string;
}) {
  const [feedback, setFeedback] = useState('');
  useEffect(() => {
    if (!feedback) return;
    const t = setTimeout(() => setFeedback(''), 1800);
    return () => clearTimeout(t);
  }, [feedback]);
  return (
    <span className={s.copy}>
      <code title={value}>{short ? value.slice(0, 12) : value}</code>
      <button
        type="button"
        aria-label={'Copy ' + label}
        title={'Copy ' + label}
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(value);
            setFeedback('Copied');
          } catch {
            setFeedback('Copy unavailable');
          }
        }}
      >
        {feedback === 'Copied' ? <Check size={13} /> : <Copy size={13} />}
      </button>
      {feedback && <small role="status">{feedback}</small>}
    </span>
  );
}
function Assign({
  job,
  tenants,
  onClose,
  onSaved,
}: {
  job: Job;
  tenants: Tenant[];
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [tenant, setTenant] = useState(job.tenant_id || ''),
    [saving, setSaving] = useState(false),
    [error, setError] = useState('');
  const newName = tenants.find((t) => t.id === tenant)?.name || 'Unassigned';
  async function save() {
    setSaving(true);
    setError('');
    try {
      const r = await fetch(endpoint + '/' + job.id + '/tenant', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tenant_id: tenant || null, version: job.assignment_version }),
      });
      if (!r.ok) throw Error((await r.json()).detail || 'Could not save the tenant assignment.');
      await onSaved();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save.');
    } finally {
      setSaving(false);
    }
  }
  return (
    <Modal title="Manage assigned tenant" onClose={onClose}>
      <div className={s.form}>
        <p>
          {names(job)} <span className={s.mono}>· {job.id.slice(0, 8)}</span>
        </p>
        <label>
          Assigned tenant
          <select value={tenant} onChange={(e) => setTenant(e.target.value)}>
            <option value="">Unassigned</option>
            {tenants.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        </label>
        {!tenants.length && (
          <p>
            Register a tenant in <a href="/?view=tenants">Tenant administration</a> to assign it here.
          </p>
        )}
        {tenant !== (job.tenant_id || '') && (
          <p>
            Confirm the change from <b>{job.tenant_name || 'Unassigned'}</b> to <b>{newName}</b>. Verified
            connection discovery follows this assignment.
          </p>
        )}
        {error && <p role="alert">{error}</p>}
        <footer>
          <button onClick={onClose}>Cancel</button>
          <button
            className={s.primary}
            disabled={saving || tenant === (job.tenant_id || '')}
            onClick={() => void save()}
          >
            {saving ? 'Saving…' : 'Confirm assignment'}
          </button>
        </footer>
      </div>
    </Modal>
  );
}
function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{children || 'Unavailable'}</dd>
    </div>
  );
}
function Connection({ title, url, status, action }: { title: string; url?: string; status: string; action?: ReactNode }) {
  const verified = url && /^https:\/\//i.test(url);
  return (
    <div className={s.connection}>
      <div className={s.connIcon}>
        <Globe size={17} />
      </div>
      <div>
        <strong>{title}</strong>
        <small>{status}</small>
        {verified && <CopyValue value={url} label={title + ' URL'} />}
      </div>
      {action !== undefined ? action : verified && (
        <a href={url} target="_blank" rel="noreferrer">
          Open {title}
          <ExternalLink size={13} />
        </a>
      )}
    </div>
  );
}
function Details({
  job,
  tab,
  setTab,
  history,
  onAssign,
}: {
  job: Job;
  tab: Tab;
  setTab: (t: Tab) => void;
  history: History | undefined | null;
  onAssign: () => void;
}) {
  const links = job.connections,
    apps = job.project_manifest || [];
  const name = (id: string) => apps.find((p) => p.id === id)?.name || 'Application';
  return (
    <section className={s.details} aria-label="Selected deployment details">
      <div className={s.detailHead}>
        <div className={s.serverTile}>
          <Server size={21} />
        </div>
        <div>
          <h2>{names(job)}</h2>
          <small>
            Deployment <CopyValue value={job.id} label="deployment ID" />
          </small>
        </div>
        <div className={s.detailState}>
          <span>Infrastructure</span>
          <Chip>{job.verified_at ? 'Verified' : state(job)}</Chip>
          <span>Application</span>
          <Chip>{readiness(job)}</Chip>
        </div>
      </div>
      <div className={s.tabs} role="tablist" aria-label="Deployment details">
        {tabs.map((t, i) => (
          <button
            key={t}
            id={'compute-tab-' + t}
            aria-selected={tab === t}
            aria-controls={'compute-panel-' + t}
            tabIndex={t === tab ? 0 : -1}
            role="tab"
            onClick={() => setTab(t)}
            onKeyDown={(e) => {
              let n = i;
              if (e.key === 'ArrowLeft') n = (i + 4) % 5;
              else if (e.key === 'ArrowRight') n = (i + 1) % 5;
              else if (e.key === 'Home') n = 0;
              else if (e.key === 'End') n = 4;
              else return;
              e.preventDefault();
              setTab(tabs[n]);
              document.getElementById('compute-tab-' + tabs[n])?.focus();
            }}
          >
            {t}
          </button>
        ))}
      </div>
      <div
        role="tabpanel"
        id={'compute-panel-' + tab}
        aria-labelledby={'compute-tab-' + tab}
        className={s.tabbody}
      >
        {tab === 'Overview' && (
          <>
            <h3>Instance overview</h3>
            <dl className={s.fields}>
              <Field label="VM hostname">
                {links?.portal_url ? (
                  <CopyValue value={new URL(links.portal_url).hostname} label="VM hostname" />
                ) : (
                  job.deployment_hostname
                )}
              </Field>
              <Field label="Public IP">
                {links?.ipv4 && <CopyValue value={links.ipv4} label="public IP" />}
              </Field>
              <Field label="Region / size">{[job.region, job.plan].filter(Boolean).join(' / ')}</Field>
              <Field label="SMD release">
                {job.release_revision && <CopyValue value={job.release_revision} label="SMD release" />}
              </Field>
              <Field label="Assigned tenant">
                <span>{job.tenant_name || 'Unassigned'}</span>{' '}
                <button className={s.linkButton} onClick={onAssign}>
                  {job.tenant_id ? 'Change tenant' : 'Assign tenant'}
                </button>
                {job.tenant_id && (
                  <small className={s.tenantId}>
                    <CopyValue value={job.tenant_id} label="tenant ID" short />
                  </small>
                )}
              </Field>
              <Field label="Last updated">{date.format(new Date(job.updated_at))} Toronto</Field>
              <Field label="Installation verified">
                {job.verified_at && date.format(new Date(job.verified_at)) + ' Toronto'}
              </Field>
            </dl>
            {job.note && (
              <p className={s.note}>
                <Activity size={15} />
                {job.note}
              </p>
            )}
          </>
        )}
        {tab === 'Applications' && (
          <>
            <h3>Installed applications</h3>
            {apps.length ? (
              apps.map((p) => {
                const app = links?.apps.find((a) => a.project_id === p.id);
                return (
                  <div className={s.appRow} key={p.id}>
                    <Box size={19} />
                    <div>
                      <strong>{p.name}</strong>
                      <small>{p.repository}</small>
                    </div>
                    <CopyValue value={p.revision} label="Git revision" short />
                    <Chip>
                      {!app
                        ? 'Unverified'
                        : app.runtime_status === 'awaiting_tenant_configuration'
                          ? 'Awaiting tenant configuration'
                          : app.runtime_status === 'runtime_unverified'
                            ? 'Runtime unverified'
                            : 'Reachable at verification'}
                    </Chip>
                    {app && (
                      <a href={app.url} target="_blank" rel="noreferrer">
                        Open app <ArrowUpRight size={14} />
                      </a>
                    )}
                  </div>
                );
              })
            ) : (
              <p>No application manifest was recorded for this deployment.</p>
            )}
          </>
        )}
        {tab === 'Access' && (
          <>
            <div className={s.sectionTitle}>
              <h3>Verified connections</h3>
              <small>Assigned tenant · {job.tenant_name || 'Unassigned'}</small>
            </div>
            <Connection
              title="SmartDev Portal"
              url={links?.portal_url}
              status={'Default account · ' + job.portal_account + ' · ' + (
                job.reconciled_at || job.status !== 'deployed' || !links ? 'No active portal' :
                'Command Center sign-in'
              )}
              action={links && !job.reconciled_at && job.status === 'deployed' ? <DeploymentLogin taskId={job.id} /> : null}
            />
            <Connection
              title="Coder"
              url={links?.coder_url}
              status={links ? 'Verified endpoint' : 'No verified endpoint'}
            />
            {links?.apps.length ? (
              links.apps.map((a) => (
                <Connection
                  key={a.project_id}
                  title={name(a.project_id) + ' application'}
                  url={a.url}
                  status={
                    a.runtime_status === 'awaiting_tenant_configuration'
                      ? 'Installed · tenant configuration required'
                      : a.runtime_status === 'runtime_unverified'
                        ? 'Installed · runtime unverified'
                        : 'Verified application endpoint'
                  }
                />
              ))
            ) : (
              <Connection title="Deployed application" status="No verified endpoint" />
            )}
            {links && (
              <div className={s.ssh}>
                SSH <CopyValue value={'ssh ' + links.ssh_user + '@' + links.ipv4} label="SSH command" />
              </div>
            )}
          </>
        )}
        {tab === 'Events' && (
          <>
            <h3>Deployment events</h3>
            {history === null ? (
              <p role="alert">Could not load deployment events.</p>
            ) : !history ? (
              <p role="status">Loading events…</p>
            ) : (
              <ol className={s.timeline}>
                {history.events.map((e, i) => (
                  <li key={e.at + i}>
                    <time>{date.format(new Date(e.at))} Toronto</time>
                    <span>
                      {e.label}
                      {e.kind === 'assignment' &&
                        ' · ' + (e.previous_tenant || 'Unassigned') + ' → ' + (e.next_tenant || 'Unassigned')}
                    </span>
                  </li>
                ))}
              </ol>
            )}
          </>
        )}
        {tab === 'Receipts' && (
          <>
            <h3>Installation receipt</h3>
            <dl className={s.fields}>
              <Field label="Verification">
                <Chip>{job.verified_at ? 'Verified' : 'Unverified'}</Chip>
              </Field>
              <Field label="Observed release">{job.observed_revision}</Field>
              <Field label="Installer SHA-256">
                {job.installer_sha256 && (
                  <CopyValue value={job.installer_sha256} label="installer SHA-256" short />
                )}
              </Field>
              <Field label="Instance ID">
                {job.instance_id && <CopyValue value={job.instance_id} label="instance ID" short />}
              </Field>
              {history?.proof && (
                <>
                  <Field label="Ed25519 public key">
                    <CopyValue value={history.proof.public_key} label="receipt public key" short />
                  </Field>
                  <Field label="Ed25519 signature">
                    <CopyValue
                      value={history.proof.signature.replace(/\s/g, '')}
                      label="receipt signature"
                      short
                    />
                  </Field>
                </>
              )}
            </dl>
            {history === null && <p role="alert">Could not load signed proof.</p>}
          </>
        )}
      </div>
    </section>
  );
}

const mainLinks = [
  { label: 'Home', href: '/?view=workspaces', icon: Home },
  { label: 'Compute', href: '/?view=deployments', icon: Server },
  { label: 'Applications', tab: 'Applications' as Tab, icon: Boxes },
  { label: 'Tenants', href: '/?view=tenants', icon: Users },
  { label: 'Operations', href: '/?view=taskboard', icon: Activity },
  { label: 'Receipts', tab: 'Receipts' as Tab, icon: FileCheck2 },
  { label: 'Settings', href: '/?view=settings', icon: Settings },
];
export default function ComputeConsole({
  deploymentForm,
  signOut,
  onAsk,
}: {
  deploymentForm: ReactNode;
  signOut?: ReactNode;
  onAsk?: () => void;
}) {
  const [inventory, setInventory] = useState<Inventory>(),
    [error, setError] = useState(''),
    [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState('All statuses'),
    [environment, setEnvironment] = useState('All environments'),
    [search, setSearch] = useState('');
  const [global, setGlobal] = useState(''),
    [globalOpen, setGlobalOpen] = useState(false),
    globalInput = useRef<HTMLInputElement>(null);
  const [selected, setSelected] = useState<string>(),
    [tab, setTab] = useState<Tab>('Overview'),
    [reverse, setReverse] = useState(false);
  const [screen, setScreen] = useState<'Deployments' | 'Instances' | 'Images' | 'Environments'>(() =>
    typeof window !== 'undefined' &&
    new URLSearchParams(window.location.search).get('view') === 'environments'
      ? 'Environments'
      : 'Deployments',
  );
  const [menu, setMenu] = useState(''),
    [mobileNav, setMobileNav] = useState(false),
    [newJob, setNewJob] = useState(false),
    [assignment, setAssignment] = useState<Job>();
  const [rowActions, setRowActions] = useState<Job>();
  const [history, setHistory] = useState<History | null>(),
    [revision, setRevision] = useState(0);
  const request = useRef(0),
    mounted = useRef(true),
    detail = useRef<HTMLDivElement>(null);
  const refresh = useCallback(async () => {
    const id = ++request.current;
    setLoading(true);
    try {
      const response = await fetch(endpoint, { cache: 'no-store', signal: AbortSignal.timeout(10000) });
      if (!response.ok) throw Error();
      const data: Inventory = await response.json();
      if (mounted.current && id === request.current) {
        setInventory(data);
        setError('');
      }
    } catch {
      if (mounted.current && id === request.current) setError('Could not refresh deployment inventory.');
    } finally {
      if (mounted.current && id === request.current) setLoading(false);
    }
  }, []);
  useEffect(() => {
    mounted.current = true;
    void refresh();
    const timer = setInterval(() => {
      if (document.visibilityState === 'visible') void refresh();
    }, 12000);
    return () => {
      mounted.current = false;
      clearInterval(timer);
    };
  }, [refresh]);
  useEffect(() => {
    function keys(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        setGlobalOpen(false);
        setMenu('');
        setMobileNav(false);
      }
      if (
        e.key === '/' &&
        !(
          e.target instanceof HTMLInputElement ||
          e.target instanceof HTMLTextAreaElement ||
          e.target instanceof HTMLSelectElement ||
          document.querySelector('dialog[open]')
        )
      ) {
        e.preventDefault();
        globalInput.current?.focus();
      }
    }
    function outside(e: PointerEvent) {
      if (e.target instanceof Element) {
        if (!e.target.closest('[data-compute-menu]')) setMenu('');
        if (!e.target.closest('[data-console-search]')) setGlobalOpen(false);
      }
    }
    document.addEventListener('keydown', keys);
    document.addEventListener('pointerdown', outside);
    return () => {
      document.removeEventListener('keydown', keys);
      document.removeEventListener('pointerdown', outside);
    };
  }, []);
  const jobs = inventory?.deployments || [],
    tenants = inventory?.tenants || [];
  const searchText = (j: Job) =>
    [
      names(j),
      j.id,
      j.tenant_name || 'Unassigned',
      state(j),
      j.region,
      j.dns_zone,
      j.connections?.ipv4,
      j.deployment_hostname,
      j.release_revision,
      ...(j.project_manifest || []).map((p) => p.repository),
    ]
      .join(' ')
      .toLowerCase();
  const rows = jobs
    .filter(
      (j) =>
        (filter === 'All statuses' || state(j) === filter) &&
        (environment === 'All environments' || j.dns_zone === environment) &&
        searchText(j).includes(search.toLowerCase().trim()) &&
        (screen !== 'Instances' || j.instance_id) &&
        (screen !== 'Images' || j.release_revision),
    )
    .sort(
      (a, b) => (reverse ? 1 : -1) * (a.updated_at.localeCompare(b.updated_at) || a.id.localeCompare(b.id)),
    );
  const active = rows.find((j) => j.id === selected) || rows[0];
  const activeId = active?.id,
    updated = active?.updated_at,
    assignmentVersion = active?.assignment_version;
  useEffect(() => {
    if (!activeId) return;
    const controller = new AbortController();
    setHistory(undefined);
    fetch(endpoint + '/' + activeId + '/history', { cache: 'no-store', signal: controller.signal })
      .then((r) => {
        if (!r.ok) throw Error();
        return r.json();
      })
      .then((h) => {
        if (!controller.signal.aborted) setHistory(h);
      })
      .catch(() => {
        if (!controller.signal.aborted) setHistory(null);
      });
    return () => controller.abort();
  }, [activeId, updated, assignmentVersion, revision]);
  function select(job: Job, next: Tab = 'Overview', scroll = false) {
    setSelected(job.id);
    setTab(next);
    setMenu('');
    setRowActions(undefined);
    setMobileNav(false);
    if (scroll) requestAnimationFrame(() => detail.current?.scrollIntoView({ block: 'nearest' }));
  }
  function navigate(next: typeof screen) {
    setScreen(next);
    setSearch('');
    setFilter('All statuses');
    setEnvironment('All environments');
    setMenu('');
    setMobileNav(false);
    setTab(next === 'Images' ? 'Receipts' : 'Overview');
  }
  function openTab(next: Tab) {
    setSearch('');
    setFilter('All statuses');
    setEnvironment('All environments');
    setScreen('Deployments');
    const j = active || jobs[0];
    if (j) select(j, next, true);
    setMobileNav(false);
  }
  function actionItems(job: Job) {
    return (
      <>
        <button onClick={() => select(job, 'Access', true)}>
          <Globe size={15} /> View access
        </button>
        <button onClick={() => select(job, 'Receipts', true)}>
          <FileCheck2 size={15} /> View receipt
        </button>
        <button
          onClick={() => {
            setMenu('');
            setRowActions(undefined);
            setAssignment(job);
          }}
        >
          <Users size={15} /> Manage assigned tenant
        </button>
      </>
    );
  }
  const stats = [
    {
      label: 'Deployed',
      count: jobs.filter((j) => state(j) === 'Deployed').length,
      icon: Server,
      tone: 'green',
    },
    {
      label: 'Reconciled',
      count: jobs.filter((j) => state(j) === 'Reconciled').length,
      icon: CheckCheck,
      tone: 'blue',
    },
    {
      label: 'Needs attention',
      count: jobs.filter((j) => ['Needs attention', 'Failed'].includes(state(j))).length,
      icon: CircleAlert,
      tone: 'amber',
    },
    {
      label: 'Tenants assigned',
      count: new Set(jobs.flatMap((j) => (j.tenant_id ? [j.tenant_id] : []))).size,
      icon: Users,
      tone: 'violet',
    },
  ];
  const query = global.trim().toLowerCase();
  const foundJobs = jobs.filter((j) => searchText(j).includes(query)).slice(0, 5);
  const destinations = [
    { label: 'Deployments', href: '/?view=deployments' },
    { label: 'Environments', action: () => navigate('Environments') },
    { label: 'Applications', action: () => openTab('Applications') },
    { label: 'Tenants', href: '/?view=tenants' },
    { label: 'Taskboard', href: '/?view=taskboard' },
    { label: 'Coder workspaces', href: '/?view=workspaces' },
    { label: 'Settings', href: '/?view=settings' },
  ].filter((i) => i.label.toLowerCase().includes(query));
  return (
    <div className={s.shell}>
      <header className={s.top}>
        <button
          className={s.hamburger}
          aria-label="Toggle navigation"
          aria-expanded={mobileNav}
          onClick={() => setMobileNav(!mobileNav)}
        >
          <Menu size={19} />
        </button>
        <a className={s.brand} href="/?view=deployments">
          <span>
            <Layers size={19} strokeWidth={2.25} />
          </span>
          SmartDev
        </a>
        <i className={s.divider} />
        <span className={s.brandSub}>Command Center</span>
        <nav aria-label="Command Center">
          <button
            className={screen === 'Environments' ? '' : s.activeTop}
            onClick={() => navigate('Deployments')}
          >
            Deployments
          </button>
          <button
            className={screen === 'Environments' ? s.activeTop : ''}
            onClick={() => navigate('Environments')}
          >
            Environments
          </button>
          <a href="/?view=tenants">Tenants</a>
          <a href="/?view=taskboard">Operations</a>
          <div data-compute-menu>
            <button aria-expanded={menu === 'admin'} onClick={() => setMenu(menu === 'admin' ? '' : 'admin')}>
              Admin <ChevronDown size={12} />
            </button>
            {menu === 'admin' && (
              <div className={s.dropdown}>
                <a href="/?view=tenants">Tenant administration</a>
                <a href="/?view=settings">Settings</a>
                {onAsk && (
                  <button
                    onClick={() => {
                      setMenu('');
                      onAsk();
                    }}
                  >
                    Ask CC
                  </button>
                )}
                {signOut}
              </div>
            )}
          </div>
        </nav>
        <div className={s.global} data-console-search>
          <Search size={15} />
          <input
            ref={globalInput}
            aria-label="Search console"
            placeholder="Search console"
            value={global}
            onFocus={() => setGlobalOpen(true)}
            onChange={(e) => {
              setGlobal(e.target.value);
              setGlobalOpen(true);
            }}
          />
          <kbd>/</kbd>
          {globalOpen && query && (
            <div className={s.searchResults}>
              {destinations.map((d) =>
                d.href ? (
                  <a key={d.label} href={d.href}>
                    {d.label}
                    <ArrowUpRight size={13} />
                  </a>
                ) : (
                  <button
                    key={d.label}
                    onClick={() => {
                      d.action?.();
                      setGlobalOpen(false);
                      setGlobal('');
                    }}
                  >
                    {d.label}
                    <ArrowRight size={13} />
                  </button>
                ),
              )}
              {foundJobs.map((j) => (
                <button
                  key={j.id}
                  onClick={() => {
                    navigate('Deployments');
                    select(j);
                    setGlobalOpen(false);
                    setGlobal('');
                  }}
                >
                  <span>
                    {names(j)} <small>{j.id.slice(0, 8)}</small>
                  </span>
                  <Chip>{state(j)}</Chip>
                </button>
              ))}
              {!foundJobs.length && !destinations.length && <p>No matches</p>}
            </div>
          )}
        </div>
      </header>
      <div className={s.frame}>
        {mobileNav && (
          <button className={s.scrim} aria-label="Close navigation" onClick={() => setMobileNav(false)} />
        )}
        <aside className={s.rail} data-open={mobileNav} aria-label="Console navigation">
          <div className={s.railMain}>
            {mainLinks.map((item) => (
              <div key={item.label}>
                {item.label === 'Compute' ? (
                  <>
                    <button className={s.computeParent} onClick={() => navigate('Deployments')}>
                      <Server size={17} />
                      Compute
                      <ChevronDown size={13} />
                    </button>
                    <div className={s.subnav}>
                      {(['Deployments', 'Instances', 'Images', 'Templates'] as const).map((n) =>
                        n === 'Templates' ? (
                          <a key={n} href="/?view=templates">
                            Templates
                          </a>
                        ) : (
                          <button
                            key={n}
                            className={screen === n ? s.railActive : ''}
                            onClick={() => navigate(n)}
                          >
                            {n}
                          </button>
                        ),
                      )}
                    </div>
                  </>
                ) : item.href ? (
                  <a href={item.href}>
                    <item.icon size={17} />
                    {item.label}
                  </a>
                ) : (
                  <button onClick={() => openTab(item.tab!)}>
                    <item.icon size={17} />
                    {item.label}
                  </button>
                )}
              </div>
            ))}
            <div className={s.railRule} />
            <a href="/?view=workspaces">
              <LayoutGrid size={16} />
              Coder workspaces
            </a>
          </div>
          <div className={s.mobileAccount}>
            {onAsk && (
              <button
                onClick={() => {
                  setMobileNav(false);
                  onAsk();
                }}
              >
                Ask CC
              </button>
            )}
            {signOut}
          </div>
          <div className={s.account}>
            <span>O</span>
            <div>
              <strong>Owner</strong>
              <small>Command Center</small>
            </div>
            <ShieldCheck size={16} />
          </div>
        </aside>
        <main className={s.main}>
          <div className={s.crumb}>
            <Server size={14} />
            <span>Compute</span>
            <ChevronRight size={13} />
            <b>{screen}</b>
          </div>
          <div className={s.titleLine}>
            <h1>{screen}</h1>
            <div className={s.tools}>
              <button
                onClick={() => {
                  void refresh();
                  setRevision((v) => v + 1);
                }}
                disabled={loading}
              >
                <RefreshCw size={15} className={loading ? s.spin : ''} />
                Refresh
              </button>
              <div data-compute-menu className={s.menuWrap}>
                <button
                  disabled={!active}
                  aria-expanded={menu === 'top'}
                  onClick={() => setMenu(menu === 'top' ? '' : 'top')}
                >
                  Actions <ChevronDown size={14} />
                </button>
                {menu === 'top' && active && (
                  <div className={s.dropdown} aria-label="Deployment actions">
                    {actionItems(active)}
                  </div>
                )}
              </div>
              <button className={s.primary} onClick={() => setNewJob(true)}>
                <Plus size={16} />
                New deployment
              </button>
            </div>
          </div>
          <div className={s.stats}>
            {stats.map((k) => (
              <div className={s.stat} key={k.label}>
                <div>
                  <span>{k.label}</span>
                  <strong>{inventory ? k.count : '—'}</strong>
                </div>
                <span className={s.statIcon} data-tone={k.tone}>
                  <k.icon size={18} />
                </span>
              </div>
            ))}
          </div>
          {error && (
            <p role="alert" className={s.alert}>
              {error} <button onClick={() => void refresh()}>Retry</button>
            </p>
          )}
          <section className={s.inventory} aria-label="Deployment inventory">
            <div className={s.panelTitle}>
              <h2>
                {screen === 'Deployments'
                  ? 'Deployment inventory'
                  : screen === 'Images'
                    ? 'Releases by deployment'
                    : screen === 'Instances'
                      ? 'Provisioned instances'
                      : 'Deployment environments'}
              </h2>
              <span>{rows.length}</span>
            </div>
            <div className={s.filters}>
              <label className={s.search}>
                <Search size={15} />
                <input
                  type="search"
                  aria-label="Search deployments"
                  placeholder="Search deployments…"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
              </label>
              <label className={s.filter}>
                <ListFilter size={15} />
                <select
                  aria-label="Filter by status"
                  value={filter}
                  onChange={(e) => setFilter(e.target.value)}
                >
                  {['All statuses', ...Object.values(states), 'Reconciled'].map((x) => (
                    <option key={x}>{x}</option>
                  ))}
                </select>
              </label>
              <label className={s.filter}>
                <Globe size={14} />
                <select
                  aria-label="Filter by environment"
                  value={environment}
                  onChange={(e) => setEnvironment(e.target.value)}
                >
                  <option>All environments</option>
                  {[...new Set(jobs.map((j) => j.dns_zone).filter((v): v is string => !!v))]
                    .sort()
                    .map((v) => (
                      <option key={v}>{v}</option>
                    ))}
                </select>
              </label>
            </div>
            <div className={s.tableWrap}>
              <table>
                <thead>
                  <tr>
                    <th aria-label="Select deployment" />
                    <th>Project</th>
                    <th>Assigned tenant</th>
                    <th>Status</th>
                    <th>VM / Public IP</th>
                    <th>SMD release</th>
                    <th aria-sort={reverse ? 'ascending' : 'descending'}>
                      <button className={s.sort} onClick={() => setReverse(!reverse)}>
                        Last updated <ArrowDownUp size={13} />
                      </button>
                    </th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((j) => (
                    <tr key={j.id} data-active={active?.id === j.id} onClick={() => select(j)}>
                      <td>
                        <input
                          type="radio"
                          aria-label={'Select ' + names(j) + ' ' + j.id.slice(0, 8)}
                          name="compute-deployment"
                          checked={active?.id === j.id}
                          onChange={() => select(j)}
                        />
                      </td>
                      <td>
                        <button className={s.rowProject} onClick={() => select(j)}>
                          {names(j)}
                        </button>
                        <small title={j.id}>
                          {j.id.slice(0, 8)}
                          {j.region ? ' · ' + j.region.toUpperCase() : ''}
                        </small>
                      </td>
                      <td className={!j.tenant_name ? s.muted : ''}>{j.tenant_name || 'Unassigned'}</td>
                      <td>
                        <Chip>{state(j)}</Chip>
                        {j.status === 'deployed' && j.verified_at && !j.reconciled_at && (
                          <small className={s.installProgress}>
                            <progress max={100} value={100} aria-label="Installation completion" />
                            100% installed
                          </small>
                        )}
                        {j.status === 'processing' && !j.reconciled_at && (
                          <small>
                            <progress aria-label="Installation in progress" />
                          </small>
                        )}
                      </td>
                      <td>
                        {j.connections?.ipv4 ? (
                          <code>{j.connections.ipv4}</code>
                        ) : j.instance_id ? (
                          <code title={j.instance_id}>{j.instance_id.slice(0, 8)}</code>
                        ) : (
                          <span className={s.muted}>—</span>
                        )}
                      </td>
                      <td>
                        {j.release_revision ? (
                          <code className={s.truncate} title={j.release_revision}>
                            {j.release_revision}
                          </code>
                        ) : (
                          <span className={s.muted}>—</span>
                        )}
                      </td>
                      <td>
                        <time
                          dateTime={j.updated_at}
                          title={date.format(new Date(j.updated_at)) + ' Toronto'}
                        >
                          {date.format(new Date(j.updated_at)).replace(/,? 20\d\d,?/, ',')}
                        </time>
                      </td>
                      <td>
                        <button
                          className={s.rowAction}
                          aria-label={'Actions for ' + j.id}
                          title="Deployment actions"
                          onClick={(e) => {
                            e.stopPropagation();
                            setSelected(j.id);
                            setRowActions(j);
                          }}
                        >
                          <Ellipsis size={17} />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!rows.length && (
              <p className={s.empty}>
                {inventory
                  ? 'No deployments match this view.'
                  : error
                    ? 'Inventory unavailable.'
                    : 'Loading deployments…'}
              </p>
            )}
            <div className={s.tableFoot}>
              <span>
                {rows.length} of {jobs.length} deployments
              </span>
              <span>Toronto time</span>
            </div>
          </section>
          {active && (
            <div ref={detail}>
              <Details
                job={active}
                tab={tab}
                setTab={setTab}
                history={history}
                onAssign={() => setAssignment(active)}
              />
            </div>
          )}
        </main>
      </div>
      {rowActions && (
        <Modal title={names(rowActions) + ' deployment actions'} onClose={() => setRowActions(undefined)}>
          <div className={s.actionList}>{actionItems(rowActions)}</div>
        </Modal>
      )}
      {newJob && (
        <Modal title="New deployment" onClose={() => setNewJob(false)}>
          {deploymentForm}
        </Modal>
      )}
      {assignment && (
        <Assign
          job={assignment}
          tenants={tenants}
          onClose={() => setAssignment(undefined)}
          onSaved={async () => {
            await refresh();
            setRevision((v) => v + 1);
          }}
        />
      )}
    </div>
  );
}
