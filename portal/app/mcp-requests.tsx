'use client';

import {useEffect, useState} from 'react';
import {TaskTable,type TaskLine} from './task-table';

export type McpRequest = {
  task_number?:string; tenant_name?:string; connector?:string; actor?:string; destination?:string; message_id?:string; failure_reason?:string; repair?:{summary:string; verified_at:string; evidence:string[]}|null; admission?:string|null; matched_request?:string|null; request_id:string; project_id:string; project_name:string; task_id:string|null; task_title:string|null;
  architect_review_required:boolean; architect_reviewed:boolean; architect_review_id?:string|null; architect_execution_id?:string|null; architect_model?:string|null; architect_classification?:string|null; architect_approved?:boolean|null; architect_reviewed_at?:string|null;
  work_status:'Pending'|'Queued'|'Processing'|'Processed'|'Done'; execution_code:number|null; waiting_for_input:boolean; execution_updated_at:string|null; scope_kind:'objective'|'global'|'unassigned'; business_objective:string|null; receipt:string|null; kind:string; delivery:string; received_at:string|null; label:string;
};
const zone = 'America/Toronto';
const day = new Intl.DateTimeFormat('en-CA',{timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit'});
const clock = new Intl.DateTimeFormat('en-CA',{timeZone:zone,month:'short',day:'numeric',year:'numeric',hour:'numeric',minute:'2-digit'});
const delivery:Record<string,string> = {accepted:'Accepted by workspace',received:'Received',unconfirmed:'Execution unconfirmed'};
const kinds:Record<string,string> = {create:'New task',message:'Follow-up',reply:'Reply',submission:'Submission'};

export function useMcpRequests(projectId = '') {
  const [requests,setRequests] = useState<McpRequest[]>([]);
  const [error,setError] = useState(false);
  const [loaded,setLoaded] = useState(false);
  useEffect(()=>{
    const controller=new AbortController();
    let timer:ReturnType<typeof setTimeout>;
    async function load(){
      try {
        const r=await fetch('/api/taskboard/mcp-requests'+(projectId?'?project_id='+encodeURIComponent(projectId):''),{cache:'no-store',signal:AbortSignal.any([controller.signal,AbortSignal.timeout(9000)])});
        if(!r.ok) throw Error();
        const data:McpRequest[]=await r.json();
        if(!controller.signal.aborted){setRequests(data);setError(false);setLoaded(true);}
      } catch {if(!controller.signal.aborted) setError(true);}
      if(!controller.signal.aborted) timer=setTimeout(load,5000);
    }
    void load();
    return ()=>{controller.abort();clearTimeout(timer);};
  },[projectId]);
  return {requests:projectId?requests.filter(r=>r.project_id===projectId):requests,error,loaded};
}

export function RequestDetails({item,expanded=false,card=false}:{item:McpRequest;expanded?:boolean;card?:boolean}) {
  const [open,setOpen]=useState(expanded);
  const [text,setText]=useState<string>();
  const [error,setError]=useState(false);
  const [retry,setRetry]=useState(0);
  useEffect(()=>{
    if(!open || text!==undefined) return;
    const controller=new AbortController();
    void fetch('/api/taskboard/mcp-requests/'+item.request_id+'?project_id='+item.project_id,{cache:'no-store',signal:controller.signal})
      .then(async r=>{if(!r.ok) throw Error();return r.json();})
      .then(data=>{if(!controller.signal.aborted){setText(data.instruction || 'The original instruction was not retained.');setError(false);}})
      .catch(()=>{if(!controller.signal.aborted)setError(true);});
    return ()=>controller.abort();
  },[open,item.request_id,item.project_id,retry,text]);
  return <details open={open} onToggle={e=>setOpen(e.currentTarget.open)}>
    <summary>{card ? 'Instruction and receipt' : <strong>{item.label} · {item.work_status}</strong>} <small>{item.received_at ? clock.format(new Date(item.received_at))+' Toronto' : 'Time unavailable'}</small></summary>
    {open && <div className="wb-mcp-message">
      <p>{item.admission==='duplicate'?'Already covered':item.admission==='updated'?'Updated existing task':kinds[item.kind]} · {item.project_name}</p>
      {item.task_id && <p>Codex task ID <code>{item.task_id}</code></p>}
      {item.task_title && <p>Submitted through {item.task_title}</p>}
      <p>Request ID <code>{item.request_id}</code></p>
      <p>Message ID <code>{item.message_id || 'Not retained'}</code></p>
      <p>Execution receipt <code>{item.receipt || 'Not retained'}</code></p>
      <p>Architect review {item.architect_review_required ? 'On' : 'Off'}
        {item.architect_reviewed && <> · {item.architect_classification} · receipt <code>{item.architect_review_id}</code></>}</p>
      <p>Source {item.connector || 'Not retained'} · Actor {item.actor || 'Not retained'} · Destination {item.destination || 'Not retained'}</p>
      {item.failure_reason && <p>{item.failure_reason}</p>}
      {item.repair && <div><p>{item.repair.summary}</p>{item.repair.evidence.map(url=><a key={url} href={url} target="_blank" rel="noreferrer">Open {url.split('/').pop()} </a>)}</div>}
      {error ? <button type="button" onClick={()=>setRetry(r=>r+1)}>Retry loading instruction</button> : <div>{text ?? 'Loading instruction…'}</div>}
    </div>}
  </details>;
}

export function receiptLine(item:McpRequest):TaskLine {
  const failed=item.execution_code!=null && item.execution_code!==0;
  return {key:item.request_id,number:item.task_number,description:item.label,objective:item.business_objective,project:item.project_name,
    tenant:item.tenant_name,source:item.connector || 'Not retained',execution:failed?'Failed · '+item.execution_code:item.work_status,
    verification:item.repair?'Repair verified':item.work_status==='Done'?'Owner completed':'Unverified',
    architectReview:item.architect_review_required?'On':'Off',
    created:item.received_at,updated:item.repair?.verified_at || item.execution_updated_at || item.received_at,
    evidence:item.repair?item.repair.evidence.length+' files':item.receipt?'Run receipt':'—',active:item.work_status==='Processing',
    details:()=><RequestDetails item={item} expanded card/>};
}

export function RequestList({items,reference}:{items:McpRequest[];reference?:string}) {
  return <ul className="wb-mcp-requests">{items.map(item=><li key={item.project_id+':'+item.request_id}><RequestDetails item={item}
    expanded={Boolean(reference && [item.receipt,item.request_id].includes(reference))}/></li>)}</ul>;
}

export function RequestInbox({items,reference,group,loaded,error}:{items:McpRequest[];reference:string;
  group:(item:McpRequest)=>string;loaded:boolean;error:boolean}) {
  const [today,setToday]=useState(!reference);
  const [query,setQuery]=useState(reference);
  const date=day.format(new Date());
  const todayItems=items.filter(r=>r.received_at && day.format(new Date(r.received_at))===date);
  const term=query.trim().toLowerCase();
  const filtered=(today?todayItems:items).filter(r=>!term || [r.label,r.receipt,r.request_id,r.task_id,r.task_title,r.project_name,r.task_number,r.tenant_name,r.connector,group(r)].some(v=>v?.toLowerCase().includes(term)));
  const groups=new Map<string,McpRequest[]>();
  for(const r of filtered){const key=group(r);const members=groups.get(key)??[];members.push(r);groups.set(key,members);}
  return <section aria-label="MCP requests" className="wb-mcp-inbox">
    <div className="wb-taskboard-filters"><button aria-pressed={today} onClick={()=>setToday(true)}>Today in Toronto ({todayItems.length})</button>
      <button aria-pressed={!today} onClick={()=>setToday(false)}>All dates ({items.length})</button>
      <label>Find a request <input type="search" placeholder="Receipt or request title" value={query} onChange={e=>setQuery(e.target.value)}/></label></div>
    {error && <p role="status">Could not refresh MCP requests. Retrying…</p>}
    {!loaded && !error && <p>Loading MCP requests…</p>}
    {loaded && !filtered.length && <p>No MCP requests match this view.</p>}
    {[...groups].map(([name,members])=><section key={name}><h3>{name}</h3><TaskTable label={name+' MCP requests'} rows={members.map(receiptLine)} initial={members.find(item=>[item.receipt,item.request_id].includes(reference))?.request_id}/></section>)}
  </section>;
}
