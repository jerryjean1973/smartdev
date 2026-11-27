const assert=require('node:assert/strict');
const {test}=require('node:test');
const {Readable}=require('node:stream');
const {conversationCopy}=require('../workspace/chat/conversation-copy.cjs');
const now=Date.parse('2026-11-22T12:00:00.000Z');
const cutoff=now-48*3600000;
const at=ms=>new Date(ms).toISOString();
const user=(text,when,extra={})=>({type:'smartdev.user',text,at:at(when),...extra});
const agent=(text,id,when,type='item.completed',extra={})=>({
  type,at:at(when),execution:'fixture-run',item:{type:'agent_message',text,id},...extra});
const copy=(mode,events)=>conversationCopy(Readable.from(events.map(x=>JSON.stringify(x)+'\n')),
  {project:'fixture-project',main:true,excluded:['excluded'],now,mode,name:'Main'});

test('latest response is one logical item, with updates superseded by completion',async()=>{
  const markdown='  indented line\n\n## Result\n\n- Parent\n  - Nested\n\n```text\n'+ 'x'.repeat(110000)+'\n```';
  assert.equal(await copy('latest',[
    agent('Earlier','1',now-4),user('Prompt',now-3),
    agent('First snapshot','2',now-2,'item.updated'),
    agent(markdown,'2',now-1),
    agent('Late older update','1',now,'item.updated'),
    agent('Excluded','3',now,'item.completed',{execution:'excluded'}),
  ]),markdown);
  assert.equal(await copy('latest',[agent('Visible stream','1',now,'item.updated')]),'Visible stream');
  assert.equal(await copy('latest',[user('Only user',now)]),'');
});

test('history includes cutoff, sorts eligible messages, and excludes private and other-task rows',async()=>{
  const history=await copy('history',[
    agent('Second','1',now-1),
    user('Cutoff',cutoff,{message:'same'}),
    user('Cutoff',cutoff,{message:'same'}),
    user('Too old',cutoff-1),
    user('Private',now,{execution:'excluded'}),
    user('Task row',now,{objective:'other-task'}),
    {type:'item.completed',at:at(now),item:{type:'command_execution',command:'Private command'}},
  ]);
  assert.match(history,/2026-11-20T12:00:00.000Z — User\n\nCutoff/);
  assert.ok(history.indexOf('Cutoff') < history.indexOf('Second'));
  assert.equal(history.match(/Cutoff/g).length,1);
  assert.doesNotMatch(history,/Too old|Private|Task row/);
});

test('copies a selected earlier response through the same privacy projection',async()=>{
  const {publicTranscript}=require('../workspace/chat/public-events.cjs');
  const previous=agent('## Earlier\n\n- Parent\n  - Nested','earlier',now-2);
  const responseId=JSON.parse(publicTranscript(JSON.stringify(previous),'fixture-project')).item.copyId;
  const scan=events=>conversationCopy(Readable.from(events.map(event=>JSON.stringify(event)+'\n')),
    {project:'fixture-project',main:true,excluded:['excluded'],now,mode:'response',responseId});
  assert.equal(await scan([previous,agent('Latest','latest',now-1)]),previous.item.text);
  assert.equal(await scan([agent('Latest','latest',now-1)]),'');
});
