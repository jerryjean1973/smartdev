'use client';

import {useEffect, useRef, useState} from 'react';
import {RequestInbox, RequestDetails, receiptLine, useMcpRequests, type McpRequest} from './mcp-requests';
import {TaskTable,shortTitle,type TaskLine} from './task-table';
import TaskActivity, {ThinkingIcon} from './task-activity';
import {CircleCheck, CircleDot, CircleHelp, CircleX, OctagonAlert, Trash2} from 'lucide-react';

type Project = {id: string; name: string; workspace_url: string | null};
type Navigation = {project: string; running: string[]; waiting: string[]; queued: string[]; paused: boolean};
type SidebarTask = {id: string; status: string; title: string | null};
type Objective = {
  id: string;
  task_number?:string; tenant_name?:string; updated_at?:string;
  display_name: string | null;
  expected_outcome: string;
  original_request: string;
  business_objective?: string | null;
  created_at: string;
  version: number;
  status: string;
  criteria: {id: string; description: string}[];
  progress: {
    milestones?: {position: number; title: string; detail: string;
      status: 'done' | 'working' | 'blocked' | 'pending'; updated_at: string;
      progress_completed?: number | null; progress_total?: number | null;
      checks?: {title: string; detail: string; status: 'done' | 'working' | 'blocked' | 'pending'}[]}[];
    completedCriteria: string[];
    blockedCriteria: string[];
    blockers: string[];
    verifiedEvidence?: string[];
    verifiedScreenshots?: string[];
    nextAction: string | null;
    executionSteps?: {id: string; criterion_id: string; agent: string; started_at: string;
      finished_at: string | null; last_contact: string; changed_files: number | null; exit_code: number | null}[];
    active: unknown[];
    attempts: {id: string; started_at: string; finished_at: string | null; exit_code: number | null}[];
  };
};

function latestRun(task: Objective) {
  return task.progress.attempts.reduce<Objective['progress']['attempts'][number] | undefined>(
    (left, right) => !left || right.started_at > left.started_at ||
      (right.started_at === left.started_at && right.id > left.id) ? right : left, undefined);
}

function statusOf(task: Objective, sidebar: SidebarTask | undefined, live?: Navigation) {
  if (live?.waiting.includes(task.id)) return 'Awaiting input';
  if (task.status === 'blocked' || task.progress.blockedCriteria.length) return 'Blocked';
  if (live?.running.includes(task.id)) return 'Running';
  if (live?.queued.includes(task.id)) return live.paused ? 'Paused' : 'Queued';
  if (task.status === 'done') return 'Completed';
  if (task.progress.active.length && (!sidebar || sidebar.status === 'Running')) return 'Running';
  if (task.status === 'ready_for_review') return task.progress.verifiedScreenshots?.length ? approvalStatus : 'Evidence needed';
  if (sidebar && !['Active', 'Ready for review', 'Completed'].includes(sidebar.status)) return sidebar.status;
  const latest = latestRun(task);
  if (latest?.finished_at && latest.exit_code === 0) return 'Evidence needed';
  if (latest?.exit_code === 130) return 'Paused';
  if (latest?.exit_code) return 'Failed';
  return sidebar?.status === 'Queued' || !latest ? 'Planned' : 'Active';
}

const filters = ['All tasks','Requested', 'Pending', 'Queued', 'Processing', 'Processed', 'Done', 'Running', 'Blocked', 'Awaiting approval', 'Completed'] as const;
type Filter = typeof filters[number];
const otherTasks = 'other-tasks';
const approvalStatus = 'Complete · awaiting approval';
const sections = ['Incomplete', 'Awaiting approval', 'Completed'] as const;
const stages = ['Requested', 'Pending', 'Queued', 'Processing', 'Reviewed', 'Processed', 'Tested', 'Requeued', 'Completed', 'Done'] as const;
type Grouping = 'Business objective' | 'Story' | 'None';
type BusinessObjective = {id: string; title: string; expected_outcome: string; priority: string; current_focus: boolean};
type CcActivity = 'queued' | 'running';
type Row = {project: Project; task: Objective; title: string; status: string; running: boolean; ccActivity?: CcActivity; request?:McpRequest};

const outcomes=['All outcomes','Running','Succeeded','Failed','Cancelled','Unknown'] as const;
type Outcome=typeof outcomes[number];
function outcome(row:Pick<Row,'running'|'request'|'task'>):Outcome {
  const code=row.request?row.request.execution_code:latestRun(row.task)?.exit_code;
  if(row.running) return 'Running';
  if(code===130) return 'Cancelled';
  if(code===0) return 'Succeeded';
  return code==null?'Unknown':'Failed';
}
function queryValue(key:string){
  return typeof window==='undefined'?'':new URLSearchParams(window.location.search).get(key) || '';
}
function choice<T extends string>(key:string,options:readonly T[],fallback:T):T {
  return options.find(value=>value===queryValue(key)) || fallback;
}

