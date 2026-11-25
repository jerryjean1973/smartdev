'use client';

import { useEffect, useState, type FormEvent } from 'react';
type Entry = { name: string; version: number; updated: string };
export default function Secrets({projectId}: {projectId: string}) {
  const [scope,setScope]=useState('DEV');
  const [entries,setEntries]=useState<Entry[]>([]);
  const [filter,setFilter]=useState('');
  const [editing,setEditing]=useState<Entry | null>(null);
  const [adding,setAdding]=useState(false);
  const [message,setMessage]=useState('');
  const [ready,setReady]=useState(false);
  const base='/api/projects/'+projectId+'/secrets/'+scope;
  async function call(path='',method='GET',body?: object) {
    const response=await fetch(base+path,{method,...(body?{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})});
    const data=await response.json();
    if (!response.ok) throw Error(typeof data.detail==='string'?data.detail:'Vault request failed.');
    return data;
  }
  useEffect(()=>{
    let closed=false;
    setEditing(null);setAdding(false);setEntries([]);setReady(false);setMessage('');
    void call().then(rows=>{if(!closed){setEntries(rows);setReady(true);}},
      error=>{if(!closed)setMessage(error.message)});
    return ()=>{closed=true;};
  },[base]);
  async function action(operation:()=>Promise<void>) {
    setMessage('');
    try { await operation(); setEntries(await call()); }
    catch (error) { setMessage(error instanceof Error?error.message:'Vault request failed.'); }
  }
  function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form=event.currentTarget;
    const values=new FormData(form);
    const name=editing?.name||String(values.get('name'));
    void action(async()=>{
      const result=await call('/'+encodeURIComponent(name),'PUT',{version:editing?.version??0,value:values.get('value')});
      form.reset();setAdding(false);setEditing(null);setMessage('Saved vault version '+result.version+'.');
    });
  }
  return <section className="panel">
    <h3>Secrets</h3>
    <label>Environment<select value={scope} onChange={event=>setScope(event.target.value)}><option>DEV</option><option>QA</option><option>PREPROD</option><option>PROD</option></select></label>
    {message&&<p role="status">{message}</p>}
    {ready&&<>
      <label>Filter secrets<input value={filter} onChange={event=>setFilter(event.target.value)}/></label>
      <button onClick={()=>{setAdding(true);setEditing(null);}}>New secret</button>
      {(adding||editing)&&<form key={editing?.name??'new'} onSubmit={save}>
        {!editing&&<label>Name<input name="name" pattern="[A-Z_][A-Z0-9_]{0,95}" required/></label>}
        <label>{editing?'New value for '+editing.name:'Value'}<input name="value" type="password" autoComplete="new-password" required/></label>
        <button>Save secret</button><button type="button" onClick={()=>{setAdding(false);setEditing(null);}}>Cancel</button>
      </form>}
      {entries.filter(row=>row.name.includes(filter.toUpperCase())).map(row=><article className="wb-task" key={row.name}>
        <p><strong>{row.name}</strong> · version {row.version} · ••••••••</p>
        <button onClick={()=>{setEditing(row);setAdding(false);}}>Rotate</button>
        {row.name !== 'OPENAI_API_KEY' && <button onClick={()=>void action(async()=>{
          const result=await call('/'+row.name+'/reveal','POST',{version:row.version});
          await navigator.clipboard.writeText(result.value);
          setMessage('Secret copied.');
        })}>Copy</button>}
        <button onClick={()=>{if(window.confirm('Delete the current version of '+row.name+'?'))
          void action(async()=>{await call('/'+row.name+'/delete','POST',{version:row.version});});}}>Delete</button>
      </article>)}
    </>}
  </section>;
}
