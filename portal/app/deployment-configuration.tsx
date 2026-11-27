'use client';
import {useEffect,useState} from 'react';

type Secret = {environment:string;name:string;version:number};
type Tenant = {id:string;name:string};

export default function DeploymentConfiguration({task,assigned,tenants,active}:{
  task:string;assigned:string|null;tenants:Tenant[];active:boolean;
}) {
  const [tenant,setTenant]=useState(assigned || ''),[entries,setEntries]=useState<Secret[]>([]);
  const [env,setEnv]=useState('DEV'),[name,setName]=useState(''),[value,setValue]=useState('');
  const [edit,setEdit]=useState(false),[credential,setCredential]=useState('');
  const [busy,setBusy]=useState(false),[message,setMessage]=useState(''),[revision,refresh]=useState(0);
  const base='/api/admin/tenants/'+tenant+'/defaults';
  useEffect(()=>{
    if (!edit || !tenant) return;
    const ctrl=new AbortController();
    setMessage('');
    fetch(base,{cache:'no-store',signal:ctrl.signal}).then(async r=>{
      if(!r.ok) throw Error('Could not load tenant defaults.');
      const data:Secret[]=await r.json();
      if(!ctrl.signal.aborted) setEntries(data);
    }).catch(e=>{if(!ctrl.signal.aborted) setMessage(e.message);});
    return ()=>ctrl.abort();
  },[base,edit,tenant,revision]);
  async function login() {
    if(credential){setCredential('');return;}
    setBusy(true);setMessage('');
    try{
      const r=await fetch('/api/admin/compute/deployments/'+task+'/tenant-login',{method:'POST',cache:'no-store'});
      const result=await r.json();
      if(!r.ok) throw Error(result.detail || 'Login unavailable.');
      setCredential(result.password);
    }catch(e){setMessage(e instanceof Error?e.message:'Login unavailable.');}
    finally{setBusy(false);}
  }
  async function save(remove?:Secret) {
    const selected=remove || entries.find(v=>v.environment===env && v.name===name);
    const endpoint=base+'/'+(remove?.environment||env)+'/'+encodeURIComponent(remove?.name||name);
    setBusy(true);setMessage('');
    try{
      const r=await fetch(remove?endpoint+'?version='+remove.version:endpoint,{
        method:remove?'DELETE':'PUT',headers:{'Content-Type':'application/json'},
        ...(remove?{}:{body:JSON.stringify({value,version:selected?.version || 0})})});
      if(!r.ok) throw Error((await r.json()).detail || 'Could not update tenant defaults.');
      setValue('');setName('');refresh(v=>v+1);
    }catch(e){setMessage(e instanceof Error?e.message:'Could not update tenant defaults.');}
    finally{setBusy(false);}
  }
  return <section aria-label="Tenant portal access and secrets">
    <h3>Tenant access</h3>
    {active && <div><button disabled={busy} onClick={()=>void login()}>{credential?'Hide tenant portal password':'Show tenant portal password'}</button>
      {credential && <input aria-label="Current tenant portal password" readOnly value={credential} onFocus={e=>e.target.select()} />}</div>}
    <button onClick={()=>{setEdit(v=>!v);setValue('');setMessage('');}}>{edit?'Close tenant defaults':'Manage tenant default secrets'}</button>
    {edit && <div>
      <label>Tenant<select value={tenant} onChange={e=>{setTenant(e.target.value);setEntries([]);setValue('');}}>{tenants.map(t=><option key={t.id} value={t.id}>{t.name}</option>)}</select></label>
      {entries.length>0 ? <ul>{entries.map(v=><li key={v.environment+v.name}>{v.environment} · <code>{v.name}</code> · v{v.version} <button disabled={busy} onClick={()=>void save(v)}>Remove</button></li>)}</ul> : <p>No default secrets configured.</p>}
      <form onSubmit={e=>{e.preventDefault();void save();}}>
        <label>Environment<select value={env} onChange={e=>setEnv(e.target.value)}>{['DEV','QA','PREPROD','PROD'].map(v=><option key={v}>{v}</option>)}</select></label>
        <label>Secret name<input required pattern="[A-Z_][A-Z0-9_]{0,95}" value={name} onChange={e=>setName(e.target.value)} /></label>
        <label>Secret value<input required autoComplete="new-password" type="password" value={value} onChange={e=>setValue(e.target.value)} /></label>
        <button disabled={busy || !tenant} type="submit">Save tenant default</button>
      </form>
    </div>}
    {message && <p role="status">{message}</p>}
  </section>;
}
