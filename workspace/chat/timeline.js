/* Projection of the existing Codex JSONL transcript into conversation and diagnostics. */
function conversation(text, start=0, scope={}) {
  const entries=[];
  const tools=new Map();
  const runStates=new Map();
  const promptActivities=new Map();
  const partialMessages=new Map();
  const prompts=new Map();
  const questions=new Set();
  const completedMessages=new Set();
  let group;
  let auth;
  let lastRequest='';
  let result;
  let latest;
  let turn = new Map();
  let offset=start;
  const encoder=new TextEncoder();
  const technical=(id, label, data, state='info', at) => {
    if (!group) {
      group={type:'technical',id:'actions:'+id,actions:[]};
      entries.push(group);
    }
    const action={id,label,data,state,at};
    group.actions.push(action);
    return action;
  };
  const narrative=(id, type, message, at) => {
    group=undefined;
    if (message) entries.push({id,type,message,at});
  };
  for (const line of text.split('\n')) {
    const id=String(offset);
    offset+=encoder.encode(line).length+1;
    if (!line.trim()) continue;
    let event;
    try { event=JSON.parse(line); }
    catch {
      const match=/^Codex exit status: (\d+)$/.exec(line);
      if (match) {
        result=Number(match[1]);
        technical(id,'Runner exit status',line,result===0 ? 'info' : result===130 ? 'stopped' : 'failed');
      } else technical(id,'Runner diagnostic',line);
      continue;
    }
    if (!event || typeof event!=='object' || (scope.main && event.objective)) continue;
    const at=event.smartdev_at || event.at;
    const record=event.item;
    if (event.type==='smartdev.user') {
      if (!event.execution) tools.clear();
      lastRequest=event.text;
      latest=event;
      turn=new Map();
      auth=undefined; result=undefined;
      narrative(id,'user',event.text,at);
      if (event.text) {
        const activity={id,state:'thinking',actions:[],commands:new Map()};
        Object.assign(entries.at(-1),{attachments:event.attachments,execution:event.execution,activity});
        if (event.execution) {
          promptActivities.set(event.execution,activity);
          prompts.set(event.execution,entries.at(-1));
        }
        activity.startedAt=at;
      }
    } else if (event.type==='smartdev.prompt_edit') {
      const prompt=prompts.get(event.execution);
      if (prompt && event.text) prompt.message=event.text;
    } else if (event.type==='smartdev.command') {
      const activity=promptActivities.get(event.execution);
      if (activity) {
        let command=activity.commands.get(event.id);
        if (!command) {
          command={text:event.text};
          activity.commands.set(event.id,command);
          activity.actions.push(command);
        }
        Object.assign(command,{state:event.state,directory:event.directory});
        activity.state='running';
        activity.target=event.target || activity.target;
        activity.label=event.text;
      }
    } else if (event.type==='smartdev.progress') {
      const activity=promptActivities.get(event.execution);
      if (activity) {
        activity.actions.push(event.text);
        activity.state='running';
        activity.label=event.text;
      } else narrative(id,'progress',event.text,at);
    } else if (event.type==='smartdev.run_status') {
      runStates.set(event.execution,{state:event.state});
      const activity=promptActivities.get(event.execution);
      if (activity) Object.assign(activity,{state:event.state,label:undefined});
    } else if (event.type==='smartdev.checkpoint') {
      group=undefined;
      entries.push({id,type:'checkpoint',message:event.checkpoint+' · '+event.branch,at});
    } else if (event.type==='smartdev.diagnostic') {
      technical(id,'Runner diagnostic',event.text,'info',at);
    } else if (event.type==='smartdev.challenge' && typeof event.stage==='string') {
      technical(id,'Effective Challenge · '+event.stage,JSON.stringify(event.artifact || event,null,2),
        event.status==='failed'?'failed':event.status==='running'?'running':'done',at);
    } else if (event.type==='smartdev.auth') {
      auth=event;
      technical(id,'Authentication check',event.message,event.status==='unavailable'?'failed':'info',at);
    } else if (event.type==='smartdev.result') {
      result=Number(event.code);
      runStates.set(event.execution,{state:event.awaitingInput?'awaiting_input':result===0?'completed':result===130?'stopped':'failed',error:event.error});
      const activity=promptActivities.get(event.execution);
      if (activity) Object.assign(activity,{state:runStates.get(event.execution).state,label:undefined,endedAt:at});
      technical(id,'Runner exit status',event.error || 'Exit '+event.code,result===0?'info':result===130?'stopped':'failed',at);
    } else if (record?.type==='agent_message' && ['item.updated','item.completed'].includes(event.type)) {
      const messageKey=record.id && (event.execution || '')+':'+record.id;
      if (messageKey && completedMessages.has(messageKey)) continue;
      if (messageKey && event.type==='item.completed') completedMessages.add(messageKey);
      const previous=messageKey && partialMessages.get(messageKey);
      if (previous) {
        const position=entries.indexOf(previous);
        if (position>=0) entries.splice(position,1);
      }
      const parts=String(record.text || '').split(/^Question:\s*/m);
      narrative(id,'agent',parts[0].trim(),at);
      if (parts[0].trim()) Object.assign(entries.at(-1),{copyId:record.copyId,execution:event.execution});
      if (parts[0].trim() && messageKey && event.type==='item.updated') partialMessages.set(messageKey,entries.at(-1));
      if (event.type==='item.completed' && parts.length>1) {
        const question=parts.slice(1).join('\n').trim();
        const key=(event.execution || '')+':'+question;
        if (question && !questions.has(key)) {
          questions.add(key);
          narrative(id+':question','question',question,at);
          entries.at(-1).execution=event.execution;
        }
      }
    } else if (['command_execution','mcp_tool_call','file_change','web_search'].includes(record?.type)) {
      const label=record.type==='command_execution'?'Run command':record.type==='file_change'?'Modify files':record.type==='web_search'?'Search the web':'Call '+(record.tool || 'MCP tool');
      const state=record.status==='failed' || (Number.isInteger(record.exit_code) && record.exit_code!==0)
        ? 'failed' : event.type==='item.completed' ? 'done' : 'running';
      const data=record.type==='command_execution'
        ? [record.command,record.aggregated_output,Number.isInteger(record.exit_code) && 'Exit '+record.exit_code].filter(Boolean).join('\n\n')
        : JSON.stringify(record,null,2);
      const toolId=(event.execution || '')+':'+record.id;
      turn.set(toolId,{type:record.type,command:record.command,state,at});
      if (tools.has(toolId)) {
        const action=tools.get(toolId);
        Object.assign(action,{state,data,endedAt:at});
      } else tools.set(toolId,technical(id,label,data,state,at));
    } else if (event.type==='error' || event.type==='turn.failed') {
      const message=event.message || event.error?.message || 'Codex reported an execution error.';
      if (/\b40[13]\b/.test(message)) auth={status:'unavailable',message:message.includes('401')
        ? 'Codex authentication required. Verify this project’s DEV OpenAI key.'
        : 'OpenAI rejected access for this project key.'};
      technical(id,/reconnecting|retry/i.test(message)?'Connection retry':'Execution error',message,'failed',at);
    } else if (['thread.started','turn.started','turn.completed'].includes(event.type)) {
      technical(id,event.type==='thread.started'?'Session started':event.type==='turn.started'?'Turn started':'Turn finished',
        JSON.stringify(event,null,2),'info',at);
    }
    // Reasoning and unsupported private event payloads are intentionally excluded.
  }
  const responses=new Map();
  for (const entry of entries) {
    if (['agent','question'].includes(entry.type) && prompts.has(entry.execution)) {
      if (!responses.has(entry.execution)) responses.set(entry.execution,[]);
      responses.get(entry.execution).push(entry);
    }
  }
  const ordered=entries.flatMap(entry=>{
    if (entry.type==='user') return [entry,...(responses.get(entry.execution) || [])];
    return ['agent','question'].includes(entry.type) && responses.has(entry.execution) ? [] : [entry];
  });
  return {runStates,entries:ordered,auth,lastRequest,result,latest,actions:[...turn.values()]};
}

if (typeof module!=='undefined') module.exports={conversation};
