'use client';

import {useEffect,useState} from 'react';

type Publishing = {url:string|null; gateway:boolean; workspace:string; runtime:string; ready:boolean; enabled:boolean};
export default function DevPublishing({projectId,openPreview,openLogs}:{
  projectId:string;openPreview:()=>void;openLogs:()=>void;
}) {
  const path='/api/projects/'+projectId+'/dev-publishing';
  const [state,setState]=useState<Publishing>();
  const [status,setStatus]=useState('');
  const [busy,setBusy]=useState(false);
  useEffect(()=>{
    let active=true;
    async function poll() {
      try {
        const response=await fetch(path,{cache:'no-store'});
        if (!response.ok) throw Error('DEV publishing status is unavailable.');
        const data=await response.json();
        if (active) setState(data);
      } catch {
        if (active) setState(undefined);
      }
    }
    void poll();
    const timer=setInterval(poll,5000);
    return ()=>{active=false;clearInterval(timer);};
  },[path]);
  async function action(name:'start'|'stop'|'restart') {
    setBusy(true);
    try {
      const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:name})});
      if (!response.ok) throw Error((await response.json()).detail || 'DEV application request failed.');
      setStatus('DEV application '+name+' requested.');
    } catch(error) {setStatus(error instanceof Error ? error.message : 'DEV application request failed.');}
    finally {setBusy(false);}
  }
  return <section className="panel">
    <h3>DEV Publishing</h3>
    <DevRelease projectId={projectId} />
    {state?.url && <p><code>{state.url}</code></p>}
    {state ? <p>Gateway {state.gateway?'healthy':'unavailable'} · Runtime {state.runtime}
      {' · '}HTTP readiness {state.ready?'passed':'pending'}</p> : <p>Checking DEV publishing status…</p>}
    <div className="wb-actions">
      <button disabled={busy || !state?.url} onClick={()=>void action('start')}>Start application</button>
      <button disabled={busy || !state?.url} onClick={()=>void action('stop')}>Stop application</button>
      <button disabled={busy || !state?.url} onClick={()=>void action('restart')}>Restart application</button>
      <button onClick={openPreview}>Open Preview</button>
      {state?.url && <a href={state.url} target="_blank" rel="noopener noreferrer">Open DEV URL</a>}
      <button onClick={openLogs}>View logs</button>
    </div>
    {status && <p role="status">{status}</p>}
  </section>;
}

type ReleaseReceipt = {
  dispatch_id: string;
  host_receipt_id?: string;
  deployed_revision?: string;
  outcome?: string;
};

function DevRelease({projectId}: {projectId: string}) {
  const path = '/api/projects/' + projectId + '/dev-release';
  const [release, setRelease] = useState<{revision: string | null; receipt: ReleaseReceipt | null}>();
  const [approval, setApproval] = useState('');
  const [message, setMessage] = useState('');
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    let active = true;
    async function refresh() {
      try {
        const response = await fetch(path, {cache: 'no-store'});
        if (active && response.ok) setRelease(await response.json());
      } catch {
        // The next refresh can recover from a transient connection failure.
      }
    }
    void refresh();
    const timer = setInterval(refresh, 5000);
    return () => { active = false; clearInterval(timer); };
  }, [path]);

  async function deploy() {
    if (!release?.revision || submitting) return;
    setSubmitting(true);
    setMessage('');
    try {
      const {grant, ticket} = JSON.parse(approval);
      if (grant?.project_id !== projectId || grant.revision !== release.revision || typeof ticket !== 'string') {
        throw Error('Use a signed MIG approval and ticket for this project and revision.');
      }
      const response = await fetch(path, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          approval_id: grant.id, task_id: grant.task_id, execution_id: grant.execution_id,
          environment: grant.environment, operation: grant.operation,
          workspace: grant.workspace, revision: grant.revision, grant, ticket,
        }),
      });
      if (!response.ok) throw Error('SmartDev rejected the release. Check that the signed approval is current.');
      setApproval('');
      setMessage('Release submitted. Deployment is confirmed when the host receipt arrives.');
    } catch (error) {
      setMessage(error instanceof SyntaxError ? 'Enter the signed approval as JSON.' :
        error instanceof Error ? error.message : 'Unable to submit the release.');
    } finally {
      setSubmitting(false);
    }
  }

  return <section>
    <h4>Deploy DEV revision</h4>
    {release?.revision ? <>
      <p>Available revision <code>{release.revision}</code></p>
      <label>Signed MIG approval and ticket
        <textarea value={approval} onChange={event => setApproval(event.target.value)} autoComplete="off" />
      </label>
      <button type="button" disabled={!approval.trim() || submitting} onClick={() => void deploy()}>Deploy revision</button>
    </> : <p>{release ? 'No release is configured for this project.' : 'Checking project release configuration…'}</p>}
    {release?.receipt && <p>
      Release {release.receipt.outcome || 'queued'} · Dispatch <code>{release.receipt.dispatch_id}</code>
      {release.receipt.deployed_revision && <> · Deployed revision <code>{release.receipt.deployed_revision}</code></>}
      {release.receipt.host_receipt_id && <> · Host receipt <code>{release.receipt.host_receipt_id}</code></>}
    </p>}
    {message && <p role="status">{message}</p>}
  </section>;
}
