'use strict';
const {createHash}=require('node:crypto');

function reference(project,value) {
  return typeof value==='string' && value
    ? createHash('sha256').update(project+'\0'+value).digest('hex').slice(0,24) : undefined;
}

function cleanText(value, limit=30000) {
  if (typeof value!=='string') return '';
  return value.slice(0,limit)
    .replace(/-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)/gi,'[credential removed]')
    .replace(/\b(?:gh[pousr]_|github_pat_|sk-|hvs\.|xox[baprs]-)[\w.-]{10,}/gi,'[credential removed]')
    .replace(/\b(?:Bearer|Basic)\s+[A-Za-z0-9_./+=-]+/gi,'[authorization removed]')
    .replace(/\b[A-Z0-9_]*(?:TOKEN|PASSWORD|SECRET|API_KEY|DATABASE_URL|PRIVATE_KEY)\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^\s,;]+)/gi,'[configuration value removed]')
    .replace(/\b(?:postgres(?:ql)?|redis|amqp|mysql):\/\/[^\s`<>"']+/gi,'[service address removed]')
    .replace(/\bhttps?:\/\/(?:localhost|127\.\d+\.\d+\.\d+|(?:[a-z0-9-]+\.)*(?:internal|local))(?::\d+)?[^\s`<>"']*/gi,'[service address removed]')
    .replace(/(?:\/var\/(?:lib|run)|\/run|\/root|\/proc|\/home\/[^/\s]+\/\.codex)\/[^\s`<>"']*/g,'[internal path removed]')
    .replace(/^\s*(?:at \S+ \(|Traceback \(|File "\/)[^\n]*$/gm,'');
}

function activity(command='') {
  if (/\b(test|pytest|vitest|jest|playwright|unittest)\b/i.test(command)) return 'Running tests';
  if (/\b(docker|kubectl|deploy|publish)\b/i.test(command)) return 'Preparing deployment';
  if (/\b(build|compile)\b/i.test(command)) return 'Building application';
  if (/\b(rg|cat|sed|find|ls|git diff)\b/i.test(command)) return 'Reviewing project files';
  return 'Working on the project';
}

const statusLabels=new Set(['thinking','running tests','building','reading project','executing','editing files']);
const statuses=new Set(['queued','running','redirected','cancelled']);
const timestamp=value=>typeof value==='string' && /^\d{4}-\d\d-\d\dT/.test(value) && !isNaN(Date.parse(value)) ? value : undefined;

function publicTranscript(text,project,{main=false,excluded=[],completeMessages=false,target}={}) {
  const suppressed=new Set(excluded);
  const visible=[];
  for (const line of text.split('\n')) {
    let event;
    try { event=JSON.parse(line); } catch {continue;}
    if (!event || suppressed.has(event.execution) || (main && event.objective)) continue;
    const base={execution:reference(project,event.execution),at:timestamp(event.at || event.smartdev_at)};
    const item=event.item;
    let output;
    if (event.type==='smartdev.user') {
      output={type:event.type,text:cleanText(event.text,completeMessages ? Infinity : 30000),message:completeMessages ? reference(project,event.message) : undefined,attachments:Array.isArray(event.attachments)
        ? event.attachments.filter(file=>file && /^[0-9a-f-]{36}$/i.test(file.id || ''))
          .map(file=>({id:file.id,originalName:cleanText(file.originalName),image:typeof file.image==='string' && /^\.(?:png|jpg|webp)$/.test(file.image)?file.image:''}))
        : []};
    } else if (['item.updated','item.completed'].includes(event.type) && item?.type==='agent_message' && (event.type==='item.completed' || item.id)) {
      output={type:event.type,item:{type:'agent_message',id:reference(project,item.id),copyId:reference(project,item.id ? JSON.stringify([event.execution,item.id]) : line),text:cleanText(item.text,completeMessages ? Infinity : 30000)}};
    } else if (event.type==='smartdev.prompt_edit') {
      output={type:event.type,text:cleanText(event.text,completeMessages ? Infinity : 30000)};
    } else if (event.type==='smartdev.run_status' && statuses.has(event.state)) {
      output={type:event.type,state:event.state};
    } else if (event.type==='smartdev.auth' && event.status==='unavailable') {
      output={type:event.type,status:'unavailable',message:'The coding agent could not connect to its model. Check this project’s AI connection in Settings.'};
    } else if (event.type==='smartdev.result' && Number.isInteger(event.code)) {
      output={type:event.type,code:event.code===0?0:event.code===130?130:1,awaitingInput:event.awaitingInput===true,
        error:event.code && event.code!==130?'The coding agent could not finish this run. Review the conversation and project settings before retrying.':undefined};
    } else if (event.type==='smartdev.checkpoint') {
      output={type:'smartdev.progress',text:'Project changes saved'};
    } else if (item?.type==='command_execution' && typeof item.id==='string' &&
               ['item.started','item.updated','item.completed'].includes(event.type)) {
      const directory=typeof item.cwd==='string' && item.cwd.startsWith(project+'/')
        ? item.cwd.slice(project.length+1) : undefined;
      output={type:'smartdev.command',id:reference(project,item.id),text:activity(item.command),
        target:cleanText(target,100) || undefined,
        directory:directory && /^(?:repository|worktrees\/task-[a-f0-9-]{36})(?:\/[A-Za-z0-9_.-]+)*$/.test(directory)
          && !directory.split('/').includes('..') ? directory : undefined,
        state:event.type!=='item.completed' ? 'running' : item.exit_code===0 ? 'completed' : 'failed'};
    } else if (event.type==='item.started') {
      if (item?.type==='command_execution') output={type:'smartdev.progress',text:activity(item.command)};
      if (item?.type==='file_change') output={type:'smartdev.progress',text:'Updating project files'};
      if (item?.type==='mcp_tool_call') output={type:'smartdev.progress',text:'Working on the project'};
    } else if (event.type==='item.completed' && item?.type==='command_execution' && activity(item.command)==='Running tests'
        && Number.isInteger(item.exit_code)) {
      output={type:'smartdev.progress',text:item.exit_code===0?'Test command passed':'Test command failed. Review the project changes and test configuration.'};
    }
    if (output) visible.push(JSON.stringify({...base,...output}));
  }
  return visible.join('\n');
}

function publicPointer(pointer) {
  if (!pointer) return pointer;
  const view=({id,name,legacy,active_task_id})=>({id,name:cleanText(name),legacy,active_task_id});
  return {version:pointer.version,focusedTask:pointer.focusedTask,active:view(pointer.active),
    conversations:pointer.conversations.map(view)};
}

function publicWorkflow(state,project,excluded=[]) {
  const suppressed=new Set(excluded);
  const directQueue=(state.direct || []).filter(item=>item.status==='queued' && !suppressed.has(item.executionId))
    .map(item=>({id:reference(project,item.executionId),conversationId:item.conversationId,
      description:cleanText(item.prompt,30000),status:'queued'}));
  return {paused:state.paused,...(directQueue.length ? {directQueue} : {}),
    skillPromotions:(state.skillPromotions || []).filter(item=>!suppressed.has(item.executionId))
      .map(({id,objective,conversationId,name,description,filePath,previous,content,status})=>
        ({id:reference(project,id),objective,conversationId,name:cleanText(name),
          description:cleanText(description),filePath:cleanText(filePath),previous:cleanText(previous),
          content:cleanText(content),status})),
    items:state.items.filter(item=>!suppressed.has(item.executionId || item.id)).map(({id,objective,conversationId,description,status,independent,error})=>
      ({id,objective,conversationId,description:cleanText(description),status,independent,
        error:error?'This instruction could not finish. Review its task before retrying.':undefined})),
    decisions:state.decisions.filter(item=>!suppressed.has(item.executionId || item.id)).map(({id,objective,conversationId,question,status,problem})=>
      ({id:reference(project,id),objective,conversationId,question:cleanText(question),status,
        problem:problem?'Review the conversation before resuming this reply.':undefined}))};
}

function diagnosticSummary(text) {
  const counts={commands:0,edits:0,integrations:0,failed:0};
  for (const line of text.split('\n')) {
    let event;
    try { event=JSON.parse(line); } catch {continue;}
    if (event?.type==='item.completed') {
      if (event.item?.type==='command_execution') counts.commands++;
      if (event.item?.type==='file_change') counts.edits++;
      if (event.item?.type==='mcp_tool_call') counts.integrations++;
    }
    if (event?.type==='smartdev.result' && event.code && event.code!==130) counts.failed++;
  }
  return counts;
}

function diagnosticEvents(text) {
  return text.split('\n').flatMap(line=>{
    try {
      return [JSON.stringify(JSON.parse(line),(key,value)=>{
        if (/credential|secret|password|token|authorization|api_?key/i.test(key)) return '[credential removed]';
        return typeof value==='string' ? cleanText(value) : value;
      })];
    } catch { return []; }
  }).join('\n');
}

module.exports={reference,cleanText,publicTranscript,publicPointer,publicWorkflow,diagnosticSummary,diagnosticEvents,statusLabels};
