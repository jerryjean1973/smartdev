const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {test}=require('node:test');
const {TextEncoder}=require('node:util');
const project=path.join(__dirname,'../workspace/chat/timeline.js');
const parse=vm.runInNewContext(fs.readFileSync(project,'utf8')+'\nconversation',{TextEncoder});

test('Main excludes task messages and tool evidence without changing stable event offsets',()=>{
  const items=[
    {type:'smartdev.user',objective:'task-id',text:'Task-only request',execution:'task'},
    {type:'item.completed',objective:'task-id',execution:'task',item:{id:'tool',type:'command_execution',command:'git status',exit_code:0}},
    {type:'smartdev.user',text:'Main question',execution:'main'},
    {type:'item.completed',execution:'main',item:{type:'agent_message',text:'Main answer'}},
  ];
  const input=items.map(item=>JSON.stringify(item)).join('\n');
  const main=parse(input,0,{main:true}).entries;
  const full=parse(input).entries;
  assert.equal(main.length,2);
  assert.equal(main[0].message,'Main question');
  assert.equal(main[0].id,full[2].id);
  assert.equal(main[1].message,'Main answer');
});

test('groups real tool updates under a stable action ID and keeps challenge evidence',()=>{
  const items=[
    {type:'item.started',execution:'task',item:{id:'tool',type:'command_execution',command:'git status'}},
    {type:'item.completed',execution:'task',item:{id:'tool',type:'command_execution',command:'git status',exit_code:1,aggregated_output:'failure evidence'}},
    {type:'smartdev.challenge',execution:'task',stage:'review',status:'complete',artifact:{finding:'review evidence'}},
  ];
  const group=parse(items.map(item=>JSON.stringify(item)).join('\n')).entries[0];
  assert.equal(group.actions.length,2);
  assert.equal(group.actions[0].id,'0');
  assert.equal(group.actions[0].state,'failed');
  assert.match(group.actions[0].data,/failure evidence/);
  assert.match(group.actions[1].data,/review evidence/);
});
