'use client';

import {useEffect,useState} from 'react';

type Status = {
  bindings: Record<string,string>;
  deployment?: {image:string; digest:string; health:string; collectedAt:string;
    events:{at:string; code:string}[]};
  configured: string[];
  observed: {status?:string; phase?:string; executable?:string; directory?:string; port?:number; healthPath?:string; lastSuccessfulStartup?:string};
};

export default function Runtime({projectId}:{projectId:string}) {
  const [environment,setEnvironment]=useState('DEV');
  const [status,setStatus]=useState<Status>();
  const [bindings,setBindings]=useState('');
  const [message,setMessage]=useState('');
  const url='/api/projects/'+projectId+'/runtime/'+environment;
  async function load() {
    const response=await fetch(url,{cache:'no-store'});
    if (!response.ok) throw new Error('Runtime configuration is unavailable.');
    return await response.json() as Status;
  }
  useEffect(()=>{
    let current=true;
    setStatus(undefined);
    setBindings('');
    setMessage('');
    void load().then(value=>{
      if (!current) return;
      setStatus(value);
      setBindings(Object.entries(value.bindings).map(([key,name])=>key+'='+name).join('\n'));
    }).catch(error=>{if(current) setMessage(error.message);});
    const timer=setInterval(()=>{
      void load().then(value=>{if (current) setStatus(value);}).catch(()=>{});
    },15000);
    return ()=>{current=false; clearInterval(timer);};
  },[url]);
  async function save() {
    try {
      const pairs=bindings.split('\n').filter(line=>line.trim()).map(line=>line.trim().split('='));
      if (pairs.some(pair=>pair.length!==2)) throw new Error('Enter one VARIABLE=PROJECT_SECRET per line.');
      const response=await fetch(url,{method:'PUT',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({bindings:Object.fromEntries(pairs)})});
      if (!response.ok) throw new Error('Bindings could not be saved. Check the variable and secret names.');
      setStatus(await load());
      setMessage('Runtime bindings saved.');
    } catch(error) {setMessage(error instanceof Error?error.message:'Bindings could not be saved.');}
  }
  return <section className="panel">
    <h3>Application runtime</h3>
    <label>Runtime environment<select value={environment} onChange={event=>setEnvironment(event.target.value)}>
      {['DEV','QA','PREPROD','PROD'].map(item=><option key={item}>{item}</option>)}
    </select></label>
    {status?.deployment && <section>
      <h4>Deployed service</h4>
      <p>{status.deployment.image} · {status.deployment.health}</p>
      <p>Observed {new Date(status.deployment.collectedAt).toLocaleString()}</p>
      <code>{status.deployment.digest}</code>
      <ul>{status.deployment.events.map(({at,code},index)=><li key={at+code+index}>
        {new Date(at).toLocaleTimeString()} · {code.replaceAll('_',' ').toLowerCase()}
      </li>)}</ul>
    </section>}
    {status && <>
      <p>{status.observed.phase?.replaceAll('_',' ') || status.observed.status || 'No startup reported'}{status.observed.port ? ' · Port '+status.observed.port : ''}</p>
      {status.observed.executable && <p>{status.observed.executable} · {status.observed.directory} · {status.observed.healthPath}</p>}
      {status.observed.lastSuccessfulStartup && <p>Last ready {new Date(status.observed.lastSuccessfulStartup).toLocaleString()}</p>}
      {Object.entries(status.bindings).map(([variable,name])=><p key={variable}>
        <code>{variable}</code> {status.configured.includes(name) ? 'Configured' : 'Missing'}
      </p>)}
      <label>Runtime secret bindings<textarea value={bindings} onChange={event=>setBindings(event.target.value)}
        placeholder="APPLICATION_VARIABLE=PROJECT_SECRET" rows={3} /></label>
      <button onClick={()=>void save()}>Save runtime bindings</button>
    </>}
    {message && <p role="status">{message}</p>}
  </section>;
}
