const assert=require('node:assert/strict');
const {test}=require('node:test');
const {publicTranscript,publicWorkflow,publicPointer,diagnosticSummary}=require('../workspace/chat/public-events.cjs');
const raw=[
 {type:'smartdev.user',execution:'internal-run',text:'Fix the project'},
 {type:'item.started',execution:'internal-run',item:{type:'command_execution',command:'docker exec internal-name cat /var/run/docker.sock'}},
 {type:'item.completed',execution:'internal-run',item:{type:'command_execution',command:'npm test with AWS_SECRET_ACCESS_KEY=unchecked',exit_code:0,aggregated_output:'private stack trace; super-secret-value'}},
 {type:'item.completed',execution:'internal-run',item:{type:'mcp_tool_call',tool:'private-mcp-name',arguments:{token:'token-value'}}},
 {type:'item.completed',execution:'internal-run',item:{type:'agent_message',text:'Changed src/login.ts. DATABASE_URL=postgres://test:pw@internal:5432'}},
 {type:'item.updated',execution:'internal-run',item:{type:'agent_message',text:'partial-secret-not-sanitized'}},
 {type:'thread.started',thread_id:'private-native-thread'},
 {type:'smartdev.diagnostic',text:'arbitrary internal env'},
 {type:'smartdev.result',execution:'internal-run',code:1,error:'/root/.codex stack trace'},
].map(JSON.stringify).join('\n');

test('history response contains only safe progress, narrative and outcome',()=>{
 const actual=publicTranscript(raw,'project',{main:true});
 for(const forbidden of ['internal-run','docker exec','docker.sock','AWS_SECRET_ACCESS_KEY','super-secret-value','private-mcp-name','token-value','postgres://','partial-secret','private-native-thread','arbitrary internal env','/root/.codex'])
   assert(!actual.includes(forbidden),forbidden);
 assert(actual.includes('Preparing deployment'));
 assert(actual.includes('Test command passed'));
 assert(actual.includes('src/login.ts'));
 assert(actual.includes('could not finish'));
 assert.deepEqual(diagnosticSummary(raw),{commands:1,edits:0,integrations:1,failed:1});
});

test('Main projection and workflow do not disclose task or native execution IDs',()=>{
 const another=JSON.stringify({type:'smartdev.user',objective:'other-task',text:'Task prompt'});
 assert(!publicTranscript(another,'project',{main:true}).includes('Task prompt'));
 const pointer=publicPointer({version:1,focusedTask:null,active:{id:'chat',name:'Main',codex_session_id:'hidden'},conversations:[]});
 assert(!JSON.stringify(pointer).includes('hidden'));
 const workflow=publicWorkflow({paused:false,items:[],decisions:[{id:'raw-run',executionId:'raw-run',threadId:'hidden',question:'Which file?',status:'pending'}]},'project');
 assert.notEqual(workflow.decisions[0].id,'raw-run');
 assert(!JSON.stringify(workflow).includes('hidden'));
 assert.equal(workflow.decisions[0].question,'Which file?');
});

test('historical visibility decisions suppress exact runs and their pending replies',()=>{
 const excluded=['acceptance-run'];
 const fixture=[
  {type:'smartdev.user',execution:'acceptance-run',text:'synthetic prompt'},
  {type:'item.completed',execution:'acceptance-run',item:{type:'agent_message',text:'synthetic reply'}},
  {type:'smartdev.user',execution:'legitimate-run',text:'Mention synthetic prompt in a real question'},
 ].map(JSON.stringify).join('\n');
 const result=publicTranscript(fixture,'project',{main:true,excluded});
 assert.equal(JSON.parse(result).text,'Mention synthetic prompt in a real question');
 const view=publicWorkflow({paused:false,items:[],decisions:[
  {id:'reply',executionId:'acceptance-run',question:'Fixture question',status:'pending'},
  {id:'another',executionId:'legitimate-run',question:'Legitimate question',status:'pending'}
 ]},'project',excluded);
 assert.deepEqual(view.decisions.map(item=>item.question),['Legitimate question']);
});

test('skill promotion proposals use scoped references and respect excluded executions',()=>{
  const proposal={id:'internal-proposal',executionId:'internal-run',conversationId:'chat',
    name:'audit',description:'Review architecture',filePath:'.agents/skills/audit/SKILL.md',
    previous:'Previous draft',content:'Proposed draft',status:'pending'};
  const state={items:[],decisions:[],skillPromotions:[proposal]};
  const [visible]=publicWorkflow(state,'one').skillPromotions;
  assert.equal(visible.content,'Proposed draft');
  assert.equal(visible.status,'pending');
  assert.notEqual(visible.id,proposal.id);
  assert.notEqual(visible.id,publicWorkflow(state,'two').skillPromotions[0].id);
  assert.equal(visible.executionId,undefined);
  assert.deepEqual(publicWorkflow(state,'one',['internal-run']).skillPromotions,[]);
});
