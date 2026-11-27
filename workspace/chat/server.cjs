const http = require('node:http');
const fs = require('node:fs/promises');
const { spawn, execFile } = require('node:child_process');
const { promisify } = require('node:util');
const { createHash, randomUUID } = require('node:crypto');
const path = require('node:path');
const os = require('node:os');
const { createInterface } = require('node:readline');
const directPolicy = require('./direct-runs.cjs');
const presentation = require('./public-events.cjs');
const {conversationCopy}=require('./conversation-copy.cjs');
const {assetStore} = require('./project-assets.cjs');
const git = promisify(execFile);
const project = path.join('/projects', process.env.PROJECT_NAME);
const root = path.join(project, 'repository');
const storage = path.join(os.homedir(), '.local', 'state', 'command-web-chat');
const ids = /^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$/i;
const active = new Map();
const conversation = path.join(storage, createHash('sha256').update(project).digest('hex') + '.conversation');
const tasksFile = conversation + '.tasks';
let admissions = Promise.resolve();
async function pointer(payload={action:'state'}, acceptance, diagnostic) {
  const access=process.env.COMMAND_MEMORY_URL && process.env.COMMAND_MEMORY_TOKEN
    ? {url:process.env.COMMAND_MEMORY_URL,token:process.env.COMMAND_MEMORY_TOKEN}
    : JSON.parse(await fs.readFile('/run/command-memory/memory.json','utf8'));
  const response=await fetch(access.url+'/conversation',{
    method:'POST',redirect:'error',signal:AbortSignal.timeout(8000),
    headers:{'Content-Type':'application/json',Authorization:'Bearer '+access.token,...(acceptance===undefined ? {} : {'X-SmartDev-Acceptance':acceptance}),...(diagnostic===undefined ? {} : {'X-SmartDev-Diagnostic':diagnostic})},body:JSON.stringify(payload)
  });
  const result=await response.json();
  if (!response.ok) throw Error(typeof result.detail==='string' ? result.detail : 'Conversation service is unavailable.');
  return result;
}
function conversationLog(record) {
  return record.legacy ? conversation : conversation+'.'+record.id;
}


function assignment(data) {
  if (!data.workflow && data.newTask===true) return {create:true};
  return data.objective ? {objective:data.objective} : {};
}

const sensitive = /-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)|\b(?:gh[pousr]_[A-Za-z0-9_]{15,}|github_pat_[A-Za-z0-9_]{15,}|sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{16,}|hvs\.[A-Za-z0-9_-]{12,})/gi;
const redact = value => String(value).replace(sensitive, '[credential redacted]');
const uploadRoot = path.join(project,'uploads');
const uploadIndex = path.join(storage,createHash('sha256').update(project).digest('hex') + '.uploads');
const assets=assetStore(uploadRoot,uploadIndex);

async function upload(file) {
  if (!file || typeof file.name!=='string' || typeof file.data!=='string' ||
      !/^[A-Za-z0-9+/]+={0,2}$/.test(file.data) || file.data.length>26_666_668)
    throw new Error('Invalid attachment. Files must be at most 20 MB.');
  const originalName=file.name.replace(/[\x00-\x1f\x7f]/g,'').slice(-180);
  const safeName=originalName.replace(/[^A-Za-z0-9._-]/g,'_') || 'attachment';
  const contents=Buffer.from(file.data,'base64');
  if (contents.length>20_000_000 || contents.toString('base64')!==file.data)
    throw new Error('Invalid attachment encoding or size.');
  const hex=contents.subarray(0,8).toString('hex');
  let image='';
  if (hex==='89504e470d0a1a0a') image='.png';
  else if (hex.startsWith('ffd8ff')) image='.jpg';
  else if (contents.toString('ascii',0,4)==='RIFF' && contents.toString('ascii',8,12)==='WEBP') image='.webp';
  if (!image && (/^image\/(png|jpeg|webp)$/i.test(file.type || '') || /\.(png|jpe?g|webp)$/i.test(safeName)))
    throw new Error('Image contents do not match a supported image format.');
  if (/\.pdf$/i.test(safeName) && !hex.startsWith('255044462d'))
    throw new Error('Invalid PDF signature.');
  if (/\.(zip|docx|xlsx|pptx)$/i.test(safeName) && !/^504b(?:0304|0506|0708)/.test(hex))
    throw new Error('Invalid archive signature.');
  if (/^(4d5a|7f454c46|cafebabe|feedface|feedfacf|cefaedfe|cffaedfe)/.test(hex) ||
      /\.(exe|msi|dll|com|scr|appimage)$/i.test(safeName))
    throw new Error('Executable uploads are unsupported.');
  if (/\.json$/i.test(safeName)) {
    try { JSON.parse(contents.toString('utf8')); } catch { throw new Error('Invalid JSON file.'); }
  }
  const mimeType=image ? (image==='.jpg'?'image/jpeg':'image/'+image.slice(1))
    : /^application\/(?:json|pdf|zip)$|^text\/[a-z0-9.+-]+$/i.test(file.type || '') ? file.type
    : /\.json$/i.test(safeName) ? 'application/json'
    : /\.pdf$/i.test(safeName) ? 'application/pdf' : 'application/octet-stream';
  return assets.store(contents,{originalName,image,mimeType});
}
async function getUpload(id) { return assets.get(id); }

