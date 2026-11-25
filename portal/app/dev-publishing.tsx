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
