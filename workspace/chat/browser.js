const tree = document.getElementById('tree');
const objective = document.getElementById('objective');
const output = document.getElementById('output');
const statusText = document.getElementById('status');
const prompt = document.getElementById('prompt');
const run = document.getElementById('run');
const review = document.getElementById('review');
const taskState = document.getElementById('task-state');
const portalOrigin = 'https://smartdev.youkandoit.com';
const selected = new Map();
let pending = false;
let newTask = false;
const feedElement=output.parentElement;
let savedMainScroll;
function leaveNewTask() {
  newTask=false;
  feedElement.inert=false;
  document.body.classList.remove('new-task-open');
  updateCopyControls();
  if (savedMainScroll!==undefined) feedElement.scrollTop=savedMainScroll;
  savedMainScroll=undefined;
}
let pointerState;
let lastMainConversation;
const responseIcon=document.getElementById('copy-response-icon');
const copyHistory=document.getElementById('copy-history');
let copyPending=false, copyFeedbackTimer;
function updateCopyControls() {
  copyHistory.disabled=copyPending || newTask || !pointerState;
}
async function writeConversationClipboard(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (error) {
    if (error.name!=='NotAllowedError' || window.parent===window || !document.hasFocus()) throw error;
    const focused=document.activeElement;
    const input=document.createElement('textarea');
    input.className='sr-only';
    input.value=text;
    input.readOnly=true;
    let wrotePlainText=false;
    function plainText(event) {
      if (!event.clipboardData) return;
      event.clipboardData.setData('text/plain',text);
      event.preventDefault();
      wrotePlainText=true;
    }
    document.body.append(input);
    document.addEventListener('copy',plainText,true);
    try {
      input.focus({preventScroll:true});
      input.select();
      if (!document.execCommand('copy') || !wrotePlainText) throw error;
    } finally {
      document.removeEventListener('copy',plainText,true);
      input.remove();
      focused?.focus({preventScroll:true});
    }
  }
}
async function copyConversation(mode,responseId,button) {
  if (copyPending || newTask || !pointerState) return;
  const version=pointerState.version;
  const feedback=document.getElementById('copy-feedback');
  clearTimeout(copyFeedbackTimer);
  copyPending=true;
  updateCopyControls();
  feedback.textContent='';
  try {
    const {text}=await api('conversation-copy',responseId ? {mode,version,responseId} : {mode,version});
    if (newTask || pointerState?.version!==version) throw Error('Conversation changed. Try copying again.');
    if (!text) {
      feedback.textContent=mode==='response' ? 'Response unavailable' : 'No messages in the last 48 hours';
      return;
    }
    await writeConversationClipboard(text);
    feedback.textContent='Copied';
    if (button) {
      button.textContent='✓';
      setTimeout(()=>button.replaceChildren(responseIcon.content.cloneNode(true)),2500);
    }
  } catch {
    feedback.textContent='Could not copy conversation';
  } finally {
    copyPending=false;
    updateCopyControls();
    copyFeedbackTimer=setTimeout(()=>{ feedback.textContent=''; },2500);
  }
}
copyHistory.onclick=()=>void copyConversation('history');
let explicitTask=false;
let navigationIntent=0;
let pendingReply;
const replyContext=document.getElementById('reply-context');
const showReply=()=>{
  replyContext.hidden=!pendingReply;
  replyContext.querySelector('span').textContent=pendingReply ? 'Replying to: '+pendingReply.question : '';
};
replyContext.querySelector('button').onclick=()=>{
  pendingReply=undefined;
  showReply();
  restoreDraft();
};
async function focusTask(id) {
  pendingReply=undefined;
  showReply();
  if (!id && pointerState && pointerState.active.id===pointerState.active.active_task_id) {
    const main=pointerState.conversations.find(item=>item.id===lastMainConversation && item.id!==item.active_task_id)
      || pointerState.conversations.find(item=>item.legacy && item.id!==item.active_task_id)
      || pointerState.conversations.find(item=>item.id!==item.active_task_id);
    if (main) return changeConversation('select',{conversation_id:main.id});
  }
  const dedicated=id && pointerState?.conversations.find(item=>item.id===id && item.active_task_id===id);
  if (dedicated) return changeConversation('select',{conversation_id:dedicated.id});
  return changeConversation('focus',{task_id:id || null});
}

