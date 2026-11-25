'use client';

import {useEffect,useState} from 'react';

type Key = {id:string; repository:string; status:string; remote_id:number|null};

export default function DeployKeys({projectId, repository}:{projectId:string;repository:string}) {
  const [keys,setKeys]=useState<Key[]>([]);
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  const endpoint='/api/integrations/github/projects/'+projectId+'/deploy-keys';

  async function refresh() {
    const response=await fetch(endpoint,{cache:'no-store'});
    if (!response.ok) throw Error('Could not load deploy keys.');
    setKeys(await response.json());
  }

  useEffect(()=>{
    void refresh().catch(()=>setError('Could not load deploy keys.'));
  },[endpoint]);

  async function change(id:string, target:string, revoke=false) {
    if (!window.confirm((revoke?'Revoke this deploy key for ':'Register or verify this read-only deploy key for ')+target+'?')) return;
    setBusy(true);
    setError('');
    try {
      const response=await fetch(endpoint+'/'+id+(revoke?'/revoke':''),{
        method:revoke?'POST':'PUT',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({repository:target,approved:true}),
      });
      if (!response.ok) throw Error((await response.json()).detail || 'Deploy-key operation failed.');
    } catch (error) {
      setError(error instanceof Error ? error.message : 'Deploy-key operation failed.');
    } finally {
      await refresh().catch(()=>setError('Could not reload deploy keys.'));
      setBusy(false);
    }
  }

  return <details>
    <summary>Deploy keys · {repository}</summary>
    <p>Read-only access. The GitHub App needs Administration write permission on this repository.</p>
    {error && <p role="alert">{error}</p>}
    <button disabled={busy || keys.some(key=>key.status==='pending' || key.status==='registered')}
      onClick={()=>void change(crypto.randomUUID(),repository)}>Provision read-only key</button>
    <ul>{keys.map(key=><li key={key.id}>
      {key.repository} · {key.status}{key.remote_id && ' · GitHub key '+key.remote_id}
      {['pending','registered'].includes(key.status) && <button disabled={busy} onClick={()=>void change(key.id,key.repository)}>Retry verification</button>}
      {key.status!=='revoked' && <button disabled={busy} onClick={()=>void change(key.id,key.repository,true)}>Revoke</button>}
    </li>)}</ul>
  </details>;
}
