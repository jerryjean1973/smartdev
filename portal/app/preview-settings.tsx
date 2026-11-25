'use client';

import {useState} from 'react';

export const environments = ['DEV','QA','PREPROD','PROD'] as const;
export type Environment = typeof environments[number];
export type PreviewEntry = {mode:'url'|'automatic';url:string|null};
export type PreviewSettings = Partial<Record<Environment,PreviewEntry>>;
export const defaultPreview: PreviewEntry = {mode:'automatic',url:null};

export default function ApplicationPreviewSettings({projectId, settings, onSave}:{
  projectId:string; settings: PreviewSettings; onSave:(environment:Environment,config:PreviewEntry)=>void;
}) {
  const [environment,setEnvironment]=useState<Environment>('DEV');
  const saved=settings[environment] || defaultPreview;
  return <section className="panel">
    <h3>Application Preview</h3>
    <label>Preview environment<select value={environment} onChange={event=>setEnvironment(event.target.value as Environment)}>
      {environments.map(value=><option key={value}>{value}</option>)}
    </select></label>
    <PreviewForm key={environment} saved={saved} projectId={projectId} environment={environment} onSave={onSave} />
  </section>;
}

function PreviewForm({saved,projectId,environment,onSave}:{
  saved:PreviewEntry; projectId:string; environment:Environment; onSave:(environment:Environment,config:PreviewEntry)=>void;
}) {
  const [url,setUrl]=useState(saved.url || '');
  const [mode,setMode]=useState(saved.mode);
  const [message,setMessage]=useState(saved.url ? 'Connection untested.' : 'No URL configured.');
  const [busy,setBusy]=useState(false);
  function target() {
    const parsed=new URL(url.trim());
    if (parsed.protocol!=='https:' || parsed.username || parsed.password || parsed.origin===location.origin)
      throw Error('Enter a separate HTTPS application URL without credentials.');
    return parsed.href;
  }
  async function test() {
    let address;
    try { address=target(); } catch {setMessage('Enter a separate HTTPS application URL without credentials.');return;}
    setBusy(true);
    try {
      const response=await fetch(address,{mode:'no-cors',credentials:'omit',referrerPolicy:'no-referrer',signal:AbortSignal.timeout(8000)});
      setMessage(response.type==='opaque' ? 'Browser connected. HTTP status and embedding are not visible to SmartDev.' :
        response.ok ? 'Application responded.' : 'Application returned HTTP '+response.status+'.');
    } catch {setMessage('Connection could not be verified from this browser. Open in browser to check access.');}
    finally {setBusy(false);}
  }
  async function save() {
    try {
      const address=url.trim() ? target() : null;
      setBusy(true);
      const request=await fetch('/api/projects/'+projectId+'/preview/'+environment,{method:'PUT',
        headers:{'Content-Type':'application/json'},body:JSON.stringify({url:address,mode})});
      const data=await request.json();
      if (!request.ok) throw Error(typeof data.detail==='string'?data.detail:'Preview configuration could not be saved.');
      onSave(environment,data);
      setMessage('Preview settings saved.');
    } catch(error) {setMessage(error instanceof Error?error.message:'Preview configuration could not be saved.');}
    finally {setBusy(false);}
  }
  return <form onSubmit={event=>{event.preventDefault();void save();}}>
    <label>Preview URL<input type="url" value={url} onChange={event=>setUrl(event.target.value)} placeholder="https://" /></label>
    <label>Preview mode<select value={mode} onChange={event=>setMode(event.target.value as PreviewEntry['mode'])}>
      <option value="automatic">Automatic</option><option value="url">URL only</option>
    </select></label>
    <p role="status">{message}</p>
    <div className="wb-actions">
      <button disabled={busy}>Save preview settings</button>
      <button type="button" disabled={busy || !url.trim()} onClick={()=>void test()}>Test connection</button>
      {saved.url && <a href={saved.url} target="_blank" rel="noopener noreferrer">Open in browser</a>}
    </div>
  </form>;
}
