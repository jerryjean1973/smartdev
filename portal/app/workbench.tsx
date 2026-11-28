'use client';

import { useCallback, useEffect, useRef, useState, type ReactNode, type PointerEvent } from 'react';
import { Ellipsis, ExternalLink, GitBranch, Globe, List, RotateCw, Maximize2, Minimize2, PanelLeftClose, PanelLeftOpen, PanelRightClose, PanelRightOpen, Plus, Settings } from 'lucide-react';
import Memory from './memory';
import Objectives from './objectives';
import Taskboard from './taskboard';
import CcConversation from './cc-conversation';
import Runtime from './runtime';
import DevPublishing from './dev-publishing';
import SmdDeployment from './smd-deployment';
import AdminProjects from './admin-projects';
import { DockerDownload, useDockerExport } from './docker-download';
import ProjectDomains from './project-domains';
import Integrations from './integrations';
import ApplicationPreviewSettings, {defaultPreview,environments,type Environment,type PreviewSettings} from './preview-settings';
import Secrets from './secrets';

type Project = { id: string; name: string; repository: string; status: string; workspace_url: string | null; error: string | null; archived: boolean };
type Run = { agent: string; branch: string; worktree: string };
type Objective = { id: string; display_name?: string; expected_outcome: string; original_request: string; status: string; progress: {
  executionSteps?: Array<Run & {id:string; criterion_id:string; started_at:string; finished_at:string|null; exit_code:number|null}>;
  active: Run[]; completedCriteria: string[]; outstandingCriteria: string[]; nextAction: string | null;
  blockedCriteria: string[]; attempts: {branch: string; worktree: string; exit_code: number | null; started_at: string; finished_at: string | null}[];
} };
type Preferences = { left: number; command: number; right: number; showLeft: boolean; showCommand: boolean; showRight: boolean; collapsed?: string[]; expandedProjects?: string[]; hidePreview?: boolean; view: 'Preview' | 'Git' | 'Logs' | 'Project settings' | 'Project context' | 'Publishing' | 'Domains'; dark: boolean; project: string };
const initial = {left: 188, command: 372, right: 420, showLeft: true, showCommand: true, showRight: false, view: 'Preview' as const, dark: true, project: ''};
const workspaceTabs = [
  ['Preview', Globe], ['Git', GitBranch], ['Logs', List],
] as const;
type SidebarTask = {id:string; project_id:string; title:string|null; status:string};
type ChatNavigationCommand = {project:string;type:'focus-task'|'new-task';id?:string};
type ResizablePanel = 'left' | 'command' | 'right';
type GitReview = {objective: string; tree: string; branch: string; head: string; base: string; diff: string; truncated: boolean};