async function trees() {
  const {stdout} = await git('git', ['worktree', 'list', '--porcelain', '-z'], {cwd:root});
  return stdout.split('\0').filter(field => field.startsWith('worktree ')).map(field => field.slice(9))
    .filter(directory => directory === root || directory.startsWith(project + '/worktrees/'))
    .map(directory => path.relative(project,directory));
}
async function read(file) {
  try { return await fs.readFile(file, 'utf8'); } catch (error) { if (error.code !== 'ENOENT') throw error; return ''; }
}
function files(tree, objective) {
  const key = createHash('sha256').update(tree + '\0' + objective).digest('hex');
  return { session: path.join(storage, key + '.session'), log: path.join(storage, key + '.log'), base: path.join(storage,key+'.base') };
}
function reply(response, code, data, type='application/json; charset=utf-8') {
  response.writeHead(code, {'Content-Type':type,'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',
    'Content-Security-Policy':"default-src 'none'; script-src 'self'; style-src 'self'; img-src blob:; connect-src 'self'; frame-ancestors 'self' https://smartdev.youkandoit.com"});
  response.end(type.startsWith('application/json') ? JSON.stringify(data) : data);
}
async function body(request) {
  if (request.headers['content-type'] !== 'application/json' || request.headers['sec-fetch-site'] !== 'same-origin') {
    throw new Error('Use the application form to submit a task.');
  }
  const chunks=[]; let size=0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > 55_000_000) throw new Error('Attachments exceed the request limit.');
    chunks.push(chunk);
  }
  return JSON.parse(Buffer.concat(chunks));
}
function structuredInstructions(prompt) {
  return /^(?:#\s+(?:CODEX|CB)\s+BUILD\b|TASK:\s*\S)/i.test(prompt.trimStart()) &&
    /^#{2,3}\s+\S/m.test(prompt);
}

async function resolveNewTaskInstructions(data) {
  if (data.newTask !== true || data.prompt !== 'Use the attached project files for this turn.' ||
      !Array.isArray(data.files) || data.files.length !== 1 || !data.files[0]?.id) return;
  const file=await getUpload(data.files[0].id);
  if (!/\.md$/i.test(file.originalName) || file.size > 120000) return;
  const contents=await fs.readFile(file.location,'utf8');
  if (!structuredInstructions(contents)) return;
  data.prompt=contents;
  data.files=[];
}

function validateInstruction(data) {
  if (typeof data.prompt !== 'string' || !data.prompt.trim() || data.prompt.length > 30000) throw new Error('Enter valid task instructions.');
  if (redact(data.prompt) !== data.prompt) throw new Error('Remove the credential from your prompt before sending.');
  if (data.criterion && !/^criterion-[1-9][0-9]?$/.test(data.criterion)) throw new Error('Enter a valid criterion ID.');
  const attachments = data.files;
  if (!Array.isArray(attachments) || attachments.length > 8 || attachments.some(f => !f || (typeof f.id !== 'string' && typeof f.data !== 'string'))) {
    throw new Error('Attach up to eight files of 20 MB each.');
  }
}

async function skillDrafts(tree) {
  const drafts=new Map();
  const parent=path.join(project,tree,'.agents','skills');
  if (await fs.realpath(parent).catch(()=>null)!==parent) return drafts;
  for (const entry of await fs.readdir(parent,{withFileTypes:true})) {
    if (!entry.isDirectory() || !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(entry.name)) continue;
    const location=path.join(parent,entry.name,'SKILL.md');
    const stat=await fs.lstat(location).catch(()=>null);
    if (!stat?.isFile() || stat.size>30000) continue;
    const content=await fs.readFile(location,'utf8').catch(()=>null);
    if (content===null || content.length>30000 || presentation.cleanText(content)!==content) continue;
    const header=content.match(/^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/)?.[1];
    if (!header) continue;
    const name=header.match(/^name:[ \t]*([a-z0-9-]+)[ \t]*\r?$/m)?.[1];
    const description=header.match(/^description:[ \t]*([^\r\n]+)\r?$/m)?.[1].trim();
    if (name===entry.name && description) drafts.set(name,{name,description,
      filePath:'.agents/skills/'+name+'/SKILL.md',content});
  }
  return drafts;
}

async function start(tree, data, chat, message, direct, queuedExecution) {
  validateInstruction(data);
  const attachments=data.files;
  const tasks = JSON.parse(await read(tasksFile) || '{}');
  const routing=assignment(data);
  data.objective=routing.objective;
  const objective=routing.objective || (routing.create ? message : undefined);
  if (objective && !ids.test(objective)) throw Error('Select a valid existing task.');
  if (!objective) tree='repository';
  if (!objective && chat.id===chat.active_task_id) throw Error('Select Main before starting a direct execution.');
  if (objective && !routing.create && chat.id===chat.active_task_id && chat.id!==objective)
    throw Error('This task belongs to a different conversation.');
  if (routing.objective && tasks[objective]?.conversationId && tasks[objective].conversationId!==chat.id)
    throw Error('This task belongs to a different conversation.');
  if (objective) tree=tasks[objective]?.tree || null;
  const managedTree = objective && 'worktrees/task-' + objective.toLowerCase();
  const existingTrees = await trees();
  if (managedTree && existingTrees.includes(managedTree)) {
    tree = managedTree;
  } else if (routing.create || (routing.objective && data.explicitTask===true && !tasks[objective])) {
    const description = data.prompt.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 46).replace(/-$/g, '') || 'task';
    const branch = 'codex/' + objective.toLowerCase() + '-' + description;
    const originalTree = root;
    try {
      const {stdout} = await git('git', ['rev-parse', '--verify', 'HEAD'], {cwd:originalTree});
      await git('git', ['worktree', 'add', '-b', branch, path.join(project, managedTree), stdout.trim()], {cwd:root,timeout:30000,env:{...process.env,GIT_LFS_SKIP_SMUDGE:'1'}});
    } catch {
      throw new Error('Could not create the isolated task worktree. Check that the starting worktree has a commit.');
    }
    tree = managedTree;
  }
  if (objective && (!tree || tree==='repository' || !existingTrees.includes(tree) && tree!==managedTree)) throw Error('This task does not have an isolated worktree.');
  if (routing.create) chat=await pointer({action:'new_task_conversation',task_id:objective,
    original_request:data.prompt,name:data.title});
  const runKey=objective ? tree : 'chat:'+chat.id;
  if (!objective && [...active.values()].some(run=>!run.objective)) throw Error('A direct execution is already using the project checkout.');
  if (active.has(runKey)) throw new Error(objective?'This task already has an active execution.':'Codex is already responding in this conversation.');

  const execution=data.resumeExecution || queuedExecution || direct?.executionId || randomUUID();
  const record = {objective, execution, conversationId:chat.id, state:'thinking', child: null, cancelled: false};
  active.set(runKey, record);
  // A session written by the former read-only runner carries obsolete instructions.
  // Keep its transcript, but resume only sessions established under the write contract.
  const names=objective ? files(tree,objective) : {session:conversationLog(chat)+'.main-write.session'};
  try {
    const skillsBefore=await skillDrafts(tree).catch(()=>null);
    const pendingQuestion=data.decision && (await workflow()).decisions.find(item=>
      item.id===data.decision && item.conversationId===chat.id &&
      (item.objective || null)===(objective || null) && item.status==='answering');
    if (data.decision && !pendingQuestion) throw Error('This question is no longer waiting for that execution.');
    const args = ['--json','--terms',String(data.terms || data.prompt).slice(0,300), ...(objective ? ['--objective',objective] : ['--conversation',chat.id,'--conversation-execution',execution])];
    if (data.criterion && data.objective) args.push('--criterion',data.criterion);
    if (!objective && pendingQuestion) args.push('--reply-question',pendingQuestion.question);
    const previous = data.resumeThread || (await read(names.session)).trim();
    if ((data.resumeExecution || data.strictResume) && !ids.test(previous)) throw Error('The original Codex thread cannot be confirmed.');
    if (ids.test(previous)) args.push('--resume',previous);
    if (data.resumeExecution || data.strictResume) args.push('--strict-resume');
    let threadId=ids.test(previous) ? previous : undefined;
    const attached = attachments.length
      ? await Promise.all(attachments.map(async file=>getUpload(file.id || (await upload(file)).id)))
      : (await Promise.all((tasks[objective]?.attachments || []).map(id=>getUpload(id).catch(error=>{
          if (error.code!=='UPLOAD_MISSING' && error.code!=='ENOENT') throw error;
        })))).filter(Boolean);
    let instructionDocument;
    const structuredTask = routing.create && structuredInstructions(data.prompt);
    if (structuredTask) {
      const filename='Task instructions - '+objective+'.md';
      instructionDocument=await assets.store(Buffer.from(data.prompt,'utf8'),
        {originalName:filename,image:'',mimeType:'text/markdown'});
      attached.unshift(await getUpload(instructionDocument.id));
    } else if (tasks[objective]?.instructionDocument && !attached.some(file=>file.id===tasks[objective].instructionDocument.id)) {
      attached.unshift(await getUpload(tasks[objective].instructionDocument.id));
    }
    for (const file of attached) args.push(file.image ? '--image' : '--file',file.location);
    if (record.cancelled) throw new Error('Task cancelled before launch.');
    const cwd=path.join(project,tree);
    if (objective && !(await read(names.base))) {
      const {stdout}=await git('git',['rev-parse','--verify','HEAD'],{cwd});
      await fs.writeFile(names.base,stdout.trim(),{mode:0o600});
    }
    if (objective && !tasks[objective]) {
      const {stdout:branch}=await git('git',['branch','--show-current'],{cwd});
      tasks[objective]={objective, projectId:process.env.PROJECT_NAME, taskId:objective,
        dependencies:[],
        tree, branch:branch.trim(), worktreeId:tree, conversationId:chat.id, request:data.prompt, createdHere:!data.objective};
    }
    if (objective) {
      tasks[objective].attachments=attached.map(file=>file.id);
      if (instructionDocument) tasks[objective].instructionDocument={id:instructionDocument.id,sha256:instructionDocument.sha256};
      await fs.writeFile(tasksFile+'.tmp',JSON.stringify(tasks),{mode:0o600});
      await fs.rename(tasksFile+'.tmp',tasksFile);
    }
    const recentTasks=Object.values(tasks).filter(task=>task.objective!==objective).slice(-8)
      .map(({objective,request,parentTaskId})=>({objective,request:request.slice(0,500),parentTaskId}));
    args.push('--project-context',JSON.stringify(recentTasks));
    if (objective) await pointer({action:'activity',conversation_id:chat.id,task_id:objective});
    const child=spawn('codex-task',[...args,'--',data.prompt],{cwd:path.join(project,tree),stdio:['ignore','pipe','pipe']});
    record.child=child;
    let writes=Promise.resolve();
    function append(line) {
      const entry=redact(line)+'\n';
      writes=writes.then(async()=>{
        if (objective) await fs.appendFile(names.log,entry,{mode:0o600});
        await fs.appendFile(conversationLog(chat),entry,{mode:0o600});
      }).catch(error=>console.error('Chat log write failed:',error.code));
    }
    if (!direct && !data.loggedReply) append(JSON.stringify({type:'smartdev.user',message,execution,text:instructionDocument ? "Task created: "+chat.name : data.prompt,at:new Date().toISOString(),objective, branch:tasks[objective]?.branch, attachments:attached.map(({id,originalName,image})=>({id,originalName,image}))}));
    append(JSON.stringify({type:'smartdev.run_status',state:'running',objective,execution,at:new Date().toISOString()}));
    let question;
    let failure;
    const stream=createInterface({input:child.stdout});
    stream.on('line',line=>{
      try {
        const event=JSON.parse(line);
        if (event.type==='thread.started' && ids.test(event.thread_id)) {
          threadId=event.thread_id;
          writes=writes.then(async()=>{
            await fs.writeFile(names.session,event.thread_id,{mode:0o600});
            await pointer({action:'activity',conversation_id:chat.id,task_id:objective,thread_id:event.thread_id});
          }).catch(error=>console.error('Could not persist Codex session:',error.message));
        }
        if (event.item?.type==='reasoning') return;
        if (event.type==='item.started' && event.item?.type==='command_execution') {
          const command=event.item.command || '';
          record.state=/\b(test|pytest|playwright|unittest|vitest|jest)\b/i.test(command) ? 'running tests'
            : /\b(build|vite build)\b/i.test(command) ? 'building'
            : /\b(rg|cat|sed|ls|find|diff)\b/i.test(command) ? 'reading project' : 'executing';
        } else if (event.type==='item.started') record.state=event.item?.type==='file_change' ? 'editing files' : 'executing';
        else if (event.type==='item.completed') record.state='thinking';
        if (event.type==='item.completed' && event.item?.type==='agent_message') {
          question=String(event.item.text || '').match(/^Question:[ \t]*([^]*\S)/m)?.[1].trim();
        }
        append(JSON.stringify({...event,objective,execution,smartdev_at:new Date().toISOString()}));
      } catch {
        append(JSON.stringify({type:'smartdev.diagnostic',text:line,objective,execution,at:new Date().toISOString()}));
      }
    });
    createInterface({input:child.stderr}).on('line',line=>{
      try {
        const event=JSON.parse(line);
        if (event.type==='smartdev.auth' && event.status==='unavailable') failure=event.message;
        append(JSON.stringify({...event,objective,execution,smartdev_at:new Date().toISOString()}));
      } catch {
        append(JSON.stringify({type:'smartdev.diagnostic',text:line,objective,execution,at:new Date().toISOString()}));
      }
    });
    child.once('error',()=>append('Could not start codex-task.'));
    child.once('close',async code=>{
      append(JSON.stringify({type:'smartdev.result',code:code ?? (record.cancelled ? 130 : 1),error:failure,awaitingInput:code===0 && Boolean(question),at:new Date().toISOString(),objective,execution}));
      await writes;
      void serialize(async()=>{
        const state=await workflow();
        const decision=data.decision && state.decisions.find(item=>item.id===data.decision);
        const queueItem=data.queueItem || decision?.queueItem;
        if (decision) {
          decision.status=code===0 ? 'resolved' : (data.strictResume ? 'needs_review' : 'pending');
          if (code===0) decision.resolvedAt=new Date().toISOString();
        }
        if (code===0 && question) state.decisions.push({id:objective?execution:randomUUID(), executionId:execution, threadId, conversationId:chat.id,
          objective, tree, queueItem, question:redact(question).slice(0,8000), status:'pending'});
        const item=state.items.find(item=>item.id===queueItem);
        if (item) {
          item.status=code===0 ? (question?'waiting':'completed') : (decision?.status==='pending'?'waiting':'failed');
          if (code!==0) item.error=redact(failure || 'Codex exited with status '+(code ?? 1)+'. Review the execution details.');
          else delete item.error;
        }
        const completedDirect=direct ? execution : data.retainedExecutionId;
        if (completedDirect) state.direct=state.direct.filter(entry=>entry.executionId!==completedDirect);
        let stagedSkill=false;
        const skillsAfter=skillsBefore && await skillDrafts(tree).catch(()=>null);
        for (const draft of skillsAfter?.values() || []) {
          const previous=skillsBefore.get(draft.name)?.content || '';
          if (previous===draft.content) continue;
          const existing=state.skillPromotions.find(row=>row.conversationId===chat.id &&
            row.objective===objective && row.name===draft.name && row.content===draft.content);
          if (existing) continue;
          state.skillPromotions.push({...draft,previous,id:randomUUID(),conversationId:chat.id,
            objective,executionId:execution,status:'pending'});
          stagedSkill=true;
        }
        if (stagedSkill || completedDirect || decision || item || (code===0 && question)) await saveWorkflow(state);
        if (!objective && code===0) {
          if (decision && !question) await pointer({action:'record_question',conversation_id:chat.id,execution_id:decision.executionId || decision.id,waiting_for_input:false});
          if (question) await pointer({action:'record_question',conversation_id:chat.id,execution_id:execution,waiting_for_input:true});
        }
        active.delete(runKey);
      }).catch(error=>{active.delete(runKey);console.error('Workflow update failed:',error.message);}).finally(scheduleDirect);
    });
    return {messageId:message, executionId:execution, conversationId:chat.id, objective, tree, parentTaskId:tasks[objective]?.parentTaskId, editorFolder: path.join(project, tree), instructionDocumentId:instructionDocument?.id};
  } catch (error) {
    active.delete(runKey);
    throw error;
  }
}