const savedConversations=document.getElementById('saved-conversations');
function setPointer(snapshot) {
  if (!snapshot || (pointerState && pointerState.version>snapshot.version)) return;
  const id=snapshot.active.id;
  const standalone=snapshot.conversations.filter(item=>item.id!==item.active_task_id);
  savedConversations.hidden=standalone.length<2;
  const views=savedConversations.querySelector('div');
  if (views.dataset.version!==String(snapshot.version)) {
    views.dataset.version=String(snapshot.version);
    views.replaceChildren(...standalone.map(item=>{
      const button=document.createElement('button');
      button.textContent=item.legacy?'Main':item.name;
      button.setAttribute('aria-current',String(item.id===id));
      button.onclick=()=>void changeConversation('select',{conversation_id:item.id});
      return button;
    }));
  }
  const changed=pointerState?.active.id!==id || pointerState?.focusedTask!==snapshot.focusedTask;
  pointerState=snapshot;
  updateCopyControls();
  if (snapshot.active.id!==snapshot.active.active_task_id) lastMainConversation=snapshot.active.id;
  if (changed) {
    if (pendingReply && (pendingReply.conversationId!==id || (pendingReply.objective || null)!==(snapshot.focusedTask || null))) pendingReply=undefined;
    showReply();
    restoreDraft();
  }
  if (!newTask && !explicitTask) objective.value=snapshot.focusedTask || '';
}
async function changeConversation(action, details={}) {
  const intent=++navigationIntent;
  try {
    const state=pointerState || await api('conversations');
    const snapshot=await api('conversations',{action,version:state.version,...details},'POST');
    if (intent!==navigationIntent) return false;
    setPointer(snapshot);
    explicitTask=false;
    leaveNewTask();
    prompt.placeholder='Message Codex';
    objective.value=pointerState?.focusedTask || '';
    restoreDraft();
    void refresh();
    return true;
  } catch(error) { if (intent===navigationIntent) { statusText.textContent=error.message; void refresh(); } return false; }
}
let polling = false;
let previouslyRunning = false;
let transcript = '';
let transcriptKey='';
let transcriptStart=0;
let tailStart=0;
let historyText='';
let retryPrompt = '';
const authPanel = document.getElementById('auth-panel');
const reconnect = document.getElementById('reconnect-codex');
const retry = document.getElementById('retry-codex');
reconnect.onclick=()=>notifyParent('manage-auth');
retry.onclick=()=>{
  if (run.disabled || !retryPrompt) return;
  prompt.value=retryPrompt;
  run.click();
};

function storageKey(suffix) { return 'smartdev-chat:' + location.pathname + ':' + suffix; }
function local(name) {
  try { return localStorage.getItem(storageKey(name)) || ''; } catch { return ''; }
}
function saveLocal(name, value) {
  try {
    if (value) localStorage.setItem(storageKey(name),value);
    else localStorage.removeItem(storageKey(name));
  } catch {}
}
function draftKey() {
  if (pendingReply) return 'answer:'+pendingReply.id;
  if (newTask) return 'new-task';
  return 'draft:'+(pointerState?.focusedTask || pointerState?.active.id || 'project');
}
function resizePrompt() {
  prompt.style.height='auto';
  prompt.style.height=prompt.scrollHeight+'px';
}
function restoreDraft() {
  prompt.value = local(draftKey()) || (!newTask && !pendingReply && !pointerState?.focusedTask && pointerState?.active.legacy ? local('project-draft') : '');
  resizePrompt();
  window.dispatchEvent(new CustomEvent('smartdev-attachment-context',{detail:draftKey()}));
  if (pointerState) void restoreUploadedDraft();
}
prompt.addEventListener('input',()=>{
  resizePrompt();
  const recognizableCredential=/-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(?:gh[pousr]_|github_pat_|sk-(?:proj-|svcacct-)?|hvs\.)[A-Za-z0-9_-]{12,}/;
  saveLocal(draftKey(),recognizableCredential.test(prompt.value) ? '' : prompt.value);
});
prompt.addEventListener('keydown',event=>{
  if (event.key==='Enter' && !event.shiftKey && !event.isComposing && !window.matchMedia?.('(pointer:coarse)').matches) {
    event.preventDefault();
    if (!run.disabled) run.click();
  }
});
function remember(scope,id) {
  selected.set(scope,id);
  saveLocal('task:'+scope,id);
}
function remembered(scope) {
  return selected.get(scope) ?? (local('task:'+scope) || /^worktrees\/task-(.+)$/.exec(scope)?.[1] || '');
}
function notifyParent(type,data={}) {
  window.parent.postMessage({source:'smartdev-chat',type,...data},portalOrigin);
}

function renderMessage(element,text) {
  function inline(parent,content) {
    const matches=content.matchAll(/`([^`]+)`|\*\*([^*]+)\*\*|\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g);
    let cursor=0;
    for (const match of matches) {
      parent.append(content.slice(cursor,match.index));
      const node=document.createElement(match[1] ? 'code' : match[2] ? 'strong' : 'a');
      node.textContent=match[1] || match[2] || match[3];
      if (match[4]) {node.href=match[4];node.target='_blank';node.rel='noopener noreferrer';}
      parent.append(node);
      cursor=match.index+match[0].length;
    }
    parent.append(content.slice(cursor));
  }
  text.split(/```/).forEach((block,index)=>{
    if (index%2) {
      const pre=document.createElement('pre');
      pre.textContent=block.replace(/^[\w+-]*\n/,'').trimEnd();
      element.append(pre);
    } else {
      for (const paragraph of block.trim().split(/\n\s*\n/)) {
        if (!paragraph) continue;
        const list=paragraph.split('\n');
        if (list.every(line=>/^[-*] /.test(line))) {
          const ul=document.createElement('ul');
          for(const entry of list) {
            const li=document.createElement('li');
            inline(li,entry.slice(2));
            ul.append(li);
          }
          element.append(ul);
        } else {
          const lines=paragraph.split('\n');
          const heading=/^(#{1,6})[ \t]+(.+)$/.exec(lines[0]);
          if (heading) {
            const title=document.createElement('h'+heading[1].length);
            inline(title,heading[2]);
            element.append(title);
            lines.shift();
          }
          if (lines.length) {
            const p=document.createElement('p');
            inline(p,lines.join('\n'));
            element.append(p);
          }
        }
      }
    }
  });
}
function responseCopyButton(copyId) {
  const button=document.createElement('button');
  button.className='copy-agent-response';
  button.type='button';
  button.title='Copy this Agent response';
  button.setAttribute('aria-label',button.title);
  button.append(responseIcon.content.cloneNode(true));
  button.onclick=()=>void copyConversation('response',copyId,button);
  return button;
}

