// Run with jsdom available on NODE_PATH. No live accounts or workspace processes are used.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {webcrypto}=require('node:crypto');
const path=require('node:path');
const {JSDOM}=require('jsdom');
const {setTimeout:delay}=require('node:timers/promises');
const root=path.join(__dirname,'..');
const source=file=>fs.readFileSync(path.join(root,file),'utf8');

async function until(condition) {
  for (let attempt=0;attempt<80;attempt++) {
    if (condition()) return;
    await delay(10);
  }
  assert.fail('Expected UI state did not arrive.');
}

for (const mode of ['task','main','admission']) test(mode==='admission' ? 'Main retains submission identity after a lost acknowledgment' : mode+' chat routes a pending answer once and preserves the composer draft', async () => {
  const dom=new JSDOM(source('workspace/chat/index.html'),{
    url:'https://coder.smartdev.youkandoit.com/apps/codex-chat/',runScripts:'outside-only',pretendToBeVisual:true,
  });
  const {window}=dom;
  const {document}=window;
  const task='22222222-2222-4222-8222-222222222222';
  const tree='worktrees/task-'+task;
  const active={id:'33333333-3333-4333-8333-333333333333',name:'Main',legacy:true};
  const initial=mode==='task'?task:null;
  let focused=initial;
  let version=1;
  const sent=[];
  const submissions=[];
  const question={id:'ask-1',objective:initial,conversationId:active.id,status:'pending',question:'Which installation should I use?'};
  const pointer=()=>({active,conversations:[active,{id:task,active_task_id:task,name:"Dedicated task"}],version,focusedTask:focused});
  const decisions=()=>sent.length || mode==='admission' ? [] : [question];
  const records=[
    {type:'smartdev.user',objective:initial,text:'Check Git access'},
    {type:'item.completed',objective:initial,item:{type:'agent_message',text:'I checked **repository access**.'}},
    {type:'item.completed',objective:initial,item:{type:'command_execution',id:'command-1',command:'git status',exit_code:0}},
  ].map(JSON.stringify).join('\n');
  window.TextEncoder=TextEncoder;
  Object.defineProperty(window.crypto,'subtle',{value:webcrypto.subtle});
  window.CSS={escape:value=>value};
  window.HTMLElement.prototype.scrollIntoView=()=>{};
  window.EventSource=class {};
  window.fetch=async (url,options)=>{
    const endpoint=String(url).split('?')[0];
    const data=options.body && JSON.parse(options.body);
    const workflow=()=>({decisions:decisions(),items:[],paused:false});
    if (endpoint==='run') {
      submissions.push(data);
      if (submissions.length===1) throw Error('Connection lost after send');
      return {ok:true,json:async()=>({tree:'repository',conversationId:active.id})};
    }
    const result={
      trees:()=>['repository',tree],relationships:()=>[],
      task:()=>({tree:focused?tree:'repository',objective:focused,conversation:pointer(),
        output:records,running:false,runs:[],projectRuns:[]}),
      conversations:()=>{
        if (data?.action==='focus') {focused=data.task_id;version++;}
        return pointer();
      },
      workflow:()=>{if(data?.action==='answer')sent.push(data);return workflow();},
    }[endpoint];
    assert.ok(result, 'Unexpected request: '+endpoint);
    return {ok:true,json:async()=>result()};
  };
  try {
    window.eval([
      'workspace/chat/timeline.js','workspace/chat/browser.js','workspace/codex-browser/chat.js',
    ].map(source).join('\n'));
    const prompt=document.getElementById('prompt');
    const decisionPanel=document.getElementById('decisions');
    if (mode==='admission') {
      await until(()=>document.getElementById('output').textContent.includes('Check Git access'));
      prompt.value='Add Hello, username under the left sidebar logo.';
      document.getElementById('run').click();
      await until(()=>document.getElementById('status').textContent.includes('Connection lost'));
      assert.equal(window.localStorage.length>0,true);
      document.getElementById('run').click();
      await until(()=>submissions.length===2);
      assert.equal(submissions[0].requestId,submissions[1].requestId);
      assert.equal(submissions[0].newTask,false);
      assert.equal(submissions[0].objective,undefined);
      return;
    }
    await until(()=>decisionPanel.querySelector('button'));
    assert.equal(decisionPanel.open,false);
    assert.equal(document.getElementById('saved-conversations').hidden,true);
    assert.doesNotMatch(document.getElementById('saved-conversations').textContent,/Dedicated task/);
    assert.equal(document.getElementById('execution-queue').open,false);
    assert.equal(document.getElementById('cancel').hidden,true);
    const actions=document.querySelector('.chat-actions');
    assert.equal(actions.open,false);
    assert.match(actions.querySelector('summary').textContent,/^Thinking$/);
    const diagnostics=[...document.querySelectorAll('.chat-actions')]
      .find(detail=>detail.querySelector('summary').textContent.includes('diagnostics') && detail.textContent.includes('git status'));
    assert.ok(diagnostics);
    assert.equal(diagnostics.open,false);
    prompt.value='An unfinished task draft';
    prompt.dispatchEvent(new window.Event('input'));
    decisionPanel.querySelector('button').click();
    await until(()=>!document.getElementById('reply-context').hidden);
    assert.equal(document.activeElement,prompt);
    assert.equal(prompt.value,'');
    prompt.value='Use the project installation.';
    prompt.dispatchEvent(new window.KeyboardEvent('keydown',{key:'Enter',cancelable:true}));
    await until(()=>decisionPanel.hidden);
    assert.equal(sent.length,1);
    assert.deepEqual([sent[0].id,sent[0].conversationId,sent[0].description],
      [question.id,active.id,'Use the project installation.']);
    assert.equal(prompt.value,'An unfinished task draft');
  } finally {
    window.close();
  }
});