async function review(tree, objective) {
  if (!objective || (tree.startsWith('worktrees/task-') && tree !== 'worktrees/task-' + objective.toLowerCase())) {
    throw new Error('Select the task worktree to review.');
  }
  const cwd=path.join(project,tree);
  const options={cwd,maxBuffer:2_000_000,timeout:8000};
  const {stdout:head}=await git('git',['rev-parse','--verify','HEAD'],options);
  const {stdout:branch}=await git('git',['rev-parse','--abbrev-ref','HEAD'],options);
  const recorded=(await read(files(tree,objective).base)).trim();
  const base=/^[a-f0-9]{40,64}$/.test(recorded) ? recorded : head.trim();
  const secretPaths=[':(exclude,glob)**/.env',':(exclude,glob)**/.env.*',':(exclude,glob)**/*.pem',':(exclude,glob)**/*.key'];
  const {stdout:tracked}=await git('git',['diff','--no-ext-diff','--no-textconv','--no-color',base,'--',...secretPaths],options);
  const {stdout:untracked}=await git('git',['ls-files','--others','--exclude-standard','-z'],options);
  const patches=[tracked];
  for (const file of untracked.split('\0').filter(Boolean).slice(0,25)) {
    if (patches.join('').length>130000) break;
    if (/^(?:\.env(?:\.|$)|id_(?:rsa|ed25519)$)|\.(?:pem|key)$/i.test(path.basename(file))) continue;
    const location=path.join(cwd,file);
    const info=await fs.lstat(location);
    if ((!info.isFile() && !info.isSymbolicLink()) || info.size>30000) {
      patches.push('Untracked file: '+file+'\n');
      continue;
    }
    try { await git('git',['diff','--no-index','--no-ext-diff','--no-textconv','--no-color','--','/dev/null',file],options); }
    catch (error) {
      if (error.code!==1) throw error;
      patches.push(error.stdout);
    }
  }
  const full=redact(patches.join(''));
  return {objective,tree,branch:branch.trim(),head:head.trim(),base,
    diff:full.slice(0,140000),truncated:full.length>140000 || untracked.split('\0').filter(Boolean).length>25};
}