function objectiveStatus(tasks: Row[], unavailable: boolean) {
  if (unavailable) return {label: 'Status unavailable', tone: 'unknown', Icon: CircleHelp};
  if (tasks.length && tasks.every(row => row.status === 'Completed' || row.status === 'Done')) {
    return {label: 'Complete', tone: 'green', Icon: CircleCheck};
  }
  if (tasks.some(row => ['Blocked', 'Awaiting input', 'Failed'].includes(row.status) || !!row.request?.waiting_for_input || (row.request?.execution_code != null && row.request.execution_code!==0))) {
    return {label: 'Blocked', tone: 'red', Icon: CircleX};
  }
  if (tasks.some(row => ['Running','Processing','Processed','Done','Completed'].includes(row.status) ||
      row.status === approvalStatus || row.task.progress.attempts.length > 0 ||
      row.task.progress.completedCriteria.length > 0)) {
    return {label: 'In progress', tone: 'yellow', Icon: CircleDot};
  }
  return {label: 'Not started', tone: 'red', Icon: CircleX};
}

function sectionOf(status: string) {
  return status === 'Completed' || status==='Done' ? 'Completed' : status === approvalStatus ? 'Awaiting approval' : 'Incomplete';
}

function kanbanStage({task, status, request}: Row): typeof stages[number] {
  if (request) return request.work_status;
  if (status === 'Completed') return 'Completed';
  if (task.status === 'ready_for_review') return 'Tested';
  const run = latestRun(task);
  if (status === 'Queued' && run?.finished_at) return 'Requeued';
  if (status === 'Queued' || status === 'Running') return 'Queued';
  if (task.progress.completedCriteria.length) return 'Reviewed';
  return run?.finished_at ? 'Processed' : 'Requested';
}

function uniqueReference(text: string, expression: RegExp) {
  const matches = new Set(Array.from(text.matchAll(expression), match => match[0].toUpperCase()));
  return matches.size === 1 ? [...matches][0] : undefined;
}

