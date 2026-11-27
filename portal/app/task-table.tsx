'use client';

import {Fragment,useState,type ReactNode} from 'react';

export type TaskLine = {
  key:string; number?:string|null; description:string; objective?:string|null; project:string;
  tenant?:string|null; source:string; execution:string; verification:string; created?:string|null;
  updated?:string|null; evidence:string; active?:boolean; details:()=>ReactNode; actions?:ReactNode;
};
const columns = [
  ['number','Task number'],['description','Description'],['objective','BO'],['project','Project'],
  ['tenant','Tenant'],['source','Source'],['execution','Execution'],['verification','Verification'],
  ['created','Created'],['updated','Updated'],['evidence','Evidence'],
] as const;
type Column = typeof columns[number][0];
const clock=new Intl.DateTimeFormat('en-CA',{timeZone:'America/Toronto',month:'short',day:'numeric',year:'numeric',hour:'numeric',minute:'2-digit'});
const collator=new Intl.Collator('en',{numeric:true,sensitivity:'base'});

export function shortTitle(input:string){
  const text=input.replace(/\s+/g,' ').trim().replace(/^(P[0-3]\s+)FOLLOW-UP:\s*/i,'$1');
  const end=text.search(/(?:\.\s|:\s)/);
  const short=(end>=16 && end<=110 ? text.slice(0,end) : text).slice(0,125).trim();
  if(!/[a-z]/.test(short) && /[A-Z]/.test(short)){
    const normal=short.toLowerCase().replace(/\b(p[0-3]|cc|mcp|vg|smd|api|bo-?\d+)\b/gi,m=>m.toUpperCase()).replace(/\bsmartdev\b/g,'SmartDev');
    return normal.charAt(0).toUpperCase()+normal.slice(1);
  }
  return short || 'Description not retained';
}

export function TaskTable({rows,label,initial}:{rows:TaskLine[];label:string;initial?:string}){
  const [open,setOpen]=useState<string|undefined>(initial);
  const [sort,setSort]=useState<{key:Column;ascending:boolean}>();
  const ordered=sort?[...rows].sort((a,b)=>collator.compare(String(a[sort.key]??''),String(b[sort.key]??''))*(sort.ascending?1:-1)):rows;
  return <div className="wb-task-table-wrap" role="region" aria-label={label} tabIndex={0}>
    <table className="wb-task-table"><caption className="wb-sr-only">{label} · times in Toronto</caption>
      <thead><tr>{columns.map(([key,name])=><th key={key} scope="col" aria-sort={sort?.key===key?(sort.ascending?'ascending':'descending'):'none'}>
        <button type="button" onClick={()=>setSort({key,ascending:sort?.key===key?!sort.ascending:true})}>{name}{sort?.key===key?(sort.ascending?' ↑':' ↓'):''}</button>
      </th>)}<th scope="col">Actions</th></tr></thead>
      <tbody>{ordered.map(row=><Fragment key={row.key}><tr data-work-id={row.key}>
        {columns.map(([key])=><td key={key}>{key==='description'?<button type="button" className="wb-table-title" aria-expanded={open===row.key}
          onClick={()=>setOpen(open===row.key?undefined:row.key)}>{shortTitle(row.description)}</button>:
          key==='created'||key==='updated'?(row[key]?<time dateTime={row[key]!} title="America/Toronto">{clock.format(new Date(row[key]!))}</time>:'Not retained'):
          <>{row[key] || (key==='objective'?'—':'Not retained')}{key==='execution' && row.active && <progress aria-label="Processing"/>}</>}</td>)}
        <td>{row.actions}<button type="button" aria-expanded={open===row.key} onClick={()=>setOpen(open===row.key?undefined:row.key)}>{open===row.key?'Close':'Details'}</button></td>
      </tr>{open===row.key && <tr><td colSpan={columns.length+1}>{row.details()}</td></tr>}</Fragment>)}</tbody>
    </table></div>;
}
