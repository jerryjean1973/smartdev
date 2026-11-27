'use strict';
const {createInterface}=require('node:readline');
const {publicTranscript,cleanText}=require('./public-events.cjs');

async function conversationCopy(stream, {project,main,excluded,now,mode,name,responseId}) {
  const cutoff=now-48*60*60*1000;
  const messages=new Map();
  let latest;
  const identities=new Map();
  let offset=0;
  if (stream) for await (const line of createInterface({input:stream,crlfDelay:Infinity})) {
    offset++;
    const projected=publicTranscript(line,project,{main,excluded,completeMessages:true});
    if (!projected) continue;
    const event=JSON.parse(projected);
    const agent=event.item?.type==='agent_message';
    if (!agent && event.type!=='smartdev.user') continue;
    const value=agent ? event.item.text : event.text;
    if (!value.trim()) continue;
    const key=agent && event.item.id ? 'agent:'+event.execution+':'+event.item.id
      : !agent && event.message ? 'user:'+event.message : String(offset);
    const at=Date.parse(event.at);
    if (mode==='latest' || mode==='response') {
      if (mode==='response' && event.item?.copyId!==responseId) continue;
      if (agent) {
        const prior=identities.get(key);
        if (!prior || latest?.key===key && !prior.completed)
          latest={key,value};
        identities.set(key,{completed:prior?.completed || event.type==='item.completed'});
      }
      continue;
    }
    if (!Number.isFinite(at) || at < cutoff || at > now) continue;
    const prior=messages.get(key);
    if (prior?.completed && event.type==='item.updated') continue;
    messages.set(key,{at:prior?.at ?? at,order:prior?.order ?? offset,value,
      speaker:agent?'Codex':'User',completed:event.type==='item.completed'});
  }
  if (mode==='latest' || mode==='response') return latest?.value || '';
  if (!messages.size) return '';
  const rows=[...messages.values()].sort((a,b)=>a.at-b.at || a.order-b.order);
  return ['# SmartDev — Conversation History',
    'Conversation: '+cleanText(name),'Period: Last 48 hours (UTC)',
    ...rows.map(row=>'## '+new Date(row.at).toISOString()+' — '+row.speaker+'\n\n'+row.value)
  ].join('\n\n');
}

module.exports={conversationCopy};