const checkpoints=path.join(project,'config','checkpoints');
async function version(id) {
  if (typeof id!=='string' || !ids.test(id)) throw new Error('Select a valid checkpoint.');
  const raw=await read(path.join(checkpoints,id+'.json'));
  if (!raw) throw new Error('Checkpoint not found.');
  const record=JSON.parse(raw);
  if (record.projectId!==process.env.PROJECT_NAME || record.id!==id || !/^[a-f0-9]{40,64}$/.test(record.sha))
    throw new Error('Invalid project checkpoint.');
  return record;
}
async function restoreVersion(source,target,filename) {
  if (!source.worktree.startsWith('worktrees/task-') || !(await trees()).includes(source.worktree) || active.has(source.worktree))
    throw new Error('Select an inactive task worktree.');
  const directory=path.join(project,source.worktree);
  return new Promise((resolve,reject)=>{
    const child=execFile('python3',['/usr/local/bin/checkpoints.py'],{timeout:30000},
      (error,stdout,stderr)=>{
        if (error) return reject(new Error(stderr.trim() || 'Could not restore the selected files.'));
        try { resolve(JSON.parse(stdout)); } catch (error) { reject(error); }
      });
    child.stdin.on('error',reject);
    child.stdin.end(JSON.stringify({directory,project,source,target,filename}));
  });
}

const workflowFile=conversation+'.workflow';
function serialize(operation) {
  const result=admissions.then(operation);
  admissions=result.catch(()=>{});
  return result;
}
async function workflow() {
  const state=JSON.parse(await read(workflowFile) || '{"paused":false,"items":[],"decisions":[]}');
  state.direct ??= [];
  state.skillPromotions ??= [];
  return state;
}
async function saveWorkflow(state) {
  await fs.writeFile(workflowFile+'.tmp',JSON.stringify(state),{mode:0o600});
  await fs.rename(workflowFile+'.tmp',workflowFile);
}
async function advanceQueue() {
  const state=await workflow();
  const next=directPolicy.nextLegacy(state);
  if (!next || [...active.values()].some(run=>next.objective ? run.objective===next.objective :
      !run.objective && run.conversationId===next.conversationId) ||
      state.decisions.some(item=>item.status==='pending' && (next.objective ? item.objective===next.objective :
        !item.objective && item.conversationId===next.conversationId))) return;
  const chat=(await pointer()).conversations.find(item=>item.id===next.conversationId);
  next.status='running';
  await saveWorkflow(state);
  try {
    if (!chat || !(await trees()).includes(next.tree)) throw Error('The associated worktree is unavailable.');
    await start(next.tree,{workflow:true,queueItem:next.id,objective:next.objective,
      prompt:next.description,terms:next.terms,criterion:next.criterion,
      files:(next.attachments || []).map(id=>({id}))},chat,next.id,undefined,next.executionId);
  } catch(error) {
    next.status='failed';
    next.error=redact(error.message);
    await saveWorkflow(state);
  }
}
async function directEvent(chat, event) {
  await fs.appendFile(conversationLog(chat),redact(JSON.stringify(event))+'\n',{mode:0o600});
}

async function writeDirectMessage(chat, item) {
  if (item.logged) return;
  const marker='"message":"'+item.messageId+'"';
  if (!(await read(conversationLog(chat))).includes(marker)) {
    await directEvent(chat,{type:'smartdev.user',message:item.messageId,execution:item.executionId,
      text:item.prompt,attachments:item.attachments,at:item.createdAt});
    if (item.deferred) await directEvent(chat,{type:'smartdev.run_status',state:'queued',execution:item.executionId,at:item.createdAt});
  }
  item.logged=true;
}

async function admitDirect(chat, instruction, messageId, requestId) {
  const state=await workflow();
  const prior=state.direct.find(item=>item.messageId===messageId);
  if (prior) return prior;
  validateInstruction(instruction);
  if (chat.id===chat.active_task_id) throw Error('Open Main before submitting a direct run.');
  const attachments=await Promise.all(instruction.files.map(async file=>getUpload(file.id || (await upload(file)).id)));
  const item={messageId,requestId,executionId:randomUUID(),conversationId:chat.id,tree:'repository',status:'queued',
    prompt:instruction.prompt,terms:String(instruction.terms || instruction.prompt).slice(0,300),
    attachments:attachments.map(({id,originalName,image})=>({id,originalName,image})),
    deferred:state.direct.length>0 || [...active.values()].some(run=>!run.objective) ||
      state.decisions.some(decision=>!decision.objective && decision.conversationId===chat.id &&
        ['pending','answering'].includes(decision.status)),
    createdAt:new Date().toISOString()};
  state.direct.push(item);
  await saveWorkflow(state);
  return item;
}

function directReceipt(item) {
  return {messageId:item.messageId,executionId:item.executionId,conversationId:item.conversationId,
    tree:item.tree,status:item.deferred?'queued':'starting'};
}

async function originalThread(chat, decision) {
  if (ids.test(decision.threadId || '')) return decision.threadId;
  const execution=decision.executionId || decision.id;
  const transcript=await read(conversationLog(chat));
  const records=transcript.split('\n').flatMap(line=>{
    try { return [JSON.parse(line)]; } catch { return []; }
  });
  const start=records.findIndex(event=>event.execution===execution && event.type==='smartdev.user');
  if (start<0) return undefined;
  // Old resumed turns may omit thread.started. Use the closest recorded thread
  // at or before that turn, never a newer conversation session.
  const exact=records.find(event=>!event.objective && event.execution===execution && event.type==='thread.started' && ids.test(event.thread_id || ''));
  return exact?.thread_id || records.slice(0,start).findLast(event=>!event.objective && event.type==='thread.started' && ids.test(event.thread_id || ''))?.thread_id;
}

async function resumeMain(chat,state,decision,instruction,messageId,retained) {
  validateInstruction(instruction);
  if ([...active.values()].some(run=>!run.objective)) throw Error('Wait for the current Main turn to release the checkout.');
  const thread=await originalThread(chat,decision);
  if (!thread) throw Error('The original Codex thread cannot be confirmed. The question and answer are still retained.');
  const executionId=decision.executionId || decision.id;
  decision.status='answering';
  decision.answerMessage=messageId;
  if (retained) retained.status='reply_dispatching';
  await saveWorkflow(state);
  try {
    const resolvedFiles=await Promise.all(instruction.files.map(async file=>getUpload(file.id || (await upload(file)).id)));
    if (!retained && !(await read(conversationLog(chat))).includes('"message":"'+messageId+'"'))
      await directEvent(chat,{type:'smartdev.user',message:messageId,execution:executionId,text:instruction.prompt,
        at:new Date().toISOString(),attachments:resolvedFiles.map(({id,originalName,image})=>({id,originalName,image}))});
    if (retained) await directEvent(chat,{type:'smartdev.run_status',state:'redirected',
      execution:retained.executionId,at:new Date().toISOString()});
    const result=await start('repository',{...instruction,files:resolvedFiles.map(({id})=>({id})),workflow:true,decision:decision.id,
      resumeExecution:executionId,resumeThread:thread,loggedReply:true,retainedExecutionId:retained?.executionId},chat,messageId);
    return {...result,status:'resuming'};
  } catch(error) {
    decision.status='pending';
    if (retained) retained.status='needs_review';
    await saveWorkflow(state);
    throw error;
  }
}

