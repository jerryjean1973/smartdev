'use client';

import {useState, type FormEvent} from 'react';

type Inspection = {
  zone:string; nameservers:string[]; records:{name:string;type:string;ttl:number}[]; more:boolean;
};

export default function DnsInspection() {
  const [result,setResult]=useState<Inspection>();
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);

  async function inspect(event:FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const zone=new FormData(event.currentTarget).get('zone');
    setResult(undefined);
    setError('');
    setBusy(true);
    try {
      const response=await fetch('/api/integrations/cloudflare/dns-inspection',{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({zone}),
      });
      const data=await response.json();
      if (!response.ok) throw Error(typeof data.detail==='string'?data.detail:'DNS lookup failed.');
      setResult(data);
    } catch (cause) {
      setError(cause instanceof Error?cause.message:'DNS lookup failed.');
    } finally { setBusy(false); }
  }

  return <details>
    <summary>Inspect DNS records</summary>
    <form onSubmit={inspect}>
      <label>DNS zone<input name="zone" required maxLength={253} placeholder="example.com" /></label>
      <button disabled={busy}>{busy?'Checking…':'Check delegation and records'}</button>
    </form>
    {error && <p role="alert">{error}</p>}
    {result && <>
      <p>{result.zone} · {result.nameservers.join(', ')}</p>
      <p>{result.records.length} record entries{result.more?' on the first page':''}</p>
      <ul>{result.records.map((item,index)=><li key={index}>{item.type} · {item.name} · TTL {item.ttl}</li>)}</ul>
    </>}
  </details>;
}