function timeline(text, start, key, reset) {
  const feed=output.parentElement;
  const pinned=reset || feed.scrollHeight-feed.scrollTop-feed.clientHeight<40;
  const scrollTop=feed.scrollTop;
  const openActivities=new Set(reset ? [] : Array.from(output.querySelectorAll('details[data-activity][open]'), node=>node.dataset.activity));
  const focusedActivity=reset ? undefined : document.activeElement?.parentElement?.dataset.activity;
  const fragment=document.createDocumentFragment();
  const {entries,auth,lastRequest,result,latest,actions,runStates}=conversation(text,start,{main:!pointerState?.focusedTask});
  executionView={latest,actions,result,runStates};
  retryPrompt=lastRequest;
  function element(tag,className,message='') {
    const node=document.createElement(tag);
    node.className=className;
    node.textContent=message;
    return node;
  }
  if (start>0) {
    const earlier=element('button','chat-earlier','Load earlier execution');
    earlier.onclick=async()=>{
      earlier.disabled=true;
      try {
        const older=await api('task',{tree:tree.value,objective:objective.value,before:start,project:'1',version:pointerState?.version || ''});
        if (key!==draftKey() || start!==transcriptStart) return;
        if (older.end!==start || older.start>=start) throw Error('Earlier execution could not be loaded.');
        historyText=older.output+historyText;
        transcriptStart=older.start;
        const height=feed.scrollHeight;
        const top=feed.scrollTop;
        timeline(historyText+transcript,transcriptStart,key,false);
        feed.scrollTop=top+feed.scrollHeight-height;
      } catch(error) { statusText.textContent=error.message; earlier.disabled=false; }
    };
    fragment.append(earlier);
  }
  for (const entry of entries) {
    if (entry.type==='progress') {
      fragment.append(element('p','chat-progress',entry.message));
    } else if (entry.type==='technical' || entry.type==='checkpoint') {
      const details=element('details','chat-actions');
      details.dataset.activity=entry.id;
      details.open=openActivities.has(entry.id);
      details.append(element('summary','',entry.type==='checkpoint'?'Checkpoint':'Show more · diagnostics'));
      if (entry.type==='checkpoint') details.append(element('p','',entry.message));
      for (const action of entry.actions || []) {
        details.append(element('strong','',action.label),element('pre','',action.data));
      }
      fragment.append(details);
    } else {
      const card=element('div','chat-event '+entry.type);
      const elapsed=entry.at && (Date.now()-new Date(entry.at).getTime())/1000;
      const relative=Number.isFinite(elapsed) ? (elapsed<60?'Just now':elapsed<3600?Math.floor(elapsed/60)+' min ago':new Date(entry.at).toLocaleString()) : '';
      const name=entry.type==='user'?'You':entry.type==='question'?'Codex question':'Codex';
      const messageHeader=element('div','chat-message-header');
      messageHeader.append(element('strong','',name+(relative?' · '+relative:'')));
      if (entry.type==='agent' && entry.copyId) messageHeader.append(responseCopyButton(entry.copyId));
      card.append(messageHeader);
      const prose=element('div','chat-prose');
      if (entry.type==='agent') {
        if (entry.message.length>520) {
          const details=element('details','chat-long-response');
          details.dataset.activity='response:'+entry.id;
          details.open=openActivities.has(details.dataset.activity);
          const preview=element('p','chat-response-preview',entry.message.slice(0,420).trim()+'…');
          renderMessage(prose,entry.message);
          details.append(element('summary','','Show more'),prose);
          card.classList.add('has-long-response');
          card.append(preview,details);
        } else { renderMessage(prose,entry.message); card.append(prose); }
      } else {
        const queued=(workflowState?.state.directQueue || []).find(run=>run.id===entry.execution);
        prose.textContent=queued?.description ?? entry.message;
        card.append(prose);
      }
      for (const file of entry.attachments || []) {
        const link=element('a','chat-attachment',file.originalName);
        const instructions=/^Task instructions - [0-9a-f-]{36}\.md$/i.test(file.originalName);
        link.href='attachment?'+new URLSearchParams(instructions ? {id:file.id,view:'1'} : {id:file.id});
        if (instructions) {
          link.target='_blank';
          link.rel='noopener';
        }
        card.append(link);
      }
      if (entry.type==='user') {
        const run=runStates.get(entry.execution);
        if (run?.state==='cancelled') card.append(element('small','inline-run-status','Removed from queue'));
        if (run?.state==='failed' && run.error) card.append(element('small','inline-run-status has-failure',run.error));
      }
      fragment.append(card);
      if (entry.activity) {
        const {id,state,label,actions,commands,target}=entry.activity;
        const states={thinking:'Thinking',running:'Running',queued:'Queued',awaiting_input:'Input requested',
          completed:'Completed',failed:'Failed',stopped:'Stopped',cancelled:'Cancelled',redirected:'Continued'};
        const details=element('details','chat-actions');
        details.dataset.activity=id;
        details.open=openActivities.has(id);
        const active=[...commands.values()].filter(command=>command.state==='running').length;
        const completed=[...commands.values()].filter(command=>command.state!=='running').length;
        const commandStatus=state==='running' && active
          ? 'Running '+active+(active===1?' command':' commands')+(target?' in '+target:'')+' · '+completed+' completed'
          : label || states[state] || 'Working';
        const duration=Math.round((Date.parse(entry.activity.endedAt)-Date.parse(entry.activity.startedAt))/1000);
        const elapsedTime=Number.isFinite(duration) && duration>=0
          ? ' · '+(duration<60?duration+'s':Math.floor(duration/60)+'m '+duration%60+'s') : '';
        details.append(element('summary','',commandStatus+elapsedTime+(actions.length?' · Show more':'')));
        if (actions.length) {
          const list=element('ol','');
          for (const action of actions) list.append(element('li','',typeof action==='string' ? action :
            [action.text,action.directory,action.state].filter(Boolean).join(' · ')));
          details.append(list);
        }
        fragment.append(details);
      }
    }
  }
  if (!entries.length) fragment.append(element('p','chat-empty','No messages yet.'));
  if (result===130) fragment.append(element('p','chat-result','Execution stopped. Send a message to continue this task.'));
  else if (Number.isInteger(result) && result!==0 && auth?.status!=='unavailable')
    fragment.append(element('p','chat-result has-failure','Codex could not finish this run. Review the conversation or retry.'));
  authPanel.hidden=auth?.status!=='unavailable';
  document.getElementById('auth-message').textContent=auth?.message || '';
  reconnect.hidden=authPanel.hidden;
  retry.hidden=authPanel.hidden;
  output.replaceChildren(fragment);
  if (focusedActivity) output.querySelector('details[data-activity="'+CSS.escape(focusedActivity)+'"] summary')?.focus({preventScroll:true});
  feed.scrollTop=pinned ? feed.scrollHeight : scrollTop;
}
async function api(endpoint,data,method='GET') {
  const response=await fetch(endpoint+(method==='GET' && data ? '?'+new URLSearchParams(data) : ''),
    method==='GET' ? {} : {method,headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  const result=await response.json();
  if (!response.ok) throw Error(result.error || 'Workspace request failed.');
  return result;
}
window.smartdevAttachmentDraft = ids => saveLocal('attachments:'+draftKey(),JSON.stringify(ids));
window.smartdevUpload = async file => {
  if (file.data==='') throw Error('The attachment is empty.');
  return api('uploads',file,'POST');
};
async function restoreUploadedDraft() {
  try {
    const key=draftKey();
    const legacy=pointerState?.active.legacy && !pointerState.focusedTask && !newTask && !pendingReply;
    const ids=JSON.parse(local('attachments:'+key) || (legacy ? local('attachment-draft') : '') || '[]').slice(0,8);
    if (!ids.length) return;
    const records=await api('uploads');
    const files=await Promise.all(ids.map(async id=>{
      const record=records.find(file=>file.id===id);
      if (!record) return;
      const response=await fetch('attachment?'+new URLSearchParams({id}));
      if (!response.ok) return;
      const type={'.png':'image/png','.jpg':'image/jpeg','.webp':'image/webp'}[record.image] || record.mimeType || '';
      const file=new File([await response.blob()],record.displayName || record.originalName,{type});
      file.smartdevUpload=Promise.resolve(record);
      file.smartdevId=id;
      return file;
    }));
    if (key===draftKey()) window.dispatchEvent(new CustomEvent('smartdev-upload-draft',{detail:files.filter(Boolean)}));
  } catch(error) { statusText.textContent=error.message; }
}
async function showUploads() {
  document.body.classList.add('uploads-view');
  document.getElementById('upload-explorer').hidden=false;
  const search=document.getElementById('upload-search');
  const list=document.getElementById('project-uploads');
  const status=document.getElementById('upload-status');
  let records=[];
  const render=()=>list.replaceChildren(...records.filter(file=>[file.displayName,file.originalName,...(file.aliases || [])].filter(Boolean)
    .some(name=>name.toLowerCase().includes(search.value.toLowerCase()))).map(file=>{
    const row=document.createElement('li');
    const link=document.createElement('a');
    link.href='attachment?'+new URLSearchParams({id:file.id});
    link.textContent=file.displayName || file.originalName;
    row.append(link);
    if (file.version>1) {
      const version=document.createElement('small');
      version.textContent=' · Version '+file.version;
      row.append(version);
    }
    for (const action of ['Preview','Rename','Delete']) {
      if (action==='Preview' && !file.image) continue;
      const button=document.createElement('button');
      button.textContent=action;
      button.onclick=async()=>{
        try {
          if (action==='Preview') {
            const response=await fetch(link.href);
            if (!response.ok) throw Error('Attachment unavailable.');
            const mime={'.png':'image/png','.jpg':'image/jpeg','.webp':'image/webp'}[file.image];
            imagePreview(new Blob([await response.arrayBuffer()],{type:mime}),link.textContent);
            return;
          }
          const name=action==='Rename' ? window.prompt('Rename upload',link.textContent) : null;
          if (action==='Rename' ? !name : !window.confirm('Delete '+link.textContent+'?')) return;
          await api('manage-upload',{id:file.id,action:action.toLowerCase(),name},'POST');
          await refreshUploads();
        } catch(error) { status.textContent=error.message; }
      };
      row.append(button);
    }
    return row;
  }));
  async function refreshUploads() {
    try {
      records=await api('uploads');
      if (!list.contains(document.activeElement)) render();
      status.textContent='';
    } catch(error) { status.textContent=error.message; }
  }
  search.oninput=render;
  await refreshUploads();
  setInterval(refreshUploads,3000);
}
async function showHistory() {
  document.body.classList.add('history-view');
  document.getElementById('checkpoint-history').hidden=false;
  const from=document.getElementById('history-from');
  const to=document.getElementById('history-to');
  const status=document.getElementById('history-status');
  const diff=document.getElementById('history-diff');
  const file=document.getElementById('history-file');
  const compare=document.getElementById('compare-versions');
  const restore=document.getElementById('restore-version');
  let reviewed;
  function clear() {
    reviewed=undefined;
    restore.disabled=true;
    diff.textContent='';
  }
  [from,to,file].forEach(node=>node.addEventListener('input',clear));
  async function load() {
    const rows=await api('history');
    const options=()=>rows.map(row=>new Option(
      row.sha.slice(0,10)+' · '+row.phase+' · '+row.branch+' · '+new Date(row.at).toLocaleString(),row.id));
    from.replaceChildren(...options());
    to.replaceChildren(...options());
    if (rows.length>1) to.selectedIndex=1;
    compare.disabled=rows.length<2;
    if (!rows.length) status.textContent='No checkpoints recorded.';
    return rows;
  }
  let rows;
  try { rows=await load(); } catch(error) { status.textContent=error.message; return; }
  compare.onclick=async()=>{
    clear();
    const pair={from:from.value,to:to.value,filename:file.value.trim() || undefined};
    try {
      const result=await api('compare',pair.filename ? pair : {from:pair.from,to:pair.to});
      if (from.value!==pair.from || to.value!==pair.to || (file.value.trim() || undefined)!==pair.filename) return;
      const left=rows.find(row=>row.id===pair.from), right=rows.find(row=>row.id===pair.to);
      diff.textContent=result.diff || 'No changes between these versions.';
      status.textContent='Task '+right.objective+' · '+right.verification+
        (right.excluded?.length ? ' · excluded files: '+right.excluded.join(', ') : '')+
        (result.truncated ? ' · diff shortened' : '');
      if (result.diff && !result.truncated && left.worktree===right.worktree && left.branch===right.branch &&
          !left.excluded?.length && !right.excluded?.length) {
        reviewed=pair;
        restore.disabled=false;
      }
    } catch(error) { status.textContent=error.message; }
  };
  restore.onclick=async()=>{
    if (!reviewed || !window.confirm('Apply the reviewed changes to this task worktree?')) return;
    const pair=reviewed;
    restore.disabled=true;
    try {
      const result=await api('restore',{...pair,confirm:true},'POST');
      clear();
      rows=await load();
      status.textContent='Restored checkpoint '+result.restored+' · recovery '+result.recovery;
    } catch(error) { status.textContent=error.message; restore.disabled=!reviewed; }
  };
}
let workflowState;
let taskLabels={};
let queueSubmission;
let executionView;
async function changeWorkflow(action, extra={}) {
  if (!pointerState?.active.id) throw Error('Select a conversation.');
  return api('workflow',{action,conversationId:pointerState.active.id,...extra},'POST');
}
function workflowButton(label, action) {
  const button=document.createElement('button');
  button.type='button';
  button.textContent=label;
  button.onclick=async()=>{
    button.disabled=true;
    try { displayWorkflow(await action()); }
    catch(error) { statusText.textContent=error.message; }
    finally { button.disabled=false; }
  };
  return button;
}
function displayWorkflow(state) {
  const focused=pointerState?.focusedTask;
  const visibleQuestions=[...output.querySelectorAll('.chat-event.question .chat-prose')]
    .map(node=>node.textContent.trim());
  const key=JSON.stringify([pointerState?.active.id,focused,state,taskLabels,visibleQuestions]);
  if (workflowState?.key===key) return;
  workflowState={key,state};
  const decisions=document.getElementById('decision-list');
  decisions.replaceChildren();
  for (const item of state.decisions.filter(item=>item.conversationId===pointerState?.active.id
      && (item.objective || null)===(focused || null)
      && ['pending','answering','needs_review'].includes(item.status))) {
    const form=document.createElement('form');
    const question=document.createElement('p');
    question.textContent=item.question;
    form.dataset.decisionId=item.id;
    const label=document.createElement('strong');
    label.textContent=(item.status==='answering' ? 'Reply in progress · ' : 'Waiting for input · ')+(item.objective ? taskLabels[item.objective] || 'Task '+item.objective.slice(0,8) : 'Main');
    form.append(label);
    const send=workflowButton('Reply',async()=>{
      if (item.objective) { if (!await focusTask(item.objective)) return state; }
      else if (pointerState?.active.id!==item.conversationId || pointerState?.focusedTask) {
        if (!await changeConversation('select',{conversation_id:item.conversationId})) return state;
      }
      if ((pointerState?.focusedTask || null)!==(item.objective || null))
        throw Error('Could not focus the task for this question.');
      pendingReply=item;
      newTask=false;
      showReply();
      restoreDraft();
      decisions.querySelector('[data-decision-id="'+CSS.escape(item.id)+'"] p')?.scrollIntoView({block:'nearest'});
      prompt.focus();
      return state;
    });
    if (!visibleQuestions.includes(item.question.trim())) form.append(question);
    if (item.status==='needs_review') {
      const review=document.createElement('small');
      review.textContent=item.problem;
      form.append(review);
    } else if (item.status==='answering') {
      const progress=document.createElement('small');
      progress.textContent='Codex is processing the reply.';
      form.append(progress);
    } else form.append(send);
    decisions.append(form);
  }
  for (const item of (state.skillPromotions || []).filter(item=>item.status==='pending' &&
    item.conversationId===pointerState?.active.id &&
    (item.objective || null)===(pointerState?.focusedTask || null))) {
    const card=document.createElement('section');
    const heading=document.createElement('strong');
    heading.textContent='Save Skill · '+item.name;
    const description=document.createElement('p');
    description.textContent=item.description;
    const file=document.createElement('code');
    file.textContent=item.filePath;
    const changes=document.createElement('details');
    const summary=document.createElement('summary');
    summary.textContent='Proposed changes';
    changes.append(summary);
    for (const [label,content] of [['Previous draft',item.previous],['Proposed draft',item.content]]) {
      if (!content) continue;
      const title=document.createElement('p');
      title.textContent=label;
      const preview=document.createElement('pre');
      preview.textContent=content;
      changes.append(title,preview);
    }
    const save=document.createElement('button');
    save.type='button';
    save.textContent='Save Skill';
    save.disabled=true;
    const unavailable=document.createElement('p');
    unavailable.textContent='Shared-library publishing is unavailable.';
    card.append(heading,description,file,changes,unavailable,save,
      workflowButton('Not now',()=>changeWorkflow('defer_skill_promotion',{id:item.id})));
    decisions.append(card);
  }
  const decisionSection=document.getElementById('decisions');
  if (!decisions.childElementCount) decisionSection.open=false;
  decisionSection.hidden=!decisions.childElementCount;
  document.getElementById('decision-count').textContent='('+decisions.childElementCount+')';
  const list=document.getElementById('queue-list');
  list.replaceChildren();
  const items=focused
    ? state.items.filter(item=>item.objective===focused && item.status!=='completed')
    : (state.directQueue || []).filter(item=>item.conversationId===pointerState?.active.id);
  const wasHidden=document.getElementById('execution-queue').hidden;
  const retained=document.getElementById('legacy-main-queue');
  const saved=state.items.filter(item=>!item.objective && item.status!=='completed' && item.conversationId===pointerState?.active.id);
  retained.hidden=!!focused || !saved.length;
  retained.querySelector('ul').replaceChildren(...saved.map(item=>{
    const label=document.createElement('li');
    label.textContent=item.description+' · Retained without execution';
    return label;
  }));
  document.getElementById('open-queue').hidden=!focused;
  const queue=document.getElementById('execution-queue');
  queue.hidden=!items.length && (!focused || (!state.paused && !queue.open));
  if (items.some(item=>item.status==='queued') && wasHidden) queue.open=true;
  if (!focused && !items.length) queue.open=false;
  document.getElementById('queue-form').parentElement.hidden=!focused;
  document.getElementById('queue-count').textContent=items.length ? '('+items.length+')' : '';
  for (const [index,item] of items.entries()) {
    const row=document.createElement('li');
    const description=document.createElement('span');
    description.textContent=(item.status==='queued' ? (index+1)+'. ' : '')+item.description+(item.error?' · '+item.error:'');
    row.dataset.status=item.status;
    row.append(description);
    if (item.objective) row.append(workflowButton('Task',()=>{
      void focusTask(item.objective);
      return state;
    }));
    if (item.objective) {
      const associated=document.createElement('small');
      associated.className='queue-task-label';
      associated.textContent=taskLabels[item.objective] || 'Task '+item.objective.slice(0,8);
      row.append(associated);
    }
    if (item.status==='queued') {
      const previous=items[items.indexOf(item)-1];
      if (previous?.status==='queued' && ((previous.conversationId!==item.conversationId &&
          (!item.objective || previous.objective!==item.objective)) || (previous.independent && item.independent)))
        row.append(workflowButton('Move up',()=>changeWorkflow('up',{id:item.id})));
    }
    if (item.status==='queued') {
      const editor=document.createElement('details');
      const title=document.createElement('summary');
      title.textContent='Edit';
      const input=document.createElement('textarea');
      input.setAttribute('aria-label','Edit queued prompt');
      input.maxLength=30000;
      input.value=item.description;
      editor.append(title,input,workflowButton('Save',()=>changeWorkflow(focused?'edit':'edit_direct',
        {id:item.id,description:input.value})));
      row.append(editor);
    }
    if (['queued','failed'].includes(item.status))
      row.append(workflowButton('Remove',()=>changeWorkflow(focused?'remove':'remove_direct',{id:item.id})));
    list.append(row);
  }
  const toggle=document.getElementById('queue-toggle');
  toggle.hidden=!focused || (!state.paused && !items.some(item=>item.status==='queued' || item.status==='running'));
  toggle.textContent=state.paused?'Resume':'Pause';
  toggle.onclick=async()=>{
    try { displayWorkflow(await changeWorkflow(state.paused?'resume':'pause')); }
    catch(error) { statusText.textContent=error.message; }
  };
}
const queuePanel=document.getElementById('execution-queue');
queuePanel.ontoggle=()=>{
  if (!queuePanel.open && !workflowState?.state.paused && !document.getElementById('queue-list').childElementCount)
    queuePanel.hidden=true;
};
document.getElementById('open-queue').onclick=()=>{
  if (!pointerState?.focusedTask) return;
  queuePanel.hidden=false;
  queuePanel.open=true;
  document.getElementById('queue-form').closest('details').open=true;
  document.querySelector('.chat-scope').open=false;
  document.getElementById('queue-instruction').focus();
};
document.getElementById('queue-form').onsubmit=async event=>{
  event.preventDefault();
  const input=document.getElementById('queue-instruction');
  const button=event.currentTarget.querySelector('[type=submit],button');
  const description=input.value;
  const independent=document.getElementById('queue-independent').checked;
  const key=JSON.stringify([pointerState?.active.id,pointerState?.focusedTask,description,independent]);
  if (queueSubmission?.key!==key) queueSubmission={key,id:crypto.randomUUID()};
  button.disabled=true;
  try {
    displayWorkflow(await changeWorkflow('add',{id:queueSubmission.id,description,independent,objective:pointerState?.focusedTask}));
    queueSubmission=undefined;
    if (input.value===description) input.value='';
  } catch(error) { statusText.textContent=error.message; }
  finally { button.disabled=false; }
};
function buttons(running, runs=[]) {
  run.disabled=pending || !tree.value;
  const cancel=document.getElementById('cancel');
  cancel.disabled=!running;
  cancel.hidden=!running;
  const {actions=[],result}=executionView || {};
  const action=actions.findLast(item=>item.state==='running');
  const decision=workflowState?.state.decisions.some(item=>item.status==='pending' &&
    (item.objective ? item.objective===pointerState?.focusedTask : !pointerState?.focusedTask && item.conversationId===pointerState?.active.id));
  const resuming=workflowState?.state.decisions.some(item=>item.status==='answering' && !item.objective &&
    !pointerState?.focusedTask && item.conversationId===pointerState?.active.id);
  const observed=runs.find(run=>run.execution===executionView?.latest?.execution) || runs[0];
  const status=running && observed ? (observed.state[0].toUpperCase()+observed.state.slice(1)) : running ? action ? (action.type==='command_execution' && /\b(test|pytest|playwright|unittest)\b/i.test(action.command) ? 'Testing' : 'Executing') : 'Thinking'
    : resuming ? 'Resuming Codex…' : decision ? 'Waiting for input' : result===0 ? 'Turn completed' : result===130 ? 'Stopped' : result ? 'Failed' : 'Idle';
  const waiting=[...(executionView?.runStates || new Map()).values()].some(run=>run.state==='queued');
  document.getElementById('activity-indicator').hidden=!running;
  document.getElementById('run-label').textContent='Codex · '+(!running && !decision && waiting?'Next prompt queued':status);

  review.disabled=!objective.value;
}
function selectTree() {
  objective.value=remembered(tree.value);
  saveLocal('selected-tree',tree.value);
  buttons(false);
  previouslyRunning=false;
  restoreDraft();
  void refresh();
}
tree.onchange=selectTree;
objective.onchange=()=>{
  explicitTask=true;
  objective.value=objective.value.trim();
  remember(tree.value,objective.value);
  restoreDraft();
  buttons(previouslyRunning);
  void refresh();
};
function beginTask() {
  if (pending) return;
  navigationIntent++;
  pendingReply=undefined;
  showReply();
  if (!newTask) savedMainScroll=feedElement.scrollTop;
  newTask=true;
  feedElement.inert=true;
  document.body.classList.add('new-task-open');
  updateCopyControls();
  prompt.placeholder='Describe the new task';

  if ([...tree.options].some(option=>option.value==='repository')) tree.value='repository';
  objective.value='';
  remember(tree.value,'');
  saveLocal('selected-tree',tree.value);
  taskState.dataset.state='';
  const label=document.createElement('small');
  label.textContent='New task';
  taskState.replaceChildren(label);
  document.getElementById('task-details').hidden=true;
  restoreDraft();
  buttons(false);
  previouslyRunning=false;
  prompt.focus();
  void refresh();
};
review.onclick=async()=>{
  try {
    const result=await api('review',{tree:tree.value,objective:objective.value});
    notifyParent('review',{review:result});
    statusText.textContent='Git review opened in the workspace.';
  } catch(error) { statusText.textContent=error.message; }
};
window.addEventListener('message',event=>{
  if (event.source!==window.parent || event.origin!==portalOrigin || event.data?.source!=='smartdev-portal') return;
  if (event.data.type==='focus-task') { if (!pending) void focusTask(event.data.id); return; }
  if (event.data.type==='new-task') { beginTask(); return; }
  if (event.data.theme==='dark' || event.data.theme==='light') document.documentElement.dataset.theme=event.data.theme;
  const task=event.data.task;
  const current=task && task.id===objective.value ? task : null;
  if (newTask) return;
  const stateKey=JSON.stringify([current,pointerState?.active.id,pointerState?.active.name]);
  if (taskState.dataset.state===stateKey) return;
  taskState.dataset.state=stateKey;
  const context=document.createElement('small');
  context.textContent=current ? 'Active task' : 'Project conversation';
  if (current?.status) context.dataset.status=current.status;
  const heading=document.createElement(current ? 'button' : 'strong');
  if (current) {
    heading.type='button';
    heading.onclick=()=>notifyParent('open-task',{id:current.id});
  }
  heading.textContent=current?.title || pointerState?.active.name && !pointerState.active.legacy && pointerState.active.name || 'Main';
  heading.title=heading.textContent;
  taskState.replaceChildren(context,heading);
  const details=document.getElementById('task-details');
  details.hidden=!current;
  details.onclick=()=>current && notifyParent('open-task',{id:current.id});
});
async function refresh() {
  if (pending || polling || !tree.value) return;
  polling=true;
  const scope=tree.value, id=objective.value;
  try {
    const [task,workflow]=await Promise.all([api('task',{tree:scope,objective:id,project:'1'}),api('workflow')]);
    if (scope!==tree.value || id!==objective.value) return;
    if (pointerState && task.conversation?.version<pointerState.version) return;
    const oldPointer=pointerState;
    setPointer(task.conversation);
    if (!newTask && (task.objective!==oldPointer?.focusedTask || task.conversation?.active.id!==oldPointer?.active.id || (task.objective && task.tree!==tree.value))) {
      const target=task.objective ? task.tree : 'repository';
      if (target && [...tree.options].some(option=>option.value===target)) tree.value=target;
      objective.value=task.objective || '';
      if (task.objective) remember(tree.value,task.objective);
    }
    const key=draftKey();
    const start=task.start || 0;
    const reset=key!==transcriptKey;
    if (reset || start!==tailStart) {
      historyText='';
      transcriptStart=start;
    }
    if (reset || task.output!==transcript || start!==tailStart) {
      transcriptKey=key;
      tailStart=start;
      transcript=task.output;
      timeline(historyText+transcript,transcriptStart,key,reset);
    }
    taskLabels=task.taskLabels || {};
    displayWorkflow(workflow);
    buttons(task.running,task.runs);
    previouslyRunning=task.running;
    notifyParent('task',{id:objective.value,focus:pointerState?.focusedTask || null,
      statuses:{running:(task.projectRuns || []).map(run=>run.objective),
        waiting:workflow.decisions.filter(item=>item.status==='pending').map(item=>item.objective).filter(Boolean),
        queued:workflow.items.filter(item=>item.status==='queued').map(item=>item.objective).filter(Boolean),paused:workflow.paused}});
    if (pendingReply && !workflow.decisions.some(item=>item.id===pendingReply.id && item.status==='pending')) {
      pendingReply=undefined;
      showReply();
      restoreDraft();
    }
  } catch(error) { statusText.textContent=error.message; }
  finally { polling=false; }
}
async function loadTrees() {
  try {
    const [list,relationships]=await Promise.all([api('trees'),api('relationships')]);
    notifyParent('relationships',{relationships});
    const first=!tree.value;
    const next=tree.value || local('selected-tree');
    tree.replaceChildren(...list.map(value=>new Option(value,value)));
    if (list.includes(next)) tree.value=next;
    if ((first || next!==tree.value) && tree.value) selectTree();
  } catch(error) { statusText.textContent=error.message; }
}
window.acquireVsCodeApi=()=>({postMessage:async message=>{
  if (message.type==='ready' || (message.type==='run' && pending)) return;
  try {
    const scope=tree.value;
    if (!scope) throw Error('Select a worktree.');
    if (message.type==='run') {
      pending=true;
      const key=draftKey();
      const creating=newTask;
      if (!creating && pendingReply) {
        const decision=pendingReply;
        await api('workflow',{action:'answer',id:decision.id,conversationId:decision.conversationId,
          description:message.prompt,files:message.files},'POST');
        window.dispatchEvent(new Event('smartdev-sent'));
        requestAnimationFrame(resizePrompt);
        pendingReply=undefined;
        saveLocal(key,'');
        showReply();
        restoreDraft();
        return;
      }
      const conversationId=pointerState?.active.id || (await api('conversations')).active.id;
      const submittedTask=creating ? undefined : pointerState?.focusedTask || undefined;
      const messageIdentity=JSON.stringify([creating ? null : conversationId,submittedTask,creating,message.title,message.prompt,message.files]);
      const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(messageIdentity));
      const fingerprint=Array.from(new Uint8Array(digest),byte=>byte.toString(16).padStart(2,'0')).join('');
      const requestKey='submission:'+fingerprint;
      const requestId=local(requestKey) || crypto.randomUUID();
      saveLocal(requestKey,requestId);
      const anticipatesReply=!submittedTask && !creating && workflowState?.state.decisions.some(item=>
        !item.objective && item.status==='pending' && item.conversationId===conversationId);
      if (anticipatesReply) document.getElementById('run-label').textContent='Resuming Codex…';
      const data=await api('run',{...message,objective:submittedTask,tree:scope,newTask:creating,explicitTask:!!submittedTask,conversationId,requestId},'POST');
      if (data.status==='resuming') document.getElementById('run-label').textContent='Resuming Codex…';
      saveLocal(requestKey,'');
      explicitTask=false;
      const created=creating;
      leaveNewTask();
      prompt.placeholder='Message Codex';
      if (created && data.objective) {
        for (let i=0;i<8;i++) {
          if (await changeConversation('focus',{task_id:data.objective})) break;
          await new Promise(resolve=>setTimeout(resolve,300));
        }
      }
      if (data.parentTaskId) notifyParent('relationships',{relationships:[data]});
      window.dispatchEvent(new Event('smartdev-sent'));
        requestAnimationFrame(resizePrompt);
      if (data.objective) remember(data.tree,data.objective);
      saveLocal(key,'');
      if (!created && !submittedTask && pointerState?.active.legacy) saveLocal('project-draft','');
      if (tree.value===scope) {
        if (![...tree.options].some(option=>option.value===data.tree)) tree.add(new Option(data.tree,data.tree));
        tree.value=data.tree;
        if (data.objective) objective.value=data.objective;
        saveLocal('selected-tree',data.tree);
        restoreDraft();
        previouslyRunning=true;
      }
    } else if (message.type==='new') {
      await changeConversation('new');
    } else {
      await api(message.type==='cancel'?'cancel':'reset',{tree:scope,objective:objective.value,conversationId:pointerState?.active.id},'POST');
      if (message.type==='new') statusText.textContent='Started a fresh Codex session for this objective.';
    }
  } catch(error) { statusText.textContent=error.message; buttons(false); }
  finally { pending=false; void refresh(); }
}});
if (new URLSearchParams(location.search).has('uploads')) void showUploads();
else if (new URLSearchParams(location.search).has('history')) void showHistory();
else {
  void loadTrees();
  window.addEventListener('DOMContentLoaded',()=>void restoreUploadedDraft(),{once:true});
  setInterval(()=>void loadTrees(),15000);
  const events=new EventSource('events');
  events.onmessage=()=>void refresh();
  setInterval(()=>void refresh(),3000);
}