async function advanceDirect() {
  const state=await workflow();
  const repair=directPolicy.retainedFlatFileReply(state);
  if (repair && ![...active.values()].some(run=>!run.objective)) {
    const chat=(await pointer()).conversations.find(item=>item.id===repair.conversationId);
    const pending=directPolicy.pendingMain(state,repair.conversationId);
    if (chat && pending) {
      try {
        await resumeMain(chat,state,pending,{prompt:repair.prompt,terms:repair.terms,
          files:repair.attachments.map(({id})=>({id}))},repair.messageId,repair);
      } catch(error) {
        repair.status='needs_review';
        await saveWorkflow(state);
        await directEvent(chat,{type:'smartdev.result',code:1,execution:repair.executionId,error:redact(error.message),
          at:new Date().toISOString()});
      }
      return;
    }
  }
  const next=directPolicy.nextDirect(state,[...active.values()]);
  if (!next) return;
  const chat=(await pointer()).conversations.find(item=>item.id===next.conversationId);
  if (!chat) throw Error('Main conversation unavailable for the retained submission.');
  await writeDirectMessage(chat,next);
  if (next.edited) await directEvent(chat,{type:'smartdev.prompt_edit',execution:next.executionId,
    text:next.prompt,at:new Date().toISOString()});
  await pointer({action:'accepted',conversation_id:chat.id,request_id:next.requestId,result:directReceipt(next)});
  // Persist the dispatch boundary before spawning. An interrupted spawn is never replayed.
  next.status='dispatching';
  await saveWorkflow(state);
  try {
    await start('repository',{prompt:next.prompt,terms:next.terms,files:next.attachments.map(({id})=>({id}))},
      chat,next.messageId,next);
  } catch(error) {
    await directEvent(chat,{type:'smartdev.result',code:1,error:redact(error.message),
      execution:next.executionId,at:new Date().toISOString()});
    state.direct=state.direct.filter(item=>item!==next);
    await saveWorkflow(state);
  }
}

function scheduleDirect() {
  void serialize(advanceDirect).catch(error=>console.error('Main dispatch:',error.message));
}

async function resumeTask(chat,state,decision,instruction,messageId) {
  decision.status='answering';
  decision.answerMessage=messageId;
  await saveWorkflow(state);
  try {
    return await start(decision.tree,{workflow:true,decision:decision.id,objective:decision.objective,
      prompt:instruction.prompt,files:instruction.files,resumeThread:instruction.strictResume ? decision.threadId : undefined,
      strictResume:instruction.strictResume},chat,messageId);
  } catch(error) {
    decision.status='pending';
    delete decision.answerMessage;
    await saveWorkflow(state);
    throw error;
  }
}

function queuedTaskReceipt(item) {
  return {messageId:item.id,executionId:item.executionId,conversationId:item.conversationId,
    objective:item.objective,tree:item.tree,status:'queued'};
}

async function queueTaskInstruction(chat, data, message, runningTree) {
  validateInstruction(data);
  if (!(await trees()).includes(runningTree)) throw Error('The task worktree is unavailable.');
  const state=await workflow();
  const existing=state.items.find(item=>item.id===message && item.conversationId===chat.id);
  if (existing) return queuedTaskReceipt(existing);
  const attachments=await Promise.all(data.files.map(async file=>getUpload(file.id || (await upload(file)).id)));
  const item={id:message,executionId:randomUUID(),conversationId:chat.id,objective:data.objective,
    tree:runningTree,description:data.prompt,terms:data.terms,criterion:data.criterion,
    attachments:attachments.map(file=>file.id),status:'queued'};
  state.items.push(item);
  await saveWorkflow(state);
  return queuedTaskReceipt(item);
}

