'use strict';
const assert=require('node:assert/strict');
const {test}=require('node:test');
const {nextDirect,nextLegacy,obsoleteMainQuestion,pendingMain,retainedFlatFileReply}=require('../workspace/chat/direct-runs.cjs');
const {conversation}=require('../workspace/chat/timeline.js');

const a={executionId:'first',conversationId:'main',status:'queued'};
const b={executionId:'second',conversationId:'main',status:'queued'};
const legacy={id:'legacy',status:'queued',description:'Create or select a BRIPe task'};
const task={id:'task-instruction',objective:'task',status:'queued'};
const state={direct:[a,b],items:[legacy,task],decisions:[],paused:false};

test('direct admission is FIFO, can run alongside background tasks and waits for a Main writer',()=>{
  assert.equal(nextDirect(state,[{objective:'a background task'}]),a);
  assert.equal(nextDirect(state,[{conversationId:'other main conversation'}]),undefined);
  assert.equal(nextDirect({...state,direct:[{...a,status:'dispatching'},b]},[]),undefined);
  assert.equal(nextDirect({...state,paused:true},[]),a);
});

test('legacy Main instructions stay retained and never block a direct prompt',()=>{
  assert.equal(nextLegacy(state),task);
  assert.equal(nextLegacy({...state,items:[legacy]}),undefined);
  assert.equal(nextLegacy({...state,items:[{...legacy,status:'failed'},task]}),task);
  assert.equal(nextDirect({...state,decisions:[{conversationId:'main',status:'pending'}]},[]),undefined);
  assert.equal(nextDirect({...state,decisions:[{conversationId:'main',objective:'task',status:'pending'}]},[]),a);
});

test('interleaved Main events replay correct per-run status and coalesce a partial response',()=>{
  const event=(type,execution,rest)=>({type,execution,...rest});
  const log=[
    event('smartdev.user','first',{text:'Edit the sidebar'}),
    event('smartdev.run_status','first',{state:'running'}),
    event('smartdev.user','second',{text:'Run the tests'}),
    event('smartdev.run_status','second',{state:'queued'}),
    event('item.updated','first',{item:{id:'answer',type:'agent_message',text:'I checked'}}),
    event('item.completed','first',{item:{id:'answer',type:'agent_message',text:'I checked the files.'}}),
    event('smartdev.result','first',{code:0}),
  ].map(JSON.stringify).join('\n');
  const replay=conversation(log,0,{main:true});
  assert.deepEqual(replay.entries.filter(entry=>entry.type==='user').map(entry=>entry.execution),['first','second']);
  assert.equal(replay.runStates.get('first').state,'completed');
  assert.equal(replay.runStates.get('second').state,'queued');
  assert.deepEqual(replay.entries.filter(entry=>entry.type==='agent').map(entry=>entry.message),['I checked the files.']);
});

test('obsolete Main task gates can be superseded without touching genuine or task-owned questions',()=>{
  const gate={status:'pending',question:'Can you create or select a BRIPe task to admit five leads?'};
  assert.equal(obsoleteMainQuestion(gate),true);
  assert.equal(obsoleteMainQuestion({...gate,objective:'explicit-task'}),false);
  assert.equal(obsoleteMainQuestion({...gate,question:'Which sidebar should I modify?'}),false);
});

test('Main pending reply wins only for its own conversation and retained flat-file recovery is unique',()=>{
  const waiting={id:'question',executionId:'original-run',threadId:'original-thread',
    conversationId:'bripe-main',status:'pending',question:'Can you configure the workspace DATABASE_URL?'};
  const reply={messageId:'answer',executionId:'queued-run',conversationId:'bripe-main',
    prompt:'no DB these are just flat file json',status:'queued'};
  const workflow={direct:[a,reply],decisions:[{...waiting,objective:'another-task'},waiting]};
  assert.equal(pendingMain(workflow,'bripe-main'),waiting);
  assert.equal(pendingMain(workflow,'other-main'),undefined);
  assert.equal(retainedFlatFileReply(workflow),reply);
  assert.equal(retainedFlatFileReply({...workflow,direct:[reply,{...reply,messageId:'another'}]}),undefined);
  assert.equal(retainedFlatFileReply({...workflow,decisions:[{...waiting,status:'resolved'}]}),undefined);
  assert.equal(nextDirect({...workflow,direct:[reply],decisions:[{...waiting,status:'needs_review'}]},[]),undefined);
});

test('replays edits and a single waiting question under the original prompt',()=>{
  const events=[
    {type:'smartdev.user',execution:'first',text:'Original'},
    {type:'smartdev.user',execution:'second',text:'Later prompt'},
    {type:'smartdev.prompt_edit',execution:'first',text:'Revised'},
    {type:'item.completed',execution:'first',item:{id:'one',type:'agent_message',text:'First response\nQuestion: Continue?'}},
    {type:'item.completed',execution:'first',item:{id:'two',type:'agent_message',text:'Question: Continue?'}},
  ];
  const result=conversation(events.map(JSON.stringify).join('\n'),0,{main:true});
  assert.deepEqual(result.entries.map(item=>item.message),['Revised','First response','Continue?','Later prompt']);
});
