// Exercise the real dispatcher and transcript files with the Codex launcher stubbed.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const os=require('node:os');
const path=require('node:path');
const vm=require('node:vm');
const {createRequire}=require('node:module');
const load=createRequire(path.resolve(__dirname,'../workspace/chat/server.cjs'));

test('Main reply records one answer with the original execution and attachment before launch',async()=>{
  const home=await fs.mkdtemp(path.join(os.tmpdir(),'smartdev-main-reply-'));
  const filename=path.resolve(__dirname,'../workspace/chat/server.cjs');
  const source=await fs.readFile(filename,'utf8');
  const calls=[];
  const context=vm.createContext({
    require:name=>name==='node:os'?{homedir:()=>home}:load(name),
    process:{env:{PROJECT_NAME:'fixture'}},
    console,
    launch:async(tree,prompt,chat,message)=>{
      calls.push({tree,prompt,chat,message});
      if (calls.length===1) throw Error('Launcher temporarily unavailable');
      return {executionId:prompt.resumeExecution,messageId:message,tree};
    },
    fixtureUpload:async id=>({id,originalName:'evidence.json',image:false}),
  });
  try {
    const functions=vm.runInContext(source.slice(0,source.lastIndexOf('main().catch'))+
      '\nstart=launch; getUpload=fixtureUpload; ({resumeMain, workflow, conversationLog, storage})',context);
    await fs.mkdir(functions.storage,{recursive:true});
    const chat={id:'11111111-1111-4111-8111-111111111111',legacy:true};
    const thread='22222222-2222-4222-8222-222222222222';
    const decision={id:'question',threadId:thread,executionId:'original-execution',conversationId:chat.id,status:'pending'};
    const workflow={decisions:[decision],items:[],direct:[]};
    const instruction={prompt:'The evidence is JSON.',files:[{id:'uploaded-file'}]};
    await assert.rejects(functions.resumeMain(chat,workflow,decision,instruction,'answer-1'),/temporarily unavailable/);
    assert.equal((await functions.workflow()).decisions[0].status,'pending');
    const result=await functions.resumeMain(chat,workflow,decision,instruction,'answer-1');
    assert.equal(result.executionId,decision.executionId);
    assert.equal(result.status,'resuming');
    assert.equal(calls[0].prompt.resumeThread,thread);
    assert.equal(calls[0].prompt.files[0].id,'uploaded-file');
    const saved=await functions.workflow();
    assert.equal(saved.decisions[0].status,'answering');
    assert.equal(saved.decisions[0].answerMessage,'answer-1');
    const message=JSON.parse((await fs.readFile(functions.conversationLog(chat),'utf8')).trim());
    assert.equal(message.execution,decision.executionId);
    assert.equal(message.attachments[0].originalName,'evidence.json');
  } finally {
    await fs.rm(home,{recursive:true,force:true});
  }
});