async function changeWorkflow(data) {
  const chat=(await pointer()).conversations.find(item=>item.id===data.conversationId);
  if (!chat) throw Error('Conversation not available.');
  const state=await workflow();
  const item=state.items.find(row=>row.id===data.id);
  if (['edit_direct','remove_direct'].includes(data.action)) {
    const queued=state.direct.find(run=>run.conversationId===chat.id && run.status==='queued'
      && presentation.reference(project,run.executionId)===data.id);
    if (!queued) throw Error('This prompt has already left the queue.');
    if (data.action==='edit_direct') {
      const prompt=typeof data.description==='string' && data.description.trim();
      if (!prompt || prompt.length>30000 || redact(prompt)!==prompt) throw Error('Enter a prompt without credentials.');
      Object.assign(queued,{prompt,terms:prompt.slice(0,300),edited:true});
    } else {
      queued.status='cancelled';
      await saveWorkflow(state);
      await directEvent(chat,{type:'smartdev.run_status',state:'cancelled',execution:queued.executionId,at:new Date().toISOString()});
      state.direct=state.direct.filter(run=>run!==queued);
    }
  } else if (data.action==='edit' && item?.status==='queued' && item.conversationId===chat.id) {
    const description=typeof data.description==='string' && data.description.trim();
    if (!description || description.length>30000 || redact(description)!==description) throw Error('Enter a prompt without credentials.');
    item.description=description;
    if (item.terms) item.terms=description.slice(0,300);
  } else if (data.action==='defer_skill_promotion') {
    const proposal=state.skillPromotions.find(row=>row.conversationId===chat.id &&
      presentation.reference(project,row.id)===data.id);
    if (!proposal || proposal.status!=='pending') throw Error('This skill proposal is no longer pending.');
    proposal.status='deferred';
    proposal.resolvedAt=new Date().toISOString();
  } else if (data.action==='add') {
    const description=typeof data.description==='string' && data.description.trim();
    if (!description || description.length>30000 || redact(description)!==description)
      throw Error('Enter an instruction without credentials.');
    const records=JSON.parse(await read(tasksFile) || '{}');
    const objective=data.objective || undefined;
    if (!objective) throw Error('Send instructions directly in Main.');
    if (objective && (!ids.test(objective) || (records[objective] && records[objective].conversationId && records[objective].conversationId!==chat.id)))
      throw Error('Task is outside this conversation.');
    const tree=records[objective]?.tree || 'repository';
    if (!ids.test(data.id || '')) throw Error('Queue request ID required.');
    if (!item) state.items.push({id:data.id,conversationId:chat.id,objective,tree,description,independent:data.independent===true,status:'queued'});
  } else if (data.action==='pause') state.paused=true;
  else if (data.action==='resume') state.paused=false;
  else if (data.action==='remove' && item?.conversationId===chat.id && ['queued','failed'].includes(item.status)) {
    state.items=state.items.filter(row=>row!==item);
  } else if (data.action==='up' && item?.status==='queued') {
    const index=state.items.indexOf(item);
    const previous=state.items[index-1];
    if (previous?.status!=='queued' || ((previous.conversationId===item.conversationId ||
        (item.objective && previous.objective===item.objective)) && !(previous.independent && item.independent)))
      throw Error('Steps for the same conversation or task run in order.');
    [state.items[index-1],state.items[index]]=[item,previous];
  } else if (data.action==='answer') {
    const decision=state.decisions.find(row=>presentation.reference(project,row.id)===data.id && row.conversationId===chat.id && row.status==='pending');
    if (!decision) throw Error('This request is no longer pending.');
    if ([...active.values()].some(run=>decision.objective ? run.objective===decision.objective :
        !run.objective && run.conversationId===chat.id)) throw Error('Wait for Codex to finish its current turn.');
    if (!decision.objective) {
      await resumeMain(chat,state,decision,{prompt:data.description,files:data.files || []},randomUUID());
      return state;
    }
    await resumeTask(chat,state,decision,{prompt:data.description,files:data.files || []},randomUUID());
    return state;
  } else throw Error('This action is no longer available.');
  await saveWorkflow(state);
  return state;
}
async function main() {
  await fs.mkdir(storage,{recursive:true,mode:0o700});
  await fs.mkdir(uploadRoot,{recursive:true,mode:0o700});
  await fs.mkdir(uploadIndex,{recursive:true,mode:0o700});
  const recovered=await workflow();
  if (recovered.direct.some(item=>item.status==='cancelled')) {
    const {conversations}=await pointer();
    for (const item of recovered.direct.filter(run=>run.status==='cancelled')) {
      const chat=conversations.find(row=>row.id===item.conversationId);
      if (chat) await directEvent(chat,{type:'smartdev.run_status',state:'cancelled',execution:item.executionId,at:new Date().toISOString()});
    }
    recovered.direct=recovered.direct.filter(run=>run.status!=='cancelled');
  }
  for (const item of recovered.items.filter(item=>item.status==='running')) {
    item.status='failed';
    item.error='The workspace restarted during execution. Review the transcript before requeuing.';
  }
  for (const item of recovered.decisions.filter(directPolicy.obsoleteMainQuestion)) {
    item.status='superseded';
    item.supersededAt=new Date().toISOString();
  }
  const answering=recovered.decisions.filter(item=>item.status==='answering');
  if (answering.length) {
    for (const item of answering) {
      // A durable user event is the admission receipt. Never replay a
      // submitted answer after the workspace runner is interrupted.
      const logs=item.objective ? [files(item.tree,item.objective).log] :
        [conversation+'.'+item.conversationId,conversation];
      let accepted=false;
      for (const log of logs) if (item.answerMessage && (await read(log)).includes('"message":"'+item.answerMessage+'"')) {
        accepted=true;
        break;
      }
      if (accepted && !item.objective) {
        item.status='needs_review';
        item.problem='The response was saved before the workspace restarted. Review the transcript before resuming.';
      } else if (accepted) {
        item.status='resolved';
        item.resolvedAt=new Date().toISOString();
        const step=recovered.items.find(step=>step.id===item.queueItem && step.status==='waiting');
        if (step) {step.status='failed';step.error='The reply was recorded before the workspace restarted. Review the conversation before continuing.';}
      } else item.status='pending';
    }
  }
  if (recovered.direct.some(item=>item.status==='dispatching')) {
    const chats=(await pointer()).conversations;
    for (const item of recovered.direct.filter(entry=>entry.status==='dispatching')) {
      const chat=chats.find(entry=>entry.id===item.conversationId);
      if (chat && !(await read(conversationLog(chat))).split('\n').some(line=>{
        try {const event=JSON.parse(line);return event.type==='smartdev.result' && event.execution===item.executionId;}
        catch {return false;}
      })) await directEvent(chat,{type:'smartdev.result',code:1,execution:item.executionId,
        error:'Workspace restarted during execution. Check the transcript and checkout before sending again.',at:new Date().toISOString()});
    }
    recovered.direct=recovered.direct.filter(item=>item.status!=='dispatching');
    await saveWorkflow(recovered);
  }
  if (recovered.items.length || recovered.decisions.length) await saveWorkflow(recovered);
  setInterval(scheduleDirect,2000).unref();
  scheduleDirect();
  setInterval(()=>void serialize(advanceQueue).catch(error=>console.error('Queue:',error.message)),2000).unref();
  http.createServer(async (request,response)=>{
    try {
      const url=new URL(request.url,'http://localhost');
      const acceptance=request.headers['x-smartdev-acceptance'];
      const binding=acceptance===undefined ? {} : {
        workspace_name:process.env.PROJECT_NAME,
        fixture_conversation_id:request.headers['x-smartdev-fixture-conversation']
      };
      if (request.method==='POST' || url.pathname==='/acceptance') {
        let permission;
        try { permission=await pointer({action:'authorize_submission',...binding},acceptance); }
        catch { return reply(response,403,{error:'Submission identity is not authorized for this workspace.'}); }
        if (url.pathname==='/acceptance')
          return permission.environment==='acceptance' ? reply(response,200,permission) :
            reply(response,403,{error:'An acceptance identity is required.'});
      }
      if (request.method==='GET' && url.pathname==='/diagnostics') {
        let access;
        try { access=await pointer({action:'authorize_diagnostics'},undefined,request.headers['x-smartdev-diagnostic']); }
        catch { return reply(response,403,{error:'Administrator authorization required.'}); }
        const snapshot=await pointer();
        const raw=url.searchParams.get('view')==='events';
        if (raw && access.role!=='administrator') return reply(response,403,{error:'Administrator authorization required.'});
        const requested=url.searchParams.get('conversation');
        const chat=requested ? snapshot.conversations.find(item=>item.id===requested) : snapshot.active;
        if (!chat) return reply(response,404,{error:'Conversation not available.'});
        const activeConversation=conversationLog(chat);
        const log=await fs.open(activeConversation).catch(error=>{if(error.code!=='ENOENT')throw error;});
        if (!log) return reply(response,200,raw ? {output:'',start:0,end:0} : presentation.diagnosticSummary(''));
        try {
          const size=(await log.stat()).size;
          const before=url.searchParams.get('before');
          const end=raw && /^\d+$/.test(before || '') ? Math.min(size,Number(before)) : size;
          const data=Buffer.alloc(Math.min(end,100000));
          const start=end-data.length;
          const {bytesRead}=await log.read(data,0,data.length,start);
          const text=data.subarray(0,bytesRead).toString('utf8');
          if (raw) return reply(response,200,{output:presentation.diagnosticEvents(text),start,end});
          return reply(response,200,presentation.diagnosticSummary(text));
        } finally {await log.close();}
      }
      if (request.method==='GET' && url.pathname==='/project-context') {
        const {stdout} = await git('python3', ['/usr/local/bin/project_context.py', root],
          {timeout:8000,maxBuffer:1_000_000});
        return reply(response,200,JSON.parse(stdout));
      }
      if (request.method==='GET' && ['/', '/chat.js', '/browser.js', '/timeline.js', '/style.css',
        '/project-context.html', '/project-context.js'].includes(url.pathname)) {
        const filename = url.pathname==='/' ? 'index.html' : url.pathname.slice(1);
        const type = filename.endsWith('.html') ? 'text/html' : filename.endsWith('.css') ? 'text/css' : 'text/javascript';
        return reply(response,200,await fs.readFile(path.join(__dirname,filename)),type);
      }
      if (request.method==='POST' && url.pathname==='/uploads') {
        const file=await upload(await body(request));
        return reply(response,201,file);
      }
      if (request.method==='POST' && url.pathname==='/manage-upload') {
        const change=await body(request);
        if (change.action==='delete') await assets.change(change.id,'delete');
        else if (change.action==='rename' && typeof change.name==='string' &&
                 change.name.trim() && change.name.length<=180 && !/[/\\\x00-\x1f\x7f]/.test(change.name))
          await assets.change(change.id,'rename',change.name.trim());
        else throw new Error('Enter a valid upload name or action.');
        return reply(response,200,{ok:true});
      }
      if (request.method==='GET' && url.pathname==='/uploads') {
        return reply(response,200,await assets.list());
      }
      if (request.method==='GET' && url.pathname==='/attachment') {
        const file=await getUpload(url.searchParams.get('id'));
        if (url.searchParams.get('view')==='1' && file.mimeType==='text/markdown') {
          response.setHeader('Cross-Origin-Resource-Policy','same-origin');
          return reply(response,200,await fs.readFile(file.location),'text/plain; charset=utf-8');
        }
        response.setHeader('Content-Disposition','attachment; filename="'+file.originalName.replace(/[^A-Za-z0-9._-]/g,'_')+'"');
        response.setHeader('Cross-Origin-Resource-Policy','same-origin');
        return reply(response,200,await fs.readFile(file.location),'application/octet-stream');
      }
      if (request.method==='GET' && url.pathname==='/history') {
        const names=await fs.readdir(checkpoints).catch(error=>{if (error.code==='ENOENT') return []; throw error;});
        const records=await Promise.all(names.filter(name=>ids.test(name.slice(0,-5)) && name.endsWith('.json'))
          .map(name=>version(name.slice(0,-5))));
        return reply(response,200,records.sort((a,b)=>b.at.localeCompare(a.at)).map(({id,sha,branch,phase,at,changedFiles})=>({id,sha,branch,phase,at,changedFiles})));
      }
      if (request.method==='GET' && url.pathname==='/compare') {
        const from=await version(url.searchParams.get('from'));
        const to=await version(url.searchParams.get('to'));
        const {stdout}=await git('git',['--literal-pathspecs','diff','--no-ext-diff','--no-textconv','--no-color','--no-renames',from.sha,to.sha,'--',
          ...(url.searchParams.get('filename') ? [url.searchParams.get('filename')] : [])],
          {cwd:root,maxBuffer:2_000_000,timeout:8000});
        const diff=redact(stdout);
        return reply(response,200,{diff:diff.slice(0,140000),truncated:diff.length>140000});
      }
      if (request.method==='POST' && url.pathname==='/restore') {
        const change=await body(request);
        if (change.confirm!==true || (change.filename!==undefined && typeof change.filename!=='string'))
          throw new Error('Review and confirm the restore first.');
        const result=await restoreVersion(await version(change.from),await version(change.to),change.filename);
        return reply(response,200,result);
      }
      if (url.pathname==='/conversations' && request.method==='GET')
        return reply(response,200,presentation.publicPointer(await pointer()));
      if (url.pathname==='/conversations' && request.method==='POST') {
        const change=await body(request);
        if (!['new','select','rename','focus'].includes(change.action)) throw Error('Invalid conversation action.');
        if (change.action==='focus') {
          const state=await pointer();
          const records=JSON.parse(await read(tasksFile) || '{}');
          const task=change.task_id && records[change.task_id];
          const owner=task?.conversationId && state.conversations.find(row=>row.id===task.conversationId);
          change.conversation_id=(owner || (change.task_id && state.active) ||
            state.conversations.find(row=>row.legacy) || state.active).id;
        }
        return reply(response,200,presentation.publicPointer(await pointer(change)));
      }
      if (url.pathname==='/workflow' && request.method==='GET') {
        const visible=await workflow();
        return reply(response,200,presentation.publicWorkflow(visible,project,(await pointer()).presentationExclusions));
      }
      if (url.pathname==='/workflow' && request.method==='POST') {
        const change=await body(request);
        const visible=await serialize(()=>changeWorkflow(change));
        return reply(response,200,presentation.publicWorkflow(visible,project,(await pointer()).presentationExclusions));
      }
      if (url.pathname==='/events' && request.method==='GET') {
        await pointer();
        response.writeHead(200,{'Content-Type':'text/event-stream','Cache-Control':'no-cache, no-transform',
          'X-Accel-Buffering':'no','X-Content-Type-Options':'nosniff'});
        let previous='', closed=false;
        response.on('close',()=>{closed=true;});
        async function notify() {
          if (closed) return;
          try {
            const snapshot=await pointer();
            const size=await fs.stat(conversationLog(snapshot.active)).then(info=>info.size,()=>0);
            const event=JSON.stringify({version:snapshot.version,changed:true});
            const cursor=JSON.stringify([snapshot.version,size,
              [...active.values()].filter(run=>snapshot.focusedTask ? run.objective===snapshot.focusedTask
                : !run.objective && run.conversationId===snapshot.active.id).map(run=>run.state),
              await fs.stat(workflowFile).then(info=>info.mtimeMs,()=>0)]);
            if (cursor!==previous) {
              previous=cursor;
              if (!response.write('data: '+event+'\n\n')) {
                response.end();
                return;
              }
            } else response.write(': keepalive\n\n');
            setTimeout(notify,1500).unref();
          } catch { response.end(); }
        }
        void notify();
        return;
      }
      if (request.method==='GET' && url.pathname==='/task-response') {
        const id=url.searchParams.get('task');
        const state=await pointer();
        const records=JSON.parse(await read(tasksFile) || '{}');
        const task=ids.test(id || '') && records[id];
        const chat=task && state.conversations.find(row=>row.id===id && row.active_task_id===id && row.id===task.conversationId);
        if (!chat) return reply(response,404,{error:'Task conversation not available.'});
        const log=await fs.open(files(task.tree,id).log).catch(error=>{if(error.code!=='ENOENT') throw error;});
        try {
          const size=log ? (await log.stat()).size : 0;
          const stream=size ? log.createReadStream({start:0,end:size-1,autoClose:false}) : undefined;
          const text=await conversationCopy(stream,{project,main:false,excluded:state.presentationExclusions,
            now:Date.now(),mode:'latest'});
          return reply(response,200,{text});
        } finally { if (log) await log.close(); }
      }
      if (request.method==='GET' && url.pathname==='/conversation-copy') {
        const mode=url.searchParams.get('mode');
        const responseId=url.searchParams.get('responseId');
        if (!['latest','history','response'].includes(mode) || mode==='response' && !/^[a-f0-9]{24}$/.test(responseId || '')) return reply(response,400,{error:'Invalid copy action.'});
        const state=await pointer();
        if (String(state.version)!==url.searchParams.get('version'))
          return reply(response,409,{error:'Conversation changed. Try copying again.'});
        const task=state.focusedTask;
        const records=task ? JSON.parse(await read(tasksFile) || '{}') : {};
        const selected=task ? state.conversations.find(item=>item.id===task && item.active_task_id===task) : state.active;
        if (!selected || task && !records[task]?.tree) return reply(response,404,{error:'Conversation not available.'});
        const file=task ? files(records[task].tree,task).log : conversationLog(selected);
        const log=await fs.open(file).catch(error=>{if(error.code!=='ENOENT')throw error;});
        try {
          const size=log ? (await log.stat()).size : 0;
          const stream=size ? log.createReadStream({start:0,end:size-1,autoClose:false}) : undefined;
          const text=await conversationCopy(stream,{project,main:!task,excluded:state.presentationExclusions,
            now:Date.now(),mode,responseId,name:selected.name});
          if ((await pointer()).version!==state.version)
            return reply(response,409,{error:'Conversation changed. Try copying again.'});
          return reply(response,200,{text});
        } finally { if (log) await log.close(); }
      }
      if (request.method==='GET' && url.pathname==='/relationships') {
        const records=JSON.parse(await read(tasksFile) || '{}');
        return reply(response,200,Object.values(records).filter(record=>record.parentTaskId)
          .map(({objective,parentTaskId,dependencies})=>({objective,parentTaskId,dependencies})));
      }
      if (request.method==='GET' && url.pathname==='/trees') return reply(response,200,await trees());
      if (!['/task','/run','/cancel','/reset','/review'].includes(url.pathname)) return reply(response,404,{error:'Unknown route.'});
      const data=request.method==='POST' ? await body(request) : Object.fromEntries(url.searchParams);
      if (!(await trees()).includes(data.tree) || (data.objective && !ids.test(data.objective))) {
        return reply(response,400,{error:'Select an authorized worktree and a valid objective ID.'});
      }
      if (request.method==='GET' && url.pathname==='/review') return reply(response,200,await review(data.tree,data.objective));
      if (request.method==='GET' && url.pathname==='/task') {
        const projectState=await pointer();
        const snapshot=data.project==='1' ? projectState : undefined;
        if (snapshot && data.before && data.version && Number(data.version)!==snapshot.version)
          return reply(response,409,{error:'Task focus changed. Refresh the conversation.'});
        const records=snapshot ? JSON.parse(await read(tasksFile) || '{}') : {};
        const canonical=snapshot?.focusedTask;
        const scope=canonical ? records[canonical]?.tree || 'repository' : data.tree;
        const running=active.get(scope);
        const runs=snapshot && [...active.values()].filter(run=>canonical ? run.objective===canonical :
          !run.objective && run.conversationId===snapshot.active.id);
        const objective=snapshot ? canonical : (data.objective || running?.objective);
        const logPath=objective ? files(scope,objective).log : snapshot && conversationLog(snapshot.active);
        const log=logPath ? await fs.open(logPath).catch(error=>{
          if (error.code !== 'ENOENT') throw error;
        }) : undefined;
        let output='', start=0, end=0;
        if (log) {
          try {
            const size=(await log.stat()).size;
            end=/^\d+$/.test(data.before || '') ? Math.min(size,Number(data.before)) : size;
            const buffer=Buffer.alloc(Math.min(end,100000));
            start=end-buffer.length;
            const {bytesRead}=await log.read(buffer,0,buffer.length,start);
            // Align a truncated page to the next complete line.
            if (start) {
              const newline=buffer.subarray(0,bytesRead).indexOf(10)+1;
              if (!newline) {
                start=end;
              } else {
                start+=newline;
                output=redact(buffer.subarray(newline,bytesRead).toString('utf8'));
              }
            } else output=redact(buffer.subarray(0,bytesRead).toString('utf8'));
          } finally { await log.close(); }
        }
        return reply(response,200,{objective, tree:scope, conversation:presentation.publicPointer(snapshot),
          taskLabels:snapshot && Object.fromEntries(Object.entries(records).slice(-250)
            .map(([id,task])=>[id,'Task '+id.slice(0,8)])),projectRuns:[...active.values()]
          .filter(run=>run.objective).map(({objective,state})=>({objective,state})),  running:snapshot ? Boolean(runs.length) : Boolean(running && running.objective===objective),
          runs:runs?.map(({execution,objective,state})=>({execution:presentation.reference(project,execution),objective,state:presentation.statusLabels.has(state)?state:'executing'})),
          output:presentation.publicTranscript(output,project,{main:!objective,excluded:projectState.presentationExclusions,
            target:process.env.PROJECT_REPOSITORY?.split('/').at(-1)}),start,end});
      }
      if (request.method!=='POST') return reply(response,405,{error:'Method not allowed.'});
      if (url.pathname==='/run') {
        if (!ids.test(data.requestId || '')) throw Error('A valid message request ID is required.');
        if (data.newTask === true && (data.objective || data.explicitTask)) throw Error('New Task cannot target an existing task.');
        await resolveNewTaskInstructions(data);
        const admission=admissions.then(async()=>{
          if (acceptance!==undefined && !ids.test(data.conversationId || ''))
            throw Error('An explicit acceptance conversation is required.');
          const state=await pointer();
          let chat=state.conversations.find(item=>item.id===(data.conversationId || state.active.id));
          if (!chat) throw Error('Conversation not available.');
          const request={conversation_id:chat.id,request_id:data.requestId};
          const taskBinding=data.newTask && !data.objective ? {
            task_payload_sha256:createHash('sha256').update(JSON.stringify([data.prompt,data.title,data.files])).digest('hex')
          } : data.objective && typeof data.waitingReply==='boolean' ? {
            task_payload_sha256:createHash('sha256').update(JSON.stringify([data.objective,data.waitingReply,data.prompt,data.files])).digest('hex')
          } : {};
          const claim=await pointer({action:'claim',...request,...taskBinding,...binding},acceptance);
          if (claim.conversation_id && claim.conversation_id!==chat.id) {
            chat=state.conversations.find(item=>item.id===claim.conversation_id);
            if (!chat) throw Error('Original submission conversation is unavailable.');
            request.conversation_id=chat.id;
          }
          if (!data.newTask && !data.objective) {
            if (!claim.claimed && claim.result) return claim.result;
            const workflowState=await workflow();
            const previous=claim.claimed ? null : workflowState.direct.find(item=>item.messageId===claim.message_id);
            if (!claim.claimed && !previous) {
              const admitted=workflowState.decisions.find(item=>!item.objective && item.conversationId===chat.id &&
                item.answerMessage===claim.message_id && ['answering','resolved','needs_review'].includes(item.status));
              if (admitted) return {messageId:claim.message_id,conversationId:chat.id,tree:'repository',
                executionId:admitted.executionId || admitted.id,status:'resuming'};
              throw Error('This message is already being processed.');
            }
            const decision=!previous && directPolicy.pendingMain(workflowState,chat.id);
            if (decision) {
              let result;
              try { result=await resumeMain(chat,workflowState,decision,data,claim.message_id); }
              catch(error) { await pointer({action:'release',...request}).catch(()=>{}); throw error; }
              await pointer({action:'accepted',...request,result});
              return result;
            }
            let item;
            try { item=previous || await admitDirect(chat,data,claim.message_id,request.request_id); }
            catch(error) { if (claim.claimed) await pointer({action:'release',...request}).catch(()=>{}); throw error; }
            const state=await workflow();
            const current=state.direct.find(entry=>entry.messageId===item.messageId);
            if (current && !current.logged) { await writeDirectMessage(chat,current); await saveWorkflow(state); }
            const result=directReceipt(item);
            await pointer({action:'accepted',...request,result});
            return result;
          }
          if (!claim.claimed) {
            if (claim.result) return claim.result;
            const retained=(await workflow()).items.find(item=>item.id===claim.message_id && item.conversationId===chat.id);
            if (!retained) throw Error('This message is already being processed.');
            const receipt=queuedTaskReceipt(retained);
            await pointer({action:'accepted',...request,result:receipt});
            return receipt;
          }
          let result;
          const {workflow: requestedWorkflow,queueItem,decision,...instruction}=data;
          try {
            if (data.objective && typeof data.waitingReply==='boolean') {
              const workflowState=await workflow();
              const pending=workflowState.decisions.find(row=>row.objective===data.objective &&
                row.conversationId===chat.id && row.status==='pending');
              const replyable=pending && !pending.answerMessage && ids.test(pending.threadId || '');
              if (data.waitingReply && !replyable) {
                result={conflict:pending
                  ? 'The original Codex thread is unavailable. Use the send-message tool to continue the task.'
                  : 'This task is not waiting for a reply. Use the send-message tool to continue the task.'};
              } else if (!data.waitingReply && replyable) {
                result={conflict:'This task is waiting for your reply. Use the waiting-task reply tool.'};
              } else if (replyable) {
                result=await resumeTask(chat,workflowState,pending,{...instruction,strictResume:true},claim.message_id);
              } else {
                result=await start(data.tree,instruction,chat,claim.message_id);
                if (pending) {
                  pending.status='needs_review';
                  await saveWorkflow(workflowState);
                }
              }
            } else {
              const running=[...active].find(([,run])=>run.objective===data.objective &&
                run.conversationId===chat.id && data.objective && data.newTask!==true);
              result=running
                ? await queueTaskInstruction(chat,instruction,claim.message_id,running[0])
                : await start(data.tree,instruction,chat,claim.message_id);
            }
          } catch(error) { await pointer({action:'release',...request}).catch(()=>{}); throw error; }
          if (result.conflict) {
            await pointer({action:'release',...request});
            return result;
          }
          await pointer({action:'accepted',...request,result});
          return result;
        });
        admissions=admission.catch(()=>{});
        const result=await admission;
        if (result.conflict) return reply(response,409,{error:result.conflict});
        const {editorFolder,executionId,...receipt}=result;
        reply(response,202,{...receipt,executionId:presentation.reference(project,executionId)});
        if (!result.objective) scheduleDirect();
        return;
      }
      if (url.pathname==='/cancel') {
        const chat=data.conversationId && ids.test(data.conversationId) ? active.get('chat:'+data.conversationId) : undefined;
        if (chat && !data.objective) { chat.cancelled=true; chat.child?.kill('SIGINT'); }
        const running=active.get(data.tree);
        if (running && running.objective===data.objective) {
          running.cancelled=true;
          running.child?.kill('SIGINT');
        }
      } else if (url.pathname==='/reset' && data.objective && !active.has(data.tree)) {
        await fs.rm(files(data.tree,data.objective).session,{force:true});
      }
      return reply(response,200,{ok:true});
    } catch (error) {
      reply(response,400,{error:'The workspace could not complete this request. Check the selected project and try again.'});
    }
  }).listen(13338,'127.0.0.1');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