export default function Workbench({projects, createForm, signOut, updateProject, managementOnly = false, codingOnly = false}: {
  managementOnly?: boolean; codingOnly?: boolean; projects: Project[]; createForm: ReactNode; signOut: ReactNode;
  updateProject: (project: Project, verb: 'retry' | 'archive') => void;
}) {
  const dockerExport = useDockerExport();
  const [prefs, setPrefs] = useState<Preferences>(initial);
  const [restored, setRestored] = useState(false);
  const [coderConnection, setCoderConnection] = useState<'connecting' | 'connected' | 'failed'>('connecting');
  const [highlightedTask, setHighlightedTask] = useState('');
  const [expandedView, setExpandedView] = useState<'preview' | 'files' | null>(null);
  const [gitReview, setGitReview] = useState<{project: string; review: GitReview}>();
  const [menu, setMenu] = useState('');
  const [creating, setCreating] = useState(false);
  const [showTaskDetails, setShowTaskDetails] = useState(false);
  const [opened, setOpened] = useState<string[]>([]);
  const [chatOpened, setChatOpened] = useState<string[]>([]);
  const [taskNavigation,setTaskNavigation]=useState<{
    project:string; focused:string|null; running:string[]; waiting:string[]; queued:string[]; paused:boolean;
  }>();
  const [sidebarTasks,setSidebarTasks] = useState<SidebarTask[]>([]);
  const [mobileLayout, setMobileLayout] = useState(false);
  const [mobileProjects, setMobileProjects] = useState(false);
  useEffect(() => {
    const query = window.matchMedia('(max-width: 1000px)');
    const update = () => setMobileLayout(query.matches);
    update();
    query.addEventListener('change', update);
    return () => query.removeEventListener('change', update);
  }, []);
  const pendingNavigation = useRef<ChatNavigationCommand | undefined>(undefined);
  const [objectives, setObjectives] = useState<Objective[]>([]);
  const [fileView, setFileView] = useState<'files'|'history'>('files');
  const [parents, setParents] = useState<Record<string,string>>({});
  const [showIntegrations, setShowIntegrations] = useState(false);
  const [showFleet, setShowFleet] = useState(managementOnly);
  const fleetPages = {
    workspaces: {label: 'Workspaces', path: '/workspaces'},
    templates: {label: 'Templates', path: '/templates'},
    agents: {label: 'Agents', path: '/agents'},
    settings: {label: 'Settings', path: '/settings'},
    deployment: {label: 'Stats', path: '/deployment'},
    ai: {label: 'AI', path: '/ai/settings'},
    health: {label: 'Healthcheck', path: '/health'},
  } as const;
  const [fleetView, setFleetView] = useState<keyof typeof fleetPages>(() => {
    const view = managementOnly && typeof window !== 'undefined' ? new URLSearchParams(window.location.search).get('view') : null;
    return view && Object.hasOwn(fleetPages, view) ? view as keyof typeof fleetPages : 'workspaces';
  });
  const [fleetPathOverride, setFleetPathOverride] = useState(() => {
    const path = managementOnly && typeof window !== 'undefined'
      ? new URLSearchParams(window.location.search).get('native') : null;
    if (!path || !new RegExp('^/' + fleetView + '(?:/[A-Za-z0-9_~-][A-Za-z0-9._~-]*)*/?$').test(path)) return null;
    return path;
  });
  const fleetObserver = useRef<MutationObserver | null>(null);
  useEffect(() => () => fleetObserver.current?.disconnect(), []);
  const [fleetReady, setFleetReady] = useState(false);
  const [adminTab, setAdminTab] = useState<'deployments' | 'tenants'>('deployments');
  const [fleetOpening, setFleetOpening] = useState(false);
  const [fleetError, setFleetError] = useState('');
  const [showAdmin, setShowAdmin] = useState(!codingOnly && !managementOnly);
  const [showTaskboard, setShowTaskboard] = useState(false);
  const [ccConversation, setCcConversation] = useState<{taskId?: string; title: string}>();
  useEffect(() => {
    const view = new URLSearchParams(window.location.search).get('view');
    if (managementOnly && (!view || view==='deployments' || view==='tenants' || view==='environments' || view==='images')) {
      setShowFleet(false); setShowTaskboard(false); setAdminTab(view==='tenants'?'tenants':'deployments'); setShowAdmin(true);
    }
    if (view === 'taskboard') {
      setShowFleet(false);
      setShowAdmin(false);
      setShowTaskboard(true);
    }
    if (!codingOnly && view === 'cc-chat') setCcConversation({title: 'Main'});
  }, []);
  const [failure, setFailure] = useState('');
  const [development, setDevelopment] = useState<{status: string; editor_ready: boolean; preview_ready?: boolean; chat_ready?: boolean; dev_preview_url?:string}>({status: '', editor_ready: false});
  const [publication,setPublication] = useState<{project:string; url:string|null; ready:boolean; runtime:string}>();
  const [working, setWorking] = useState(false);

  const [runtimeError, setRuntimeError] = useState('');
  const selectionTouched = useRef(false);
  const [selectionResolved,setSelectionResolved] = useState(false);
  const [previewRevision, setPreviewRevision] = useState(0);
  const [previewConsole, setPreviewConsole] = useState<string[]>([]);
  const [previewEnvironment, setPreviewEnvironment] = useState<Environment>('DEV');
  const [previewConfig, setPreviewConfig] = useState<{project:string; settings:PreviewSettings; error?:boolean}>();
  const [previewSource, setPreviewSource] = useState<{project:string;tree:string}>();
  const [viewport, setViewport] = useState('100%');
  const available = projects.filter(project => !project.archived);
  const sidebarProjects = available.filter(project =>
    !['browser validation','public github','public github read validation','public github validation']
      .includes(project.name.trim().replace(/\s+/g,' ').toLowerCase()));
  const selected = available.find(project => project.id === prefs.project) ?? available[0];
  const expandedProjects = prefs.expandedProjects ?? (selected && !prefs.collapsed?.includes(selected.id) ? [selected.id] : []);
  useEffect(()=>{
    let mounted=true;
    const load=()=>void fetch('/api/task-navigation',{cache:'no-store'}).then(async response=>{
      if (!response.ok) return;
      const rows:SidebarTask[]=await response.json();
      if (mounted) setSidebarTasks(rows);
    }).catch(()=>{});
    load();
    const timer=setInterval(load,3000);
    return ()=>{mounted=false;clearInterval(timer);};
  },[]);
  const active = objectives.flatMap(objective => objective.progress.active);
  const workspace = selected?.workspace_url;
  const editor = workspace ? workspace.replace(/\/$/, '') + '/apps/editor/' : null;
  const previewRoot = workspace && development.dev_preview_url===workspace.replace(/\/$/, '') + '/apps/dev-preview/' ? development.dev_preview_url : null;
  const sourceTree = previewSource?.project===selected?.id ? previewSource?.tree || 'repository' : 'repository';
  const previewUrl = previewRoot ? previewRoot + '?' + new URLSearchParams({
    __smartdev_base: new URL(previewRoot).pathname.replace(/\/$/,''),
    __smartdev_tree: sourceTree,
  }) : null;
  const previewSettings=selectionResolved && previewConfig?.project===selected?.id ? previewConfig : undefined;
  const configured=previewSettings?.settings[previewEnvironment] || defaultPreview;
  const configuredUrl=configured.url;
  // These deployed applications need their own origin for storage and API calls.
  // Other configured URLs retain an opaque sandboxed origin.
  const deploymentPreview = configuredUrl && selected && typeof window!=='undefined'
    && /^smd-[a-f0-9]{32}\./.test(window.location.hostname)
    && new URL(configuredUrl).origin==='https://app-'+selected.id.replaceAll('-','').slice(0,12)+'-'+window.location.hostname.slice(4,16)+window.location.hostname.slice(36);
  const registeredProductPreview = deploymentPreview || configuredUrl && /^https:\/\/(?:v2\.bcentrisk\.com|vendorgate\.ca)\//.test(configuredUrl);
  const automatic=Boolean(previewSettings && !previewSettings.error && !configuredUrl &&
    configured.mode==='automatic' && previewEnvironment==='DEV' && workspace);
  const frameUrl=managementOnly ? null : configuredUrl || (automatic && development.preview_ready && coderConnection==='connected' ? previewUrl : null);
  const previewMenu=useRef<HTMLDetailsElement>(null);
  const [previewAction,setPreviewAction]=useState('');
  const [restartingPreview,setRestartingPreview]=useState(false);
  const sources=Array.from(new Map(objectives.flatMap(task=>[...task.progress.active,...task.progress.attempts]
    .filter(run=>run.worktree.startsWith('worktrees/task-')).map(run=>[run.worktree,task.expected_outcome] as const))).entries());
  const selectSource=()=> <select aria-label="Preview source" value={sourceTree}
      onChange={event=>selected && setPreviewSource({project:selected.id,tree:event.target.value})}>
    <option value="repository">Project checkout</option>
    {sources.map(([tree,label])=><option key={tree} value={tree}>{label.slice(0,65)}</option>)}
  </select>;
  function closePreviewMenu() {
    if (previewMenu.current) previewMenu.current.open=false;
  }
  async function restartPreview() {
    if (!selected || restartingPreview) return;
    setRestartingPreview(true);
    try {
      const response=await fetch('/api/projects/'+selected.id+'/dev-publishing',{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'restart'}),
      });
      if (!response.ok) throw Error((await response.json()).detail || 'Preview restart failed.');
      setPreviewAction('Preview restart requested.');
    } catch(error) {
      setPreviewAction(error instanceof Error ? error.message : 'Preview restart failed.');
    } finally { setRestartingPreview(false); }
  }
  const selectedProjectId=selected?.id;
  useEffect(()=>{
    if (!selectedProjectId) return;
    const request=new AbortController();
    void fetch('/api/projects/'+selectedProjectId+'/preview',{signal:request.signal,cache:'no-store'}).then(async response=>{
      if (!response.ok) throw Error('Preview configuration could not be loaded.');
      const settings=await response.json() as PreviewSettings;
      if (!request.signal.aborted) setPreviewConfig({project:selectedProjectId,settings});
    }).catch(()=>{
      if (!request.signal.aborted) setPreviewConfig({project:selectedProjectId,settings:{},error:true});
    });
    return ()=>request.abort();
  },[selectedProjectId]);
  const automaticStart=useRef(new Set<string>());
  useEffect(()=>{
    if (!automatic || prefs.view!=='Preview' || !selectedProjectId || working ||
        coderConnection!=='connected' || development.status!=='stopped' ||
        automaticStart.current.has(selectedProjectId)) return;
    automaticStart.current.add(selectedProjectId);
    void lifecycle('start');
  },[automatic,prefs.view,selectedProjectId,development.status,working,coderConnection]);
  useEffect(() => {
    setPreviewConsole([]);
    if (!previewRoot) return;
    const origin = new URL(previewRoot).origin;
    function onConsole(event: MessageEvent) {
      const frame = document.querySelector<HTMLIFrameElement>('iframe[title="DEV application preview"]');
      if (event.source !== frame?.contentWindow || event.origin !== origin ||
          event.data?.source !== 'smartdev-preview') return;
      if (event.data.action==='configure-secrets') {
        setPrefs(current=>({...current,view:'Project settings',hidePreview:false}));
        return;
      }
      if (typeof event.data.message !== 'string') return;
      setPreviewConsole(lines=>[...lines.slice(-49),event.data.message.slice(0,1200)]);
    }
    window.addEventListener('message',onConsole);
    return ()=>window.removeEventListener('message',onConsole);
  }, [previewRoot]);

  useEffect(() => {
    try {
      const current = localStorage.getItem('smartdev.workspace.v3');
      const saved = JSON.parse(current ?? localStorage.getItem('smartdev.workspace.v2') ?? localStorage.getItem('smartdev.workspace.v1') ?? '{}');
      setPrefs({...initial, ...saved, showRight: current ? saved.showRight ?? false : false,
        view: current && ['Preview','Git','Logs','Project settings','Project context','Publishing','Domains'].includes(saved.view) ? saved.view : 'Preview',
        dark: saved.dark ?? true});
    } catch {}
    void fetch('/api/account/project-selection',{cache:'no-store',signal:AbortSignal.timeout(3500)}).then(async response=>{
      if (!response.ok) return;
      const {id}=await response.json();
      if (typeof id==='string' && !selectionTouched.current) setPrefs(current=>({...current,project:id}));
    }).catch(()=>{}).finally(()=>setSelectionResolved(true));
    setRestored(true);
  }, []);
  useEffect(() => {
    if (restored) localStorage.setItem('smartdev.workspace.v3', JSON.stringify(prefs));
  }, [prefs, restored]);
  const openWorkspaces = useCallback(async () => {
    setFleetOpening(true);
    setFleetError('');
    try {
      const current = await fetch('/api/v2/users/me', {cache: 'no-store'});
      if (!current.ok) {
        const response = await fetch('/api/coder-session?target=management', {method: 'POST'});
        if (!response.ok) throw Error();
        const {endpoint, grant} = await response.json();
        const exchange = await fetch(endpoint, {method: 'POST',
          headers: {'Content-Type': 'application/json'}, body: JSON.stringify({grant})});
        if (!exchange.ok) throw Error();
      }
      setFleetReady(true);
    } catch {
      setFleetError('Could not open Workspaces. Try again.');
    } finally {
      setFleetOpening(false);
    }
  }, []);
  useEffect(() => {
    const view = new URLSearchParams(window.location.search).get('view');
    if (managementOnly && view && !['deployments', 'environments', 'images', 'tenants', 'taskboard'].includes(view)) {
      void openWorkspaces();
    }
  }, [managementOnly, openWorkspaces]);

  async function connectCoder() {
    setCoderConnection('connecting');
    try {
      const response = await fetch('/api/coder-session', {method: 'POST', cache: 'no-store'});
      if (!response.ok) throw new Error('Coder handoff is unavailable.');
      const {endpoint, grant} = await response.json();
      const exchange = await fetch(endpoint, {
        method: 'POST', credentials: 'include', cache: 'no-store',
        headers: {'Content-Type': 'application/json'}, body: JSON.stringify({grant}),
      });
      if (!exchange.ok) throw new Error('Coder did not accept the handoff.');
      setCoderConnection('connected');
    } catch {
      setCoderConnection('failed');
    }
  }
  useEffect(() => { void connectCoder(); }, []);
  useEffect(() => {
    if (!selected?.id || managementOnly) return;
    let closed = false;
    setObjectives([]);
    setFailure('');
    setDevelopment({status: '', editor_ready: false});
    setRuntimeError('');
    async function runtime() {
      try {
        const response = await fetch('/api/projects/' + selected.id + '/development');
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail || 'Workspace status is unavailable.');
        if (!closed) setDevelopment(result);
      } catch {
        if (!closed) setDevelopment({status: 'unavailable', editor_ready: false});
      }
    }
    void runtime();
    const runtimeTimer = setInterval(runtime, 4000);
    const project=selected.id;
    async function publishing() {
      try {
        const response=await fetch('/api/projects/'+project+'/dev-publishing',{cache:'no-store'});
        if (!response.ok) throw Error();
        if (!closed) setPublication({...(await response.json()),project});
      } catch { if (!closed) setPublication(undefined); }
    }
    void publishing();
    const publishingTimer=setInterval(publishing,5000);
    async function refresh() {
      if (showTaskboard) return;
      try {
        const response = await fetch('/api/projects/' + selected.id + '/objectives');
        if (!response.ok) throw new Error('Task status is unavailable.');
        const rows: Objective[] = await response.json();
        if (!closed) { setObjectives(rows); setFailure(''); }
      } catch {
        if (!closed) setFailure('Task status is unavailable.');
      }
    }
    void refresh();
    const timer = setInterval(refresh, 3000);
    return () => { closed = true; clearInterval(timer); clearInterval(runtimeTimer); clearInterval(publishingTimer); };
  }, [selected?.id,showTaskboard]);
  useEffect(() => {
    function shortcuts(event: KeyboardEvent) {
      if (!event.altKey || event.shiftKey || event.ctrlKey) return;
      if (event.key === '1' || event.key === '2' || event.key === '3') {
        event.preventDefault();
        const panel = event.key === '1' ? 'showLeft' : event.key === '2' ? 'showCommand' : 'showRight';
        setPrefs(current => ({...current, [panel]: !current[panel]}));
      }
    }
    window.addEventListener('keydown', shortcuts);
    return () => window.removeEventListener('keydown', shortcuts);
  }, []);

  function resize(side: ResizablePanel, event: PointerEvent<HTMLDivElement>) {
    const grip = event.currentTarget;
    grip.setPointerCapture(event.pointerId);
    const start = event.clientX;
    const original = prefs[side];
    grip.onpointermove = move => changeWidth(side, original + (move.clientX - start) * (side === 'right' ? -1 : 1));
    grip.onlostpointercapture = () => { grip.onpointermove = null; };
  }
  function changeWidth(side: ResizablePanel, width: number) {
    setPrefs(current => ({...current, [side]: Math.max(side === 'left' ? 160 : 240, Math.min(width, window.innerWidth * .62))}));
  }
  function divider(side: ResizablePanel) {
    return <div className="wb-divider" role="separator" aria-label={side + ' panel width'} aria-orientation="vertical"
      tabIndex={0} onPointerDown={event => resize(side, event)} onKeyDown={event => {
        if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
        event.preventDefault();
        changeWidth(side, prefs[side] + (event.key === 'ArrowRight' ? 16 : -16) * (side === 'right' ? -1 : 1));
      }} />;
  }
  useEffect(() => {
    if (selected?.id && development.chat_ready && coderConnection === 'connected') {
      setChatOpened(current => current.includes(selected.id) ? current : [...current, selected.id]);
    }
  }, [selected?.id, development.chat_ready, coderConnection]);
  useEffect(() => {
    const projectId = selected?.id;
    if (!workspace || !projectId) return;
    const origin = new URL(workspace).origin;
    function message(event: MessageEvent) {
      const frame = document.querySelector<HTMLIFrameElement>('#command-panel iframe:not([hidden])');
      if (event.origin !== origin || !frame || event.source !== frame.contentWindow || event.data?.source !== 'smartdev-chat') return;
      if (event.data.type === 'task') {
        let observed=taskNavigation?.project===projectId ? taskNavigation : undefined;
        if (event.data.statuses) {
          const validIds=(list:unknown)=>Array.isArray(list) ? list.filter(id=>typeof id==='string' && /^[a-f0-9-]{36}$/i.test(id)).slice(0,500) : [];
          const {running,waiting,queued,paused}=event.data.statuses;
          const snapshot={project:projectId,focused:validIds([event.data.focus])[0] || null,
            running:validIds(running),waiting:validIds(waiting),queued:validIds(queued),paused:paused===true};
          observed=snapshot;
          setTaskNavigation(previous=>JSON.stringify(previous)===JSON.stringify(snapshot) ? previous : snapshot);
          const command=pendingNavigation.current;
          if (command?.project===projectId) {
            pendingNavigation.current=undefined;
            frame.contentWindow?.postMessage({source:'smartdev-portal',type:command.type,id:command.id},origin);
          }
        }
        const task = objectives.find(item => item.id === event.data.id);
        const sidebarTask = sidebarTasks.find(item=>item.id===event.data.id && item.project_id===projectId);
        frame.contentWindow?.postMessage({source:'smartdev-portal',theme:prefs.dark?'dark':'light',task: (task || sidebarTask) && {
          id:event.data.id, title:sidebarTask?.title || "Project development task", original:task?.original_request, status:task ? taskStatus(task,observed) : sidebarTask?.status,
          branch:task && (task.progress.active[0]?.branch || lastAttempt(task)?.branch), nextAction:task?.progress.nextAction,
        }},origin);
      } else if (event.data.type === 'open-task' && typeof event.data.id === 'string') {
        setHighlightedTask(event.data.id);
        setShowTaskDetails(true);
      } else if (event.data.type === 'relationships' && Array.isArray(event.data.relationships)) {
        const ids = /^[a-f0-9-]{36}$/i;
        const pairs = event.data.relationships.slice(0,1000).filter((row: {objective?: string;parentTaskId?: string}) =>
          row && typeof row.objective === 'string' && typeof row.parentTaskId === 'string' && ids.test(row.objective) && ids.test(row.parentTaskId));
        if (pairs.length) setParents(previous => ({...previous,...Object.fromEntries(pairs.map(
          (row: {objective: string;parentTaskId: string}) => [projectId + ':' + row.objective,row.parentTaskId]
        ))}));
      } else if (event.data.type === 'manage-auth') {
        setPrefs(p=>({...p,view:'Project settings',hidePreview:false}));
        setExpandedView(null);
      } else if (event.data.type === 'review') {
        const review = event.data.review;
        if (!review || !['objective','tree','branch','head','base','diff'].every(key => typeof review[key] === 'string')
            || review.diff.length > 140000) return;
        setGitReview({project:projectId,review});
        setPrefs(p=>({...p, view:'Git',hidePreview:false}));
        setExpandedView(null);
      }
    }
    window.addEventListener('message',message);
    return () => window.removeEventListener('message',message);
  }, [selected?.id, workspace, objectives, sidebarTasks, prefs.dark, taskNavigation]);
  useEffect(() => {
    if (!showTaskDetails || creating || !highlightedTask || !prefs.showCommand) return;
    const detail = document.getElementById('objective-' + highlightedTask);
    if (detail) {
      detail.scrollIntoView({block:'nearest'});
    }
  }, [highlightedTask, showTaskDetails, creating, prefs.showCommand]);
  function choose(project: Project) {
    selectionTouched.current=true;
    setSelectionResolved(true);
    if (pendingNavigation.current?.project!==project.id) pendingNavigation.current=undefined;
    setPrefs(current => ({...current, project: project.id,
      expandedProjects: current.expandedProjects ?? expandedProjects}));
    setShowTaskDetails(false);
    setMenu('');
    void fetch('/api/account/project-selection/'+project.id,{method:'PUT'}).catch(()=>{});
    setCreating(false);
  }
  async function lifecycle(action: 'start' | 'stop') {
    if (!selected || working || (action === 'stop' && !window.confirm('Stop this development workspace and its running processes?'))) return;
    setWorking(true);
    setRuntimeError('');
    try {
      const response = await fetch('/api/projects/' + selected.id + '/development', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({action}),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || 'Coder rejected the request.');
      setDevelopment(result);
    } catch (error) {
      setRuntimeError(error instanceof Error ? error.message : 'Could not update the workspace.');
    } finally { setWorking(false); }
  }
  function openEditor() {
    if (!selected || !editor) return;
    setOpened(current => current.includes(selected.id) ? current : [...current, selected.id]);
    setPrefs(current => ({...current, showRight: true}));
  }
  function lastAttempt(task: Objective) {
    return task.progress.attempts.reduce<Objective['progress']['attempts'][number] | undefined>((latest, attempt) =>
      !latest || attempt.started_at > latest.started_at ? attempt : latest, undefined);
  }

  const navigation = taskNavigation?.project === selected?.id ? taskNavigation : undefined;
  function taskStatus(task: Objective, live=navigation) {
    if (live?.waiting.includes(task.id)) return 'Awaiting input';
    if (live?.running.includes(task.id) || task.progress.active.length) return 'Running';
    if (live?.queued.includes(task.id)) return live.paused ? 'Paused' : 'Queued';
    if (task.status==='done') return 'Completed';
    if (task.status==='blocked') return 'Blocked';
    if (task.status==='ready_for_review') return 'Ready for review';
    const attempt=lastAttempt(task);
    if (attempt?.finished_at && attempt.exit_code===130) return 'Paused';
    if (attempt?.finished_at && attempt.exit_code && attempt.exit_code!==0) return 'Failed';
    return 'Active';
  }
  function sendChatNavigation(project:Project, type:ChatNavigationCommand['type'], id?:string) {
    if (!project.workspace_url) return;
    setShowTaskboard(false);
    const frame=document.querySelector<HTMLIFrameElement>('iframe[data-chat-project="'+project.id+'"]');
    if (project.id===selected?.id && navigation && frame && !frame.hidden) {
      frame.contentWindow?.postMessage({source:'smartdev-portal',type,id},new URL(project.workspace_url).origin);
    } else {
      pendingNavigation.current={project:project.id,type,id};
      if (project.id!==selected?.id) choose(project);
    }
    setShowTaskDetails(false);
    setCreating(false);
    setExpandedView(null);
    setPrefs(p=>({...p,showCommand:true}));
  }
  const showProjects = !managementOnly && (mobileLayout ? mobileProjects : prefs.showLeft) && (showTaskboard || !expandedView);
  const toggleProjects = () => mobileLayout ? setMobileProjects(open => !open)
    : setPrefs(previous => ({...previous, showLeft: !previous.showLeft}));
  const showCommand = prefs.showCommand && !expandedView;
  const showView = expandedView !== 'files' && (expandedView === 'preview' || !prefs.hidePreview);
  const showFiles = expandedView === 'files' || (!expandedView && prefs.showRight);
  const published=publication?.project===selected?.id ? publication : undefined;
  useEffect(() => {
    if (showFiles && selected?.id && development.editor_ready && coderConnection === 'connected') {
      setOpened(current => current.includes(selected.id) ? current : [...current, selected.id]);
    }
  }, [showFiles, selected?.id, development.editor_ready, coderConnection]);
  function toggleView(view: Preferences['view']) {
    setShowTaskboard(false);
    if (expandedView === 'files') setExpandedView(null);
    setPrefs(p => ({...p, view, hidePreview:false}));
  }
  function toggleFiles() {
    setShowTaskboard(false);
    if (showFiles) {
      setExpandedView(null);
      setPrefs(p=>({...p,showRight:false}));
    } else {
      setExpandedView(null);
      openEditor();
      setPrefs(p=>({...p,showRight:true}));
    }
  }

  async function renameTask(task: SidebarTask) {
    const name = window.prompt('Task name', task.title || 'Project development task')?.trim();
    if (!name) return;
    try {
      const response = await fetch('/api/projects/' + task.project_id + '/objectives/' + task.id + '/display-name', {
        method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name}),
      });
      const result=await response.json();
      if (!response.ok) throw Error(typeof result.detail==='string'?result.detail:'Could not rename task.');
      setSidebarTasks(rows => rows.map(row => row.id === task.id ? {...row, title:result.display_name} : row));
      setObjectives(rows => rows.map(row => row.id === task.id ? {...row, display_name:result.display_name} : row));
    } catch (error) { setFailure(error instanceof Error ? error.message : 'Could not rename task.'); }
  }

  function inspectTask(id: string) {
    setShowTaskboard(false);
    setHighlightedTask(id);
    setCreating(false);
    setShowTaskDetails(true);
    setPrefs(p => ({...p, showCommand:true}));
    setMenu('');
  }

  return <div className={managementOnly && showAdmin && adminTab==='deployments' ? "compute-workbench" : "workbench"} data-theme={prefs.dark ? 'dark' : 'light'}>
    {!(managementOnly && showAdmin && adminTab==='deployments') && <header className="wb-toolbar">
      <div className="wb-toolbar-group">
        <strong className="wb-brand"><span className="brand-mark" aria-hidden="true">S</span>SmartDev</strong>
        {!managementOnly && <><select className="wb-project-switch" aria-label="Select project" value={selected?.id || ''} onChange={event => {
          const project = available.find(item => item.id === event.target.value);
          if (project) choose(project);
        }}>
          {!selected && <option value="">Select project</option>}
          {available.map(project => <option key={project.id} value={project.id}>{project.name}</option>)}
        </select>
        <button aria-pressed={!showAdmin && !showTaskboard && prefs.view !== 'Project settings'} onClick={() => {setShowAdmin(false);setShowTaskboard(false);setShowTaskDetails(false);setCreating(false);if(prefs.view==='Project settings') toggleView('Preview');}}>Build</button></>}
      </div>
      <div className="wb-toolbar-group wb-actions">
        {codingOnly && typeof window!=='undefined' && window.location.hostname==='coder.smartdev.youkandoit.com'
          && selected?.id==='bca501cf-7f58-4cd6-b254-33bf4fe48c7c' &&
          <a href="https://smartdev.youkandoit.com/" target="_blank" rel="noopener noreferrer">Open Command Center</a>}
        {!codingOnly && <button type="button" onClick={() => setCcConversation({title: 'Main'})}>Ask CC</button>}
        <button type="button" aria-pressed={showTaskboard} onClick={() => {
          setShowFleet(false);
          setShowTaskboard(true);
          setShowAdmin(false);
        }}>Taskboard</button>
        {managementOnly ? <>
          <button type="button" aria-pressed={showFleet && fleetView === 'workspaces'} onClick={() => {
            setShowAdmin(false); setShowTaskboard(false); setShowFleet(true); setFleetPathOverride(null); setFleetView('workspaces');
            if (!fleetReady && !fleetOpening) void openWorkspaces();
          }}>Workspaces</button>
          {(['deployments', 'tenants'] as const).map(tab =>
            <button key={tab} type="button" aria-pressed={showAdmin && adminTab === tab} onClick={() => {
              setShowFleet(false); setShowTaskboard(false); setAdminTab(tab); setShowAdmin(true);
            }}>{tab === 'deployments' ? 'Deployments' : 'Tenants'}</button>)}
          {(['ai', 'settings'] as const).map(view =>
            <button key={view} type="button" aria-pressed={showFleet && fleetView === view} onClick={() => {
              setShowAdmin(false); setShowTaskboard(false); setShowFleet(true); setFleetPathOverride(null); setFleetView(view);
              if (!fleetReady && !fleetOpening) void openWorkspaces();
            }}>{fleetPages[view].label}</button>)}
        </> : !codingOnly && <button type="button" aria-pressed={showAdmin} onClick={() => {
          setShowAdmin(open => !open);setShowTaskboard(false);
        }}>Admin</button>}
        {!managementOnly && !showAdmin && <button onClick={toggleProjects} aria-controls="projects-panel" aria-expanded={showProjects}>{showProjects ? <PanelLeftClose aria-hidden /> : <PanelLeftOpen aria-hidden />}{showProjects ? 'Hide Projects' : 'Show Projects'}</button>}
        {!managementOnly && !showAdmin && <button onClick={toggleFiles} aria-controls="editor-panel" aria-expanded={showFiles}>{showFiles ? <PanelRightClose aria-hidden /> : <PanelRightOpen aria-hidden />}{showFiles ? 'Hide Files' : 'Show Files'}</button>}
        {!managementOnly && published?.ready && published.url
          ? <a className="wb-open-app" href={published.url} target="_blank" rel="noopener noreferrer">Open application</a>
          : null}
        {managementOnly ? <>
          <a className="wb-open-app" href="https://coder.smartdev.youkandoit.com/">Open Coder</a>
          {signOut}
        </> : <details className="wb-menu">
          <summary><Settings aria-hidden />Settings</summary>
          <div className="wb-menu-items">
            <button type="button" onClick={event => {
              event.currentTarget.closest('details')?.removeAttribute('open');
              setShowIntegrations(true);
            }}>Integrations</button>
            <button onClick={() => setPrefs(p => ({...p, showCommand: !p.showCommand}))} aria-controls="command-panel" aria-expanded={prefs.showCommand}>{prefs.showCommand ? 'Hide agent' : 'Show agent'}</button>
            <button onClick={() => {setExpandedView(null);setPrefs(p=>({...p,hidePreview:!p.hidePreview}));}}>{showView ? 'Hide preview workspace' : 'Show preview workspace'}</button>
            <button onClick={() => setPrefs(p => ({...p, dark: !p.dark}))}>{prefs.dark ? 'Light theme' : 'Dark theme'}</button>
            {development.status === 'stopped' && <button disabled={working} onClick={() => void lifecycle('start')}>Start DEV</button>}
            {development.status === 'running' && <button disabled={working} onClick={() => void lifecycle('stop')}>Stop DEV</button>}
            {selected && <button onClick={event=>{
              event.currentTarget.closest('details')?.removeAttribute('open');
              toggleView('Project settings');
            }}>Project settings</button>}
            {workspace && <a href={workspace} target="_blank" rel="noreferrer">Manage DEV</a>}
            {editor && <a href={editor} target="_blank" rel="noreferrer">Full IDE</a>}
            {signOut}
          </div>
        </details>}
      </div>
    </header>}
    {ccConversation && <CcConversation key={ccConversation.taskId || 'main'} {...ccConversation}
      onClose={() => setCcConversation(undefined)} />}
    {managementOnly && <section hidden={!showFleet} aria-label={fleetPages[fleetView].label}>
      {fleetError && <p role="alert">{fleetError}</p>}
      {fleetOpening && <p role="status">Opening Workspaces…</p>}
      {fleetReady && showFleet && <iframe title={fleetView === 'workspaces' ? 'Coder workspaces' : fleetPages[fleetView].label}
        src={fleetPathOverride ?? fleetPages[fleetView].path} onLoad={event => {
          fleetObserver.current?.disconnect();
          const doc = event.currentTarget.contentDocument;
          if (!doc?.body) return;
          const updateNativeNavigation = () => {
            for (const button of doc.querySelectorAll<HTMLButtonElement>('button[aria-haspopup="menu"]')) {
              if (button.textContent?.trim() === 'Admin settings') button.hidden = true;
            }
            const agents = [...doc.querySelectorAll<HTMLAnchorElement>('a[href="/agents"]')]
              .find(link => link.textContent?.trim() === 'Agents');
            if (!agents?.parentElement || agents.parentElement.querySelector('[data-smartdev-fleet-link]')) return;
            const links = (['deployment', 'health'] as const).map(view => {
              const page = fleetPages[view];
              const link = doc.createElement('a');
              link.dataset.smartdevFleetLink = view;
              link.className = agents.className;
              link.href = page.path;
              link.textContent = page.label;
              return link;
            });
            agents.after(...links);
          };
          updateNativeNavigation();
          const style = doc.createElement('style');
          style.textContent = 'button[aria-haspopup="menu"][hidden] { display: none !important }';
          doc.head.append(style);
          fleetObserver.current = new MutationObserver(updateNativeNavigation);
          fleetObserver.current.observe(doc.body, {subtree: true, childList: true});
        }}
        style={{display: 'block', width: '100%', height: 'calc(100dvh - 56px)', border: 0}} />}
    </section>}
    {showAdmin && <AdminProjects dockerExport={dockerExport} projects={projects} signOut={signOut} onAsk={()=>setCcConversation({title:'Main'})} activeTab={managementOnly ? adminTab : undefined} />}
    {showIntegrations && <Integrations projectId={selected?.id} repository={selected?.repository} onClose={() => setShowIntegrations(false)} />}
    {(!managementOnly || showTaskboard) && <div className="wb-grid" hidden={showAdmin} style={{display: showAdmin ? 'none' : undefined, gridTemplateColumns: [
      ...(showProjects ? ['minmax(150px, min(' + prefs.left + 'px, 19vw))', '5px'] : []),
      ...(showTaskboard ? ['minmax(0, 1fr)'] : [
        ...(showCommand ? [showView ? 'minmax(270px, min(' + prefs.command + 'px, ' + (showFiles ? '31vw' : '42vw') + '))' : 'minmax(270px, 1fr)'] : []),
        ...(showCommand && showView ? ['5px'] : []),
        ...(showView ? ['minmax(220px, 1fr)'] : []),
        ...(showFiles ? showView ? ['5px','minmax(0, min(' + prefs.right + 'px, 26vw))'] : ['minmax(0, 1fr)'] : []),
      ]),
    ].join(' ')}}>
      <aside className="wb-side" id="projects-panel" hidden={!showProjects}>
        <div className="wb-panel-header">
          <h2>Projects</h2>
          <button className="wb-add" aria-label="New or import project" title="New or import project" onClick={() => {setShowTaskboard(false);setCreating(true); setShowTaskDetails(false);setPrefs(p=>({...p,showCommand:true}));}}><Plus aria-hidden /></button>
          <button aria-label="Collapse projects panel" title="Collapse projects panel" onClick={toggleProjects}><PanelLeftClose aria-hidden /></button>
        </div>
        <nav className="wb-project-tree" aria-label="Projects and tasks" onKeyDown={event=>{if (event.key==='Escape') setMenu('');}}>
          {sidebarProjects.map(project => {
            const current = selected?.id === project.id;
            const expanded = expandedProjects.includes(project.id);
            return <div className="wb-tree-project" key={project.id}>
              <div className="wb-tree-row" onContextMenu={event=>{event.preventDefault();setMenu(project.id);}}>
                <button className="wb-tree-select wb-tree-heading" aria-expanded={expanded}
                  aria-controls={'project-children-'+project.id} title={project.name} onClick={()=>{
                    setPrefs(p=>({...p,expandedProjects:expanded
                      ? expandedProjects.filter(id=>id!==project.id) : [...expandedProjects,project.id]}));
                  }}><span className="wb-tree-icon" aria-hidden>{expanded ? '▾' : '▸'}</span><span className="wb-tree-title">{project.name}</span></button>
                <button className="wb-tree-overflow" aria-label={project.name+' actions'} aria-expanded={menu===project.id}
                  onClick={()=>setMenu(menu===project.id?'':project.id)}><Ellipsis aria-hidden /></button>
              </div>
              {menu===project.id && <div className="wb-tree-actions">
                <button disabled={!project.workspace_url} onClick={()=>{choose(project);toggleView('Project context');}}>Project context</button>
                <button onClick={()=>{choose(project);toggleView('Publishing');}}>Publishing</button>
                <button onClick={()=>{choose(project);toggleView('Domains');}}>Domains</button>
                <button onClick={()=>{choose(project);toggleView('Project settings');}}>Project settings</button>
                <DockerDownload project={project} bundle={dockerExport} />
                {project.status==='failed' && <button onClick={()=>{setMenu('');updateProject(project,'retry');}}>Retry provisioning</button>}
                {['created','failed'].includes(project.status) && <button onClick={()=>{setMenu('');updateProject(project,'archive');}}>Archive project</button>}
              </div>}
              {expanded && <div id={'project-children-'+project.id} className="wb-tree-tasks">
                <button disabled={!project.workspace_url} className="wb-tree-select" aria-current={current && navigation && !navigation.focused ? 'true' : undefined}
                  onClick={()=>sendChatNavigation(project,'focus-task')}><span className="wb-tree-icon" aria-hidden /><span className="wb-tree-title">Main</span></button>
                <button disabled={!project.workspace_url} className="wb-tree-select wb-tree-new" onClick={()=>sendChatNavigation(project,'new-task')}>
                  <span className="wb-tree-icon"><Plus aria-hidden /></span><span className="wb-tree-title">New task</span></button>
                {sidebarTasks.filter(task=>task.project_id===project.id).map(task=>{
                  const status=current && navigation?.waiting.includes(task.id) ? 'Awaiting input'
                    : current && navigation?.running.includes(task.id) ? 'Running'
                    : current && navigation?.queued.includes(task.id) ? navigation.paused?'Paused':'Queued'
                    : task.status;
                  const title=task.title || 'Project development task';
                  return <div key={task.id}>
                    <div className="wb-tree-row" onContextMenu={event=>{event.preventDefault();setMenu(task.id);}}>
                      <button className="wb-tree-select" disabled={!project.workspace_url} title={title+' · '+status}
                        aria-current={current && navigation?.focused===task.id ? 'true' : undefined}
                        onClick={()=>sendChatNavigation(project,'focus-task',task.id)}>
                        <span className="wb-tree-icon"><span className="wb-navigation-dot" data-status={status} aria-label={status} role="img" /></span>
                        <span className="wb-tree-title">{title}</span>
                      </button>
                      <button className="wb-tree-overflow" aria-label={title+' actions'} aria-expanded={menu===task.id}
                        onClick={()=>setMenu(menu===task.id?'':task.id)}><Ellipsis aria-hidden /></button>
                    </div>
                    {menu===task.id && <div className="wb-tree-actions">
                      <button onClick={()=>{setMenu('');void renameTask(task);}}>Rename task</button>
                      {current && <button onClick={()=>inspectTask(task.id)}>Task details</button>}
                    </div>}
                  </div>;
                })}
              </div>}
            </div>;
          })}
        </nav>
      </aside>{showProjects && (showTaskboard || showCommand || showView) && divider('left')}
      {showTaskboard && <div className="wb-taskboard-panel">
        <Taskboard projects={available} navigation={navigation} sidebarTasks={sidebarTasks} parents={parents}
          onAskCc={codingOnly ? undefined : (taskId, title) => setCcConversation({taskId, title})}
          onReply={(projectId, taskId) => {
            const project = available.find(item => item.id === projectId);
            if (managementOnly) {
              setCcConversation({taskId, title: project?.name ?? 'Task'});
            } else if (project) sendChatNavigation(project, 'focus-task', taskId);
          }} />
      </div>}
      <aside className="wb-command" id="command-panel" aria-label="AI conversation" hidden={showTaskboard || !showCommand}>
        {!showTaskDetails && !creating && selected && coderConnection === 'connected' && !development.chat_ready &&
          <div className="wb-empty"><h3>{development.status === 'stopped' ? 'DEV is stopped' : 'The coding agent is waiting for DEV'}</h3>
            <p>{development.status === 'stopped' ? 'Start the workspace to open the coding agent.' : 'Chat opens when the workspace reports it ready.'}</p>
            {development.status === 'stopped' && <button disabled={working} onClick={() => void lifecycle('start')}>Start DEV</button>}
          </div>}
        {!showTaskDetails && !creating && selected && coderConnection !== 'connected' && <div className="wb-empty" role="status">
          <h3>{coderConnection === 'connecting' ? 'Connecting to coding agent' : 'Coder connection unavailable'}</h3>
          {coderConnection === 'failed' && <button onClick={() => void connectCoder()}>Reconnect</button>}
        </div>}
        {!managementOnly && available.filter(project => chatOpened.includes(project.id) && project.workspace_url).map(project =>
          <iframe key={project.id} data-chat-project={project.id} className="wb-chat" hidden={showTaskDetails || creating || coderConnection !== 'connected' || project.id !== selected?.id}
            title={project.name + ' coding-agent chat'} src={project.workspace_url!.replace(/\/$/, '') + '/apps/codex-chat/'}
            allow="clipboard-read; clipboard-write" />)}
        {runtimeError && <p role="alert" className="error">{runtimeError}</p>}
        {failure && <p role="alert" className="error">{failure}</p>}
        {creating ? createForm : !selected ? <p>Select or create a project.</p> : <>
          {selected.error && <p role="alert" className="error">{selected.error}</p>}
          {showTaskDetails && <div className="wb-content">
            <button className="wb-back-chat" onClick={()=>setShowTaskDetails(false)}>← Conversation</button>
            <div className="wb-task-details">
            {objectives.filter(task=>task.id===highlightedTask).map(task => {
              const attempt = lastAttempt(task);
              return <section id={'objective-' + task.id} key={task.id}>
              <h2>{task.display_name || task.expected_outcome}</h2>
              <p>{task.original_request}</p>
              {!!task.progress.executionSteps?.length && <details>
                <summary>Execution steps</summary>
                <ol>{task.progress.executionSteps.map(step=><li key={step.id}>
                  {step.criterion_id} · {step.agent} · {step.finished_at ? 'Exit '+step.exit_code : 'Running'}
                  {' · '}{new Date(step.started_at).toLocaleString()}
                </li>)}</ol>
              </details>}
              <p><code>{task.id}</code></p>
              {parents[selected.id + ':' + task.id] && <p>Parent task <code>{parents[selected.id + ':' + task.id]}</code></p>}
              <p>{task.progress.completedCriteria.length} verified · {task.progress.outstandingCriteria.length} outstanding</p>
              {task.progress.nextAction && <p>{task.progress.nextAction}</p>}
              {attempt && <p>{attempt.branch} · {attempt.worktree} · {new Date(attempt.finished_at ?? attempt.started_at).toLocaleString()}</p>}
              {task.progress.active.map((run, i) => <p key={i}>{run.agent} · {run.branch} · {run.worktree}</p>)}
              <button onClick={() => void navigator.clipboard.writeText(task.id)}>Copy objective ID</button>
            </section>})}</div>
            <Objectives key={selected.id} projectId={selected.id} />
          </div>}
        </>}
      </aside>
      {!showTaskboard && showCommand && showView && divider('command')}
      <section className="wb-editor wb-main-view" id="preview-panel" aria-label="Preview workspace" hidden={showTaskboard || !showView}>
        <div className="wb-view-header">
          <nav className="wb-workspace-tabs" aria-label="Application workspace views">
            {workspaceTabs.map(([view,Icon]) => <button key={view} aria-pressed={prefs.view === view} onClick={()=>toggleView(view)}>
              <Icon aria-hidden />{view}
            </button>)}
          </nav>
          <button className="wb-expand-preview" title={expandedView === 'preview' ? 'Restore layout' : 'Expand preview'}
            onClick={()=>setExpandedView(value=>value==='preview'?null:'preview')}>
            {expandedView === 'preview' ? <Minimize2 aria-hidden /> : <Maximize2 aria-hidden />}
            <span>{expandedView === 'preview' ? 'Restore layout' : 'Expand'}</span>
          </button>
        </div>
          {prefs.view === 'Preview' && selected && <div className="wb-preview">
            <div className="wb-preview-toolbar" role="group" aria-label="Preview browser controls">
              <div className="wb-preview-environment">
                <span className="wb-preview-indicator" data-live={previewEnvironment==='DEV' && published?.ready} aria-hidden />
                <select aria-label="Preview environment" title="Preview environment" value={previewEnvironment}
                  onChange={event=>{setPreviewAction('');setPreviewEnvironment(event.target.value as Environment);}}>
                  {environments.map(environment=><option key={environment}>{environment}</option>)}
                </select>
              </div>
              {automatic && <div className="wb-preview-source">{selectSource()}</div>}
              <div className="wb-preview-navigation">
                <button aria-label="Refresh preview" title="Refresh preview" disabled={!frameUrl}
                  onClick={()=>setPreviewRevision(value=>value+1)}><RotateCw aria-hidden /></button>
              </div>
              <input className="wb-address" aria-label="Preview URL" title={frameUrl || 'Preview URL unavailable'}
                value={frameUrl ? frameUrl.split(/[?#]/,1)[0] : ''} placeholder="Preview URL unavailable"  readOnly />
              {frameUrl ? <a className="wb-preview-external" href={frameUrl} target="_blank" rel="noopener noreferrer"
                aria-label="Open preview in new tab" title="Open preview in new tab"><ExternalLink aria-hidden /></a>
                : <button disabled aria-label="Open preview in new tab"><ExternalLink aria-hidden /></button>}
              <details ref={previewMenu} className="wb-preview-more"
                onBlur={event=>{if (!event.currentTarget.contains(event.relatedTarget)) closePreviewMenu();}}
                onKeyDown={event=>{if (event.key==='Escape') {closePreviewMenu();event.currentTarget.querySelector('summary')?.focus();}}}>
                <summary aria-label="More preview options" title="More preview options"><Ellipsis aria-hidden /></summary>
                <div className="wb-preview-options">
                  {automatic && <label className="wb-preview-source-option">Preview source{selectSource()}</label>}
                  <label>Viewport<select value={viewport} onChange={event=>setViewport(event.target.value)}>
                    <option value="100%">Desktop</option><option value="768px">Tablet</option><option value="390px">Mobile</option>
                  </select></label>
                  <button disabled={!frameUrl} onClick={()=>{if(frameUrl) void navigator.clipboard.writeText(frameUrl)
                    .then(()=>setPreviewAction('Preview URL copied.'),()=>setPreviewAction('Could not copy the Preview URL.'));}}>
                    Copy preview URL
                  </button>
                  <button onClick={()=>toggleView('Logs')}>Application logs</button>
                  {automatic && <button disabled={!frameUrl || restartingPreview} onClick={()=>void restartPreview()}>Restart preview</button>}
                  <p role="status">{previewAction}</p>
                </div>
              </details>
            </div>
            {frameUrl ? <>
              <iframe title={configuredUrl?'Application Preview':'DEV application preview'} className="project-preview"
                key={selected.id+':'+previewEnvironment+':'+frameUrl+':'+previewRevision} src={frameUrl}
                style={{width:viewport}} sandbox={configuredUrl && !registeredProductPreview ? "allow-scripts allow-forms" : "allow-scripts allow-same-origin allow-forms"}
                allow="camera 'none'; microphone 'none'; geolocation 'none'; payment 'none'"
                referrerPolicy="strict-origin-when-cross-origin" />
              {configuredUrl && <small>If the application does not appear in the frame, open it in a new tab.</small>}
            </> : <div className="wb-empty">
              <h3>{!previewSettings ? 'Loading Preview configuration' : previewSettings.error ? 'Preview configuration unavailable'
                  : automatic ? (development.status==='stopped'?'DEV is stopped':'Connecting to DEV preview')
                  : 'No Preview URL configured'}</h3>
              {previewSettings && <button onClick={()=>toggleView('Project settings')}>Configure application preview</button>}
              {automatic && development.status==='stopped' && <button disabled={working} onClick={()=>void lifecycle('start')}>Start DEV</button>}
              {automatic && coderConnection==='failed' && <button onClick={()=>void connectCoder()}>Reconnect Coder</button>}
            </div>}
          </div>}
          {prefs.view === 'Project context' && selected && <div className="wb-content">
            {workspace && development.chat_ready && coderConnection==='connected'
              ? <iframe key={selected.id} title={selected.name + ' project context'}
                  src={workspace.replace(/\/$/, '') + '/apps/codex-chat/project-context.html'} />
              : <p>Project context is available when the workspace is running.</p>}
          </div>}
          {prefs.view === 'Publishing' && selected && <div className="wb-content">
            <h2>Publishing</h2>
            {!codingOnly && <SmdDeployment key={'smd-'+selected.id} projectId={selected.id} projectName={selected.name} />}
            <DevPublishing key={'publishing-'+selected.id} projectId={selected.id} openPreview={()=>toggleView('Preview')} openLogs={()=>toggleView('Logs')} />
          </div>}
          {prefs.view === 'Domains' && selected && <ProjectDomains key={selected.id} projectId={selected.id} />}
          {prefs.view === 'Project settings' && selected && <div className="wb-content">
            <h2>Project settings</h2>
            {previewSettings && !previewSettings.error && <ApplicationPreviewSettings key={selected.id} projectId={selected.id}
              settings={previewSettings.settings} onSave={(environment,entry)=>setPreviewConfig(current=>({
                project:selected.id,settings:{...(current?.project===selected.id ? current.settings : {}),[environment]:entry}
              }))} />}

            <Runtime key={selected.id} projectId={selected.id} />
            <Secrets key={'secrets-'+selected.id} projectId={selected.id} />
            <section className="panel"><h3>Git</h3>
              <p>{selected.repository || 'Local repository'}</p>
              {editor && <button disabled={!development.editor_ready} onClick={openEditor}>Open workspace source control</button>}
            </section>
            <section className="panel"><h3>Agent governance</h3>
              <p>{active.length} active assignments</p>
              <button onClick={() => setShowTaskDetails(true)}>Review objectives and evidence</button>
            </section>
          </div>}
          {prefs.view === 'Logs' && selected && <div className="wb-content">
            <h2>Application logs</h2>
            {previewRoot && development.preview_ready && coderConnection==='connected' &&
              <iframe title="DEV application logs" src={previewRoot+'.__smartdev/logs'} sandbox="allow-scripts allow-same-origin allow-forms" />}
            {previewConsole.length>0 && <details><summary>Browser console</summary><pre>{previewConsole.join('\n')}</pre></details>}
            <h2>Execution history</h2><Memory key={selected.id} projectId={selected.id} />
          </div>}
        {prefs.view === 'Git' && selected && (gitReview?.project === selected.id
          ? <section className="wb-git">
              <h2>Git review</h2>
              <p><code>{gitReview.review.branch}</code></p>
              <p>Base <code>{gitReview.review.base.slice(0,12)}</code> · HEAD <code>{gitReview.review.head.slice(0,12)}</code></p>
              {gitReview.review.truncated && <p>Diff shortened. Use the task worktree in the full IDE for the rest.</p>}
              <pre aria-label="Git diff">{gitReview.review.diff || 'No changes against this base.'}</pre>
            </section>
          : <div className="wb-empty"><h3>Review task changes</h3><p>Select a conversation and click Review changes to load its worktree diff here.</p></div>)}
      </section>
      {!showTaskboard && showFiles && showView && divider('right')}
      <aside className="wb-editor wb-files" id="editor-panel" hidden={showTaskboard || !showFiles}>
        <div className="wb-panel-header">
          <h2>Files &amp; folders</h2>
          <div className="wb-actions">
            <button aria-label={expandedView === 'files' ? 'Restore files layout' : 'Expand editor'}
              onClick={()=>setExpandedView(value=>value==='files'?null:'files')}>
              {expandedView === 'files' ? <Minimize2 aria-hidden /> : <Maximize2 aria-hidden />}
            </button>
            <button aria-label="Collapse editor panel" onClick={toggleFiles}><PanelRightClose aria-hidden /></button>
          </div>
        </div>
        {showFiles && selected && <nav className="wb-tabs" aria-label="Workspace files and history">
          <button aria-pressed={fileView==='files'} onClick={()=>setFileView('files')}>Files</button>
          <button aria-pressed={fileView==='history'} onClick={()=>setFileView('history')}>History</button>
        </nav>}
        {showFiles && selected && fileView==='history' && (workspace && development.chat_ready && coderConnection === 'connected'
          ? <iframe key={selected.id} title={selected.name + ' history'}
              src={workspace.replace(/\/$/, '') + '/apps/codex-chat/?history=1'} />
          : <p>History is available when the workspace is running.</p>)}
        {showFiles && selected && fileView==='files' && <>
          <div className="wb-editor-actions">
            {editor && <a href={editor} target="_blank" rel="noreferrer">Full IDE</a>}
            <span>Search and Git changes are in the IDE sidebar.</span>
          </div>
          {workspace && development.chat_ready && coderConnection === 'connected' && <details>
            <summary>Uploads</summary>
            <iframe key={selected.id} title={selected.name + ' uploads'}
              src={workspace.replace(/\/$/, '') + '/apps/codex-chat/?uploads=1'} style={{width:'100%',height:220,border:0}} />
          </details>}
          {editor && !opened.includes(selected.id) && <button disabled={!development.editor_ready || coderConnection !== 'connected'} onClick={openEditor}>Load embedded IDE</button>}
          {(!editor || !development.editor_ready || coderConnection !== 'connected') && <div className="wb-empty"><h3>Editor unavailable</h3><p>Editor opens when DEV and Coder are ready.</p></div>}
        </>}
        {available.filter(project => opened.includes(project.id) && project.workspace_url).map(project =>
          <iframe key={project.id} hidden={fileView!=='files' || coderConnection !== 'connected' || project.id !== selected?.id} title={project.name + ' IDE'}
            src={project.workspace_url!.replace(/\/$/, '') + '/apps/editor/'} allow="clipboard-read; clipboard-write" />)}
      </aside>
    </div>}

  </div>;
}