export default function Taskboard({projects, navigation, sidebarTasks, parents, onAskCc, onReply}: {
  projects: Project[];
  navigation?: Navigation;
  sidebarTasks: SidebarTask[];
  parents: Record<string, string>;
  onAskCc?: (taskId: string, title: string) => void;
  onReply: (projectId: string, taskId: string) => void;
}) {
  const lanes=useRef<HTMLDivElement>(null);
  const projectIds = projects.map(project => project.id).sort().join(',');
  const [objectives, setObjectives] = useState<Record<string, Objective[]>>({});
  const [ccActivity, setCcActivity] = useState<Record<string, CcActivity>>({});
  const [hostTasks, setHostTasks] = useState<string[]>([]);
  const [unavailable, setUnavailable] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [projectFilter, setProjectFilter] = useState(()=>typeof window==='undefined'?'':new URLSearchParams(window.location.search).get('project') || '');
  const [filter, setFilter] = useState<Filter>(()=>choice('lifecycle',filters,'All tasks'));
  const [open, setOpen] = useState<string | null>(null);
  const [workOpen, setWorkOpen] = useState<Record<string, boolean>>({});
  const [openTask, setOpenTask] = useState<string | null>(null);
  const [businessObjectives, setBusinessObjectives] = useState<BusinessObjective[]>([]);
  const [businessFilter, setBusinessFilter] = useState(()=>queryValue('bo'));
  const [execution,setExecution]=useState<Outcome>(()=>choice('outcome',outcomes,'All outcomes'));
  const [view, setView] = useState<'list' | 'kanban' | 'mcp'>(()=>choice('boardView',['list','kanban','mcp'] as const,'list'));
  const [source,setSource] = useState<'all'|'mcp'|'ui'>(()=>choice('source',['all','mcp','ui'] as const,'all'));
  useEffect(()=>{
    const url=new URL(window.location.href);
    for(const [key,value] of Object.entries({bo:businessFilter,source:source==='all'?'':source,lifecycle:filter==='All tasks'?'':filter,
      outcome:execution==='All outcomes'?'':execution,boardView:view==='list'?'':view,project:projectFilter})){
      if(value) url.searchParams.set(key,value);else url.searchParams.delete(key);
    }
    window.history.replaceState(window.history.state,'',url);
  },[businessFilter,source,filter,execution,view,projectFilter]);
  const mcp = useMcpRequests(projectFilter);
  const [receipt,setReceipt] = useState('');
  useEffect(()=>{
    const params=new URLSearchParams(window.location.search);
    if(params.has('receipt')) {
      setView('mcp');
      setReceipt(params.get('receipt') || '');
    }
  },[]);
  const [catalogUnavailable, setCatalogUnavailable] = useState(false);
  const [grouping, setGrouping] = useState<Grouping>('Business objective');
  const [dateOrder, setDateOrder] = useState<'newest' | 'oldest'>('newest');
  const [changing, setChanging] = useState(false);
  const [notice, setNotice] = useState('');
  const [actionError, setActionError] = useState('');
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let refreshing = false;
    async function refresh() {
      if (refreshing || controller.signal.aborted) return;
      refreshing = true;
      clearTimeout(timer);
      const ids = projectIds.split(',').filter(id=>id && (!projectFilter || id===projectFilter));
      const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(4500)]);
      const askCc = !onAskCc ? Promise.resolve(null) : fetch('/api/cc-codex/activity', {cache: 'no-store', signal})
        .then(async response => {
          if (!response.ok) return null;
          return await response.json() as {tasks: {task_id: string; status: CcActivity}[]; host_tasks?: string[]};
        }).catch(() => null);
      const results = await Promise.allSettled(ids.map(async id => {
        const response = await fetch('/api/projects/' + id + '/objectives?board=true'+(source==='mcp'?'&source=mcp':''), {
          cache: 'no-store', signal,
        });
        if (!response.ok) throw Error();
        return await response.json() as Objective[];
      }));
      const cc = await askCc;
      if (controller.signal.aborted) return;
      setCcActivity(Object.fromEntries((cc?.tasks ?? []).map(task => [task.task_id, task.status])));
      setHostTasks(cc?.host_tasks ?? []);
      setObjectives(previous => {
        const next = {...previous};
        results.forEach((result, index) => {
          if (result.status === 'fulfilled') next[ids[index]] = result.value;
        });
        return next;
      });
      setUnavailable(ids.filter((_, index) => results[index].status === 'rejected'));
      setLoading(false);
      refreshing = false;
      timer = setTimeout(refresh, 3000);
    }
    const followVisibleTab = () => {
      if (document.visibilityState === 'visible') void refresh();
    };
    window.addEventListener('focus', followVisibleTab);
    document.addEventListener('visibilitychange', followVisibleTab);
    void refresh();
    return () => {
      controller.abort();
      clearTimeout(timer);
      window.removeEventListener('focus', followVisibleTab);
      document.removeEventListener('visibilitychange', followVisibleTab);
    };
  }, [projectIds, projectFilter, source, refreshKey]);

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const response = await fetch('/api/taskboard/business-objectives', {
          cache: 'no-store', signal: controller.signal,
        });
        if (!response.ok) throw Error();
        const result: BusinessObjective[] = await response.json();
        if (!controller.signal.aborted) setBusinessObjectives(result);
      } catch {
        if (!controller.signal.aborted) setCatalogUnavailable(true);
      }
    }
    void load();
    return () => controller.abort();
  }, []);

  function businessId({task, title, request}: Row) {
    if (request) return request.scope_kind==='global' ? 'global' : request.business_objective || undefined;
    const explicit = /^\s*(BO-\d+)\b/i.exec(task.business_objective ?? '')?.[1].toUpperCase();
    const referenced = explicit || uniqueReference(title, /\bBO-\d+\b/gi) ||
      uniqueReference(task.original_request, /\bBO-\d+\b/gi);
    return businessObjectives.some(objective => objective.id === referenced) ? referenced : undefined;
  }

  const selectedObjective = businessFilter;
  const sidebar = new Map(sidebarTasks.map(task => [task.id, task]));
  const objectiveRows:Row[] = projects.flatMap(project => {
    const live = navigation?.project === project.id ? navigation : undefined;
    return (objectives[project.id] ?? []).map(task => ({
      project, task, title: task.display_name || sidebar.get(task.id)?.title || task.expected_outcome,
      status: statusOf(task, sidebar.get(task.id), live),
      ccActivity: ccActivity[task.id],
      running: hostTasks.includes(task.id) || ccActivity[task.id] === 'running' || Boolean(live?.running.includes(task.id)) ||
        (!unavailable.includes(project.id) && (task.progress.active.length > 0 ||
          !!task.progress.milestones?.some(step => step.status === 'working'))),
    }));
  });
  const projectMap=new Map(projects.map(project=>[project.id,project]));
  const objectiveMap=new Map(objectiveRows.map(row=>[row.task.id,row]));
  const replaced=new Set<string>();
  const requestRows:Row[]=[];
  const covered=new Set<string>();
  const sourceRequests=new Map(mcp.requests.map(request=>[request.request_id,request]));
  const coveredRequests=new Set<string>();
  for (const request of mcp.requests) {
    if(request.admission==='duplicate') continue;
    const original=request.matched_request?sourceRequests.get(request.matched_request):undefined;
    const group=original?.request_id || request.request_id;
    if(coveredRequests.has(group)) continue;
    coveredRequests.add(group);
    if((request.kind==='create' || original?.kind==='create') && request.task_id){
      if(covered.has(request.task_id)) continue;
      covered.add(request.task_id);
    }
    const project=projectMap.get(request.project_id);
    if (!project) continue;
    const existing=(request.kind==='create' || original?.kind==='create') && request.task_id ? objectiveMap.get(request.task_id) : undefined;
    if (existing) {
      replaced.add(existing.task.id);
      requestRows.push({...existing,request, running:existing.running || request.work_status==='Processing'});
      continue;
    }
    const task:Objective={id:request.request_id,display_name:request.label,expected_outcome:request.label,
      original_request:request.label,business_objective:request.business_objective,created_at:request.received_at || '',
      status:request.work_status,version:0,criteria:[],progress:{completedCriteria:[],blockedCriteria:[],blockers:[],nextAction:null,active:[],attempts:[]}};
    requestRows.push({project,task,request,title:original?.label || request.label,status:request.work_status,running:request.work_status==='Processing'});
  }
  const projectRows=[...objectiveRows.filter(row=>!replaced.has(row.task.id)),...requestRows]
    .filter(row=>!projectFilter || row.project.id===projectFilter);
  const allRows=projectRows.filter(row=>source==='all' || (source==='mcp'?Boolean(row.request):!row.request));
  function requestGroupLabel(r:McpRequest) {
    if(r.scope_kind==='global') return 'Global platform';
    const bo=businessObjectives.find(item=>item.id===r.business_objective);
    return bo ? bo.id+' — '+bo.title : r.business_objective || 'Other tasks / bugs';
  }
  const boardRequests=mcp.requests.filter(r=>(!projectFilter || r.project_id===projectFilter) &&
    (!businessFilter || (r.scope_kind==='global'?'global':r.business_objective||otherTasks)===businessFilter));
  function isOtherTask(row: Row) {
    if (row.request) return row.request.scope_kind==='unassigned';
    return !businessId(row) || row.task.status === 'blocked' || /\b(?:bug|fix|repair|remediation)\b/i.test(row.title);
  }

  const rows = allRows
    .filter(row => (!projectFilter || row.project.id === projectFilter) &&
      (!selectedObjective || (selectedObjective === otherTasks ? isOtherTask(row) : businessId(row) === selectedObjective)))
    .sort((a, b) => (dateOrder === 'newest' ? b.task.created_at.localeCompare(a.task.created_at) :
      a.task.created_at.localeCompare(b.task.created_at)) || a.title.localeCompare(b.title));
  const isBlocked = (status: string) => status === 'Blocked' || status === 'Awaiting input';
  function matches(filter: Filter, {status, running, request}: Row) {
    if (filter === 'All tasks') return true;
    if (filter==='Requested') return status==='Planned' || status==='Requested';
    if (request && filter===request.work_status) return true;
    if (filter==='Completed' && request?.work_status==='Done') return true;
    if (filter === 'Running') return running;
    if (filter === 'Blocked') return isBlocked(status) || Boolean(request?.execution_code);
    if (filter === 'Awaiting approval') return status === 'Complete · awaiting approval';
    return status === filter;
  }
  const inboxRows:Row[]=source==='ui'?[]:boardRequests.flatMap(request=>{
    const project=projectMap.get(request.project_id);
    if(!project) return [];
    const task=objectiveMap.get(request.task_id || '')?.task || {id:request.request_id} as Objective;
    return [{project,task,title:request.label,status:request.work_status,running:request.work_status==='Processing',request}];
  });
  const candidates=view==='mcp'?inboxRows:rows;
  const outcomeRows=candidates.filter(row=>execution==='All outcomes'||outcome(row)===execution);
  const visible=outcomeRows.filter(row=>matches(filter,row));
  const approvable = visible.filter(({status}) => status === approvalStatus);
  const canDelete = ({running, task, request}: Row) => (!request || (request.kind==='create' && request.task_id===task.id)) && !running && task.progress.active.length === 0;
  const deletable = visible.filter(canDelete);
  const titles = new Map(sidebarTasks.map(task => [task.id, task.title]));

  function groupName(row: Row) {
    const {project, task, title} = row;
    if (grouping === 'Business objective') {
      if (row.request) return requestGroupLabel(row.request);
      const id = businessId(row);
      const objective = businessObjectives.find(item => item.id === id);
      return objective ? objective.id + ' — ' + objective.title : id || 'Other tasks / bugs';
    }
    if (grouping === 'Story') {
      const parent = parents[project.id + ':' + task.id];
      return (parent && (titles.get(parent) || 'Parent task ' + parent)) ||
        uniqueReference(title, /\b(?:STORY|US)-\d+\b/gi) ||
        uniqueReference(task.original_request, /\b(?:STORY|US)-\d+\b/gi) || 'No story assigned';
    }
    return '';
  }

  function groups(items: Row[]) {
    const result = new Map<string, Row[]>();
    for (const item of items) {
      const label = groupName(item);
      const group = result.get(label) ?? [];
      group.push(item);
      result.set(label, group);
    }
    return [...result];
  }

  async function change(action: 'approve' | 'delete', items: Row[]) {
    if (!items.length || changing) return;
    const label = items.length === 1 ? items[0].title : items.length + ' tasks in this view';
    const warning = action === 'delete' ? ' Task history will be retained.' :
      ' Approve only outcomes you have reviewed.';
    if (!window.confirm((action === 'delete' ? 'Delete ' : 'Approve ') + label + '?' + warning)) return;
    setChanging(true);
    setNotice('');
    setActionError('');
    try {
      const response = await fetch('/api/taskboard/actions', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({action, tasks: items.map(({project, task}) => ({
          project_id: project.id, task_id: task.id, version: task.version, run_id: latestRun(task)?.id ?? null,
        }))}),
      });
      const result = await response.json();
      if (!response.ok) throw Error(typeof result.detail === 'string' ? result.detail : 'Taskboard change failed.');
      setNotice((action === 'delete' ? 'Deleted ' : 'Approved ') + items.length + (items.length === 1 ? ' task.' : ' tasks.'));
    } catch (error) {
      setActionError(error instanceof Error ? error.message : 'Taskboard change failed.');
    } finally {
      setRefreshKey(key => key + 1);
      setChanging(false);
    }
  }


  function renderTask({project, task, title, status, running, ccActivity,request}: Row) {
    if (request && !(request.kind==='create' && request.task_id===task.id)) {
      const detail = request.waiting_for_input ? 'Awaiting input' : request.execution_code != null && request.execution_code!==0 ? 'Execution failed' : '';
      return <li className="wb-taskboard-row" key={project.id+':'+request.request_id} data-request-id={request.request_id}>
        <div className="wb-taskboard-item"><div><h2><button className="wb-taskboard-title" type="button"
          aria-expanded={openTask===task.id} onClick={()=>setOpenTask(openTask===task.id?null:task.id)}>{request?.task_number && <small>{request.task_number} </small>}{shortTitle(title)}</button></h2>
          <span className="wb-taskboard-meta">{project.name} · {request.work_status}{detail && ' · '+detail}</span>
          {request.received_at && <div className="wb-taskboard-meta">Created {new Date(request.received_at).toLocaleString('en-CA',{timeZone:'America/Toronto'})} Toronto</div>}
          {running && <ThinkingIcon/>}</div>
          {running && <progress aria-label={title+' processing'} />}
          <div className="wb-taskboard-actions">
            {request.task_id && onAskCc && <button type="button" onClick={()=>onAskCc(request.task_id!,title+' · MCP request '+(request.receipt || request.request_id))}>Ask CC</button>}
            {request.task_id && <button onClick={()=>onReply(project.id,request.task_id!)}>Reply to coding agent</button>}
            {request.work_status==='Processed' && request.execution_code===0 && !request.waiting_for_input &&
              <button onClick={async()=>{const response=await fetch('/api/projects/'+project.id+'/mcp-tasks/'+request.request_id+'/complete',{method:'POST'});if(!response.ok)setActionError('Could not complete this request.');else setNotice('Request marked done.');}}>Mark done</button>}
          </div></div>
        {request.task_id && request.receipt && <TaskActivity projectId={project.id} taskId={request.task_id}
          requestId={request.request_id} running={running} queued={request.work_status==='Queued'}
          expanded={!!workOpen[task.id]} onExpand={next=>setWorkOpen(old=>old[task.id]===next?old:{...old,[task.id]:next})}/>}
        {openTask===task.id && <RequestDetails item={request} expanded card/>}
      </li>;
    }
    const finished = new Set(task.progress.completedCriteria);
    const total = task.criteria.length;
    const verified = task.criteria.filter(criterion => finished.has(criterion.id)).length;
    const awaitingApproval = status === approvalStatus;
    const progressAvailable = !unavailable.includes(project.id);
    const milestones = task.progress.milestones ?? [];
    const milestonesDone = milestones.filter(step => step.status === 'done').length;
    const showMilestones = milestones.length > 0 && status !== 'Completed' && !awaitingApproval;
    const milestoneLabels = {done: 'Done', working: 'Processing', blocked: 'Blocked', pending: ''};
    const milestoneUnits = milestones.reduce((sum, step) => {
      if (step.status === 'done') return sum + 1;
      if (!step.progress_total || step.progress_completed == null) return sum;
      return sum + Math.max(0, Math.min(1, step.progress_completed / step.progress_total));
    }, 0);
    const percent = status === 'Completed' || request?.work_status==='Done' || awaitingApproval ? 100
      : showMilestones ? Math.min(milestonesDone === milestones.length ? 100 : 99, Math.floor(100 * milestoneUnits / milestones.length))
      : total ? Math.floor(100 * verified / total) : null;
    const blocked = isBlocked(status);
    const expanded = !!workOpen[task.id];
    const setExpanded = (next: boolean) => setWorkOpen(old => !!old[task.id] === next ? old : {...old, [task.id]: next});
    function milestoneStatus(state: keyof typeof milestoneLabels) {
      if (!milestoneLabels[state]) return null;
      if (state !== 'working') return <span className="wb-milestone-status">{milestoneLabels[state]}</span>;
      return <button type="button" className="wb-milestone-work" aria-expanded={expanded}
        aria-controls={'work-' + task.id} onClick={event => {
          event.preventDefault(); event.stopPropagation(); setExpanded(!expanded);
          if (!expanded) document.getElementById('work-' + task.id)?.querySelector('summary')?.focus();
        }}><ThinkingIcon /> Processing</button>;
    }
    const blockerId = 'taskboard-blocker-' + task.id;
    const detailsId = 'taskboard-details-' + task.id;
    const attempt = latestRun(task);
    const evidence = task.progress.verifiedEvidence ?? [];
    const screenshots = task.progress.verifiedScreenshots ?? [];
    return <li key={task.id} className="wb-taskboard-row" data-request-id={request?.request_id}>
      <div className="wb-taskboard-item">
        <div>
          <h2><button type="button" className="wb-taskboard-title" aria-expanded={openTask === task.id}
            aria-controls={detailsId} onClick={() => setOpenTask(current => current === task.id ? null : task.id)}>
            {title}
          </button></h2>
          <span className="wb-taskboard-meta">{project.name} · {status}</span>
          <div className="wb-taskboard-meta">Created <time dateTime={task.created_at}>
            {new Date(task.created_at).toLocaleString(undefined, {year:'numeric',month:'short',day:'numeric',hour:'numeric',minute:'2-digit',second:'2-digit',timeZone:'America/Toronto',timeZoneName:'short'})}
          </time></div>
        </div>
        <div className="wb-taskboard-progress">
          {!progressAvailable ? <span role="status">Progress unavailable · retrying</span> : percent === null ? running ? <progress aria-label={title + ' processing'} /> : <span>No acceptance criteria</span> : <>
            <progress max={100} value={percent} aria-label={title + (showMilestones ? ' deployment preparation progress' : awaitingApproval ? ' execution progress' : ' completion progress')} />
            <strong>{percent}%</strong>
          </>}
          {progressAvailable && (awaitingApproval ? <small>Awaiting owner approval</small>
            : showMilestones ? <small>{milestonesDone} of {milestones.length} rollout milestones done · {verified} of {total} live checks verified</small>
              : total > 0 && <small>{verified} of {total} criteria verified</small>)}
        </div>
        <div className="wb-taskboard-actions">
          {blocked && <button type="button" aria-expanded={open === task.id} aria-controls={blockerId}
            onClick={() => setOpen(current => current === task.id ? null : task.id)}>
            <OctagonAlert size={16} aria-hidden="true" /> Why blocked
          </button>}
          {onAskCc && <button type="button" aria-label={'Ask CC about ' + title} onClick={() => onAskCc(task.id, title)}>
            Ask CC
          </button>}
          <button type="button" disabled={!project.workspace_url} onClick={() => onReply(project.id, task.id)}>
            Reply to coding agent
          </button>
          <button type="button" aria-label={'Approve ' + title} title="Approve task"
            disabled={changing || status !== approvalStatus}
            onClick={() => void change('approve', [{project, task, title, status, running}])}>
            <CircleCheck size={16} aria-hidden="true" />
          </button>
          <button type="button" aria-label={'Delete ' + title} title="Delete task"
            disabled={changing || !canDelete({project, task, title, status, running})}
            onClick={() => void change('delete', [{project, task, title, status, running}])}>
            <Trash2 size={16} aria-hidden="true" />
          </button>
        </div>
      </div>
      {request && <div className="wb-taskboard-meta">MCP task · {request.work_status}</div>}
      <TaskActivity projectId={project.id} taskId={task.id} running={running} queued={ccActivity === 'queued'} expanded={expanded} onExpand={setExpanded} />
      {milestones.length > 0 && progressAvailable && <details key={String(showMilestones)} className="wb-taskboard-milestones" open={showMilestones}>
        <summary>Deployment checklist</summary>
        <ol>{milestones.map(step => {
          const checks = step.checks?.filter(check => check.status !== 'pending') ?? [];
          const heading = <>
            <span className="wb-milestone-icon" aria-hidden="true">
              {step.status === 'done' && <CircleCheck size={17} />}
            </span>
            <span className="wb-milestone-heading">
              <strong><span className="wb-milestone-number">{step.position}. </span>{step.title}</strong>
              {milestoneStatus(step.status)}
            </span>
          </>;
          return <li key={step.position} data-status={step.status}>
            {step.detail || checks.length > 0 ? <details key={step.status} className="wb-milestone-detail" open={showMilestones && (step.status === 'working' || step.status === 'blocked')}>
              <summary className="wb-milestone-row">{heading}</summary>
              {step.detail && <p className="wb-milestone-description">{step.detail}</p>}
              {checks.length > 0 && <ul className="wb-milestone-checks" aria-label={step.title + ' checklist'}>
                {checks.map(check => <li key={check.title} className="wb-milestone-row" data-status={check.status}>
                  <span className="wb-milestone-icon" aria-hidden="true">
                    {check.status === 'done' && <CircleCheck size={17} />}
                  </span>
                  <div className="wb-milestone-content">
                    <div className="wb-milestone-heading">
                      <strong>{check.title}</strong>
                      {milestoneStatus(check.status)}
                    </div>
                    {check.detail && check.status !== 'done' && <p>{check.detail}</p>}
                  </div>
                </li>)}
              </ul>}
            </details> : <div className="wb-milestone-row">{heading}</div>}
            {step.status === 'working' && <div className="wb-milestone-progress wb-milestone-description">
              {step.progress_total && step.progress_completed != null ? <>
                <progress aria-label={step.title + ' progress'} max={step.progress_total} value={step.progress_completed} />
                <strong>{Math.floor(100 * step.progress_completed / step.progress_total)}%</strong>
                <small>{step.progress_completed} of {step.progress_total} checks done</small>
              </> : <progress aria-label={step.title + ' in progress'} />}
            </div>}
          </li>;
        })}</ol>
      </details>}
      {openTask === task.id && <section id={detailsId} className="wb-taskboard-detail" aria-label="Task details">
        <h3>Task details</h3>
        <p>Task ID <code>{task.id}</code></p>
        <h4>Expected outcome</h4>
        <p>{task.expected_outcome}</p>
        {task.original_request !== task.expected_outcome && <details>
          <summary>Original request</summary>
          <p>{task.original_request}</p>
        </details>}
        <h4>Acceptance criteria</h4>
        {total ? <ul>{task.criteria.map(criterion => <li key={criterion.id}>
          {finished.has(criterion.id) ? 'Verified' : task.progress.blockedCriteria.includes(criterion.id) ?
            'Blocked' : 'Outstanding'} · {criterion.description}
        </li>)}</ul> : <p>No acceptance criteria recorded.</p>}
        <h4>Runner activity</h4>
        {!progressAvailable ? <p role="status">Runner activity unavailable · retrying</p> :
          task.progress.executionSteps?.length ? <ol className="wb-task-activity">
            {task.progress.executionSteps.slice(-6).map(run => <li key={run.id}>
              <strong>{run.finished_at ? run.exit_code === 0 ? 'Run finished' : 'Run stopped' : 'Coding run active'}</strong>
              {' · '}{run.agent} · {run.criterion_id === 'implementation' ? 'Implementation' : run.criterion_id}
              {run.changed_files != null && <> · {run.changed_files} changed files reported</>}
              <div>{run.finished_at ? 'Finished' : 'Last runner check-in'}{' '}
                <time dateTime={run.last_contact}>{new Date(run.last_contact).toLocaleTimeString()}</time></div>
            </li>)}
          </ol> : <p>No runner activity recorded for this task.</p>}
        <h4>Latest run</h4>
        {attempt ? <p>{attempt.finished_at ? 'Finished · exit ' + (attempt.exit_code ?? 'unknown') : 'Running'}
          {' · '}<time dateTime={attempt.finished_at ?? attempt.started_at}>
            {new Date(attempt.finished_at ?? attempt.started_at).toLocaleString()}
          </time></p> : <p>No run recorded.</p>}
        <h4>Verified evidence</h4>
        {evidence.length ? <ul>{evidence.map((item, index) => <li key={index}>{item}</li>)}</ul>
          : <p>No verified evidence recorded for this task.</p>}
        <h4>Screenshot proof</h4>
        {screenshots.length ? <div className="wb-taskboard-screenshots">
          {screenshots.map((url, index) => <a key={url} href={url} target="_blank" rel="noreferrer">
            <img src={url} alt={'Verified screenshot ' + (index + 1)} loading="lazy" referrerPolicy="no-referrer" />
          </a>)}
        </div> : <p>No verified screenshots recorded for this task.</p>}
        {task.progress.blockers.length > 0 && <>
          <h4>Blockers</h4>
          <ul>{task.progress.blockers.map((reason, index) => <li key={index}>{reason}</li>)}</ul>
        </>}
        {task.progress.nextAction && <p>Next action · {task.progress.nextAction}</p>}
      </section>}
      {blocked && <div className="wb-taskboard-blocker" id={blockerId} hidden={open !== task.id}>
        {task.progress.blockers.length ? <ul>{task.progress.blockers.map((reason, index) =>
          <li key={index}>{reason}</li>)}</ul> :
          <p>{status === 'Awaiting input' ? 'Open the task conversation to read and answer the pending question.' :
            'No blocker explanation has been recorded for this objective.'}</p>}
        {task.progress.nextAction && <p>{task.progress.nextAction}</p>}
      </div>}
    </li>;
  }

  function tableLine(row:Row):TaskLine {
    const {task,request,project}=row;
    const run=latestRun(task);
    const underlying=request?.task_id || task.id;
    const evidence=task.progress.verifiedScreenshots?.length || 0;
    const line:TaskLine=request?receiptLine(request):{
      key:task.id,number:task.task_number,description:row.title,objective:businessId(row),project:project.name,tenant:task.tenant_name,
      source:'Command Center',execution:row.status,verification:evidence?'Evidence recorded':'Unverified',created:task.created_at,
      updated:task.updated_at || run?.finished_at || run?.started_at || task.created_at,evidence:evidence?evidence+' screenshots':'—',
      active:row.running,details:()=>null};
    return {...line, details:()=><div>{request && <RequestDetails item={request} expanded card/>}<ul className="wb-taskboard-list">{renderTask(row)}</ul></div>,
      actions:<>{onAskCc && <button type="button" onClick={()=>onAskCc(underlying,row.title+(request?' · MCP request '+request.request_id:''))}>Ask CC</button>}
        <button type="button" onClick={()=>onReply(project.id,underlying)}>Reply to coding agent</button></>};
  }

  return <section className="wb-taskboard" aria-labelledby="taskboard-heading">
    <div className="wb-taskboard-header">
      <h1 id="taskboard-heading">Taskboard</h1>
      <div className="wb-taskboard-options"><label>Project <select aria-label="Project" value={projectFilter} onChange={event => setProjectFilter(event.target.value)}>
        <option value="">All projects</option>
        {projects.map(project => <option key={project.id} value={project.id}>{project.name}</option>)}
      </select></label>
      <label>Source <select aria-label="Source" value={source} onChange={event=>setSource(event.target.value as 'all'|'mcp'|'ui')}>
        <option value="all">All sources</option><option value="mcp">MCP only</option><option value="ui">UI only</option>
      </select></label>
      <label>Group by <select value={grouping} onChange={event => setGrouping(event.target.value as Grouping)}>
        <option>Business objective</option><option>Story</option><option>None</option>
      </select></label>
      <label>Date <select value={dateOrder} onChange={event => setDateOrder(event.target.value as 'newest' | 'oldest')}>
        <option value="newest">Newest first</option><option value="oldest">Oldest first</option>
      </select></label></div>
    </div>
    {catalogUnavailable && <p role="status">Business objectives are unavailable.</p>}
    <details className="wb-business-objectives" open>
      <summary>Business objectives</summary>
      <div className="wb-taskboard-filters">
        <button type="button" aria-pressed={!selectedObjective && source==='all'} onClick={() => {setBusinessFilter('');}}>
          All objectives
        </button>
        <button type="button" aria-pressed={source==='mcp' && !selectedObjective}
          onClick={()=>{setSource('mcp');}}>
          MCP tasks <span>{projectRows.filter(row=>row.request).length}</span>
        </button>
        <button type="button" aria-pressed={selectedObjective === 'global'}
          onClick={() => {setBusinessFilter('global');}}>
          Global platform <span>{allRows.filter(row=>businessId(row)==='global').length}</span>
        </button>
        <button type="button" aria-pressed={selectedObjective === otherTasks}
          onClick={() => {setBusinessFilter(otherTasks);}}>
          Other tasks / bugs <span>{allRows.filter(isOtherTask).length}</span>
        </button>
        {businessObjectives.map(objective => {
          const linked = allRows.filter(row => businessId(row) === objective.id);
          const {label, tone, Icon} = objectiveStatus(linked, loading || unavailable.length > 0);
          const descriptionId = 'business-objective-status-' + objective.id;
          const completed = linked.filter(row => ['Completed','Done'].includes(row.status)).length;
          return <button type="button" key={objective.id} aria-describedby={descriptionId}
            aria-pressed={selectedObjective === objective.id} title={label + ' · ' + completed + ' of ' + linked.length + ' tasks complete'}
            onClick={() => {setBusinessFilter(objective.id);setGrouping('Business objective');}}>
            <Icon className="wb-business-status" data-tone={tone} size={15} aria-hidden="true" />
            {objective.priority} · {objective.id} — {objective.title}
            <span id={descriptionId} hidden>{label}. {completed} of {linked.length} tasks complete.</span>
          </button>;
        })}
      </div>
      {businessObjectives.filter(objective => selectedObjective ? objective.id === selectedObjective : objective.current_focus)
        .map(objective => <p key={objective.id}>
          {objective.current_focus && <strong>Current focus · </strong>}
          {objective.id} — {objective.expected_outcome}
        </p>)}
    </details>
    <div className="wb-taskboard-filters" aria-label="Taskboard view">
      <button type="button" aria-pressed={view === 'list'} onClick={() => setView('list')}>List</button>
      <button type="button" aria-pressed={view === 'kanban'} onClick={() => setView('kanban')}>Kanban</button>
      <button type="button" aria-pressed={view==='mcp'} onClick={()=>setView('mcp')}>MCP requests</button>
    </div>
    <nav aria-label="Filter tasks" className="wb-taskboard-filters">
      {filters.map(item => <button key={item} type="button" aria-pressed={filter === item} onClick={() => setFilter(item)}>
        {item} <span>{outcomeRows.filter(row => matches(item, row)).length}</span>
      </button>)}
    </nav>
    <label className="wb-outcome-filter">Execution outcome <select aria-label="Execution outcome" value={execution} onChange={e=>setExecution(e.target.value as Outcome)}>
      {outcomes.map(item=><option value={item} key={item}>{item} ({candidates.filter(row=>matches(filter,row) && (item==='All outcomes'||outcome(row)===item)).length})</option>)}</select></label>
    {view==='mcp' ? <RequestInbox items={visible.map(row=>row.request!)} reference={receipt} loaded={mcp.loaded} error={mcp.error}
      group={requestGroupLabel}/> : <>
    <div className="wb-taskboard-actions" aria-label="Bulk task actions">
      <button type="button" disabled={changing || !approvable.length} onClick={() => void change('approve', approvable)}>
        <CircleCheck size={16} aria-hidden="true" /> Approve all ({approvable.length})
      </button>
      <button type="button" disabled={changing || !deletable.length} onClick={() => void change('delete', deletable)}>
        <Trash2 size={16} aria-hidden="true" /> Delete all ({deletable.length})
      </button>
      <small>Applies to eligible tasks in this view.</small>
    </div>
    {notice && <p role="status">{notice}</p>}
    {actionError && <p role="alert">{actionError}</p>}
    {loading && <p role="status">Loading tasks…</p>}
    {!!unavailable.length && <p role="status">Could not refresh {projects.filter(project =>
      unavailable.includes(project.id)).map(project => project.name).join(', ')}.</p>}
    {!loading && !visible.length && <p>No tasks match this view.</p>}
    {view === 'kanban' ? <><nav aria-label="Kanban navigation" className="wb-lane-controls">
      <button type="button" onClick={()=>lanes.current?.scrollBy({left:-260})}>← Previous columns</button>
      <select aria-label="Go to column" defaultValue="" onChange={event=>{const lane=lanes.current?.children[Number(event.target.value)] as HTMLElement|undefined;
        if(lane && lanes.current) lanes.current.scrollLeft=lane===lanes.current.lastElementChild?lanes.current.scrollWidth:
          lane.offsetLeft-(lanes.current.firstElementChild as HTMLElement).offsetLeft;}}>
        <option value="" disabled>Go to column</option>{stages.map((stage,index)=><option value={index} key={stage}>{stage}</option>)}</select>
      <button type="button" onClick={()=>lanes.current?.scrollBy({left:260})}>Next columns →</button></nav>
      <div ref={lanes} className="wb-kanban"  role="region" aria-label="Business objective Kanban" tabIndex={0}>
      {stages.map(stage => {
        const cards = visible.filter(item => kanbanStage(item) === stage);
        return <section className="wb-kanban-lane" key={stage} aria-label={stage}>
          <h2>{stage} <small>{cards.length}</small></h2>
          {cards.length ? <ul className="wb-taskboard-list">{cards.map(renderTask)}</ul> : <p>No tasks</p>}
        </section>;
      })}
    </div></> : sections.map(section => {
      const items = visible.filter(({status}) => sectionOf(status) === section);
      if (!items.length) return null;
      return <section className="wb-taskboard-section" key={section}>
        <h2>{section}</h2>
        {groups(items).map(([group, members]) => <div className="wb-taskboard-group" key={group}>
          {group && <h3>{group}</h3>}
          <TaskTable rows={members.map(tableLine)} label={[section,group].filter(Boolean).join(' · ')}/></div>)}
    </section>;
    })}</>}
  </section>;
}
