// Run only against an authorized disposable project with a sidebar to edit.
const {acceptanceContext}=require('./acceptance_browser.cjs');
const assert=require('node:assert/strict');
const {chromium}=require(process.env.SMARTDEV_PLAYWRIGHT || 'playwright');

(async()=>{
  const url=process.env.SMARTDEV_PORTAL_URL;
  const project=process.env.SMARTDEV_TEST_PROJECT;
  assert(url && project,'Set the test portal and disposable project name.');
  const args=[];
  if (process.env.SMARTDEV_TEST_RESOLVER) args.push('--host-resolver-rules='+process.env.SMARTDEV_TEST_RESOLVER);
  const browser=await chromium.launch({args});
  try {
    const page=await (await acceptanceContext(browser,{viewport:{width:1440,height:900}})).newPage();
    await page.goto(process.env.SMARTDEV_TEST_CHAT_URL);
    const projectId=process.env.SMARTDEV_TEST_PROJECT_ID;
    await page.evaluate(async()=>{
      const initial=await fetch('conversations');
      if (!initial.ok) throw Error('Acceptance conversation is unavailable.');
      const state=await initial.json();
      const response=await fetch('conversations',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({action:'focus',version:state.version,task_id:null})});
      if (!response.ok) throw Error('Could not select the isolated Main conversation.');
    });
    const health=await (await page.request.get(new URL('/api/health',url).href)).json();
    assert.equal(health.main_execution_protocol,'workspace-write-v1','Mismatched API or unapplied migration');
    const frame=page;
    const prompt=frame.locator('#prompt');
    await frame.locator('#tree option[value="repository"]').waitFor({state:'attached',timeout:45000});
    await frame.locator('#tree').evaluate(tree=>{
      if (tree.value!=='repository') throw Error('Main is not focused on the repository checkout.');
    });
    const objectives=async()=>{
      const response=await page.request.get(new URL('/api/projects/'+projectId+'/objectives',url).href);
      assert.equal(response.ok(),true);
      return (await response.json()).map(item=>item.id).sort();
    };
    const before=await objectives();
    const instruction='Add Hello, username under the left sidebar logo.';
    const admission=page.waitForResponse(response=>response.url().endsWith('/run') && response.request().method()==='POST');
    await prompt.fill(instruction);
    await prompt.press('Enter');
    const response=await admission;
    assert.equal(response.status(),202);
    const run=await response.json();
    assert.equal(run.tree,'repository');
    assert.equal(run.objective,undefined);
    assert.match(run.executionId,/^[a-f0-9-]{36}$/);
    const timeout=Date.now()+180_000;
    let events=[];
    const history=()=>frame.locator('#output').evaluate(async()=>{
      const response=await fetch('task?tree=repository&project=1');
      if (!response.ok) throw Error('Could not read Main transcript');
      return (await response.json()).output;
    });
    while (Date.now()<timeout) {
      events=(await history()).split('\n').flatMap(line=>{try{return [JSON.parse(line)];}catch{return [];}});
      events=events.filter(event=>event.execution===run.executionId);
      if (events.some(event=>event.type==='smartdev.result')) break;
      await new Promise(resolve=>setTimeout(resolve,1500));
    }
    assert.equal(events.find(event=>event.type==='smartdev.result')?.code,0,'Main did not finish successfully');
    assert(events.some(event=>event.type==='smartdev.user' && event.text===instruction));
    assert(!events.some(event=>event.item?.type==='agent_message' &&
      /create or select (?:a |an )?.{0,35}task/i.test(event.item.text)));
    const checkpoint=events.find(event=>event.type==='smartdev.checkpoint')?.checkpoint;
    assert(checkpoint,'No Main source checkpoint was captured');
    const saved=await frame.locator('#output').evaluate(async id=>{
      const response=await fetch('history');
      if (!response.ok) throw Error('Could not read checkpoint metadata');
      return (await response.json()).find(record=>record.id===id);
    },checkpoint);
    assert.equal(saved?.execution,run.executionId);
    assert.equal(saved?.objective,null);
    assert.equal(saved?.worktree,'repository');
    assert(saved.changedFiles.length,'Checkpoint contains no changed files');
    assert(events.some(event=>event.item?.type==='command_execution' && event.item.exit_code===0 &&
      /\b(test|pytest|unittest|playwright|vitest|jest)\b/i.test(event.item.command)),
      'No successful test command was recorded');
    assert.deepEqual(await objectives(),before);
    await page.reload();
    await frame.locator('.chat-event.user').getByText(instruction,{exact:true}).waitFor({timeout:45000});
    assert((await history()).includes(run.executionId),'Run did not persist in Main after refresh');
    const thread=events.find(event=>event.type==='thread.started')?.thread_id;
    assert(thread,'Main did not record a Codex thread');
    const followUp='In the same checkout, tell me which file you changed in the previous turn. Do not modify files.';
    const followUpAdmission=page.waitForResponse(response=>response.url().endsWith('/run') && response.request().method()==='POST');
    await prompt.fill(followUp);
    await prompt.press('Enter');
    const followUpResponse=await followUpAdmission;
    assert.equal(followUpResponse.status(),202);
    const second=await followUpResponse.json();
    assert.notEqual(second.executionId,run.executionId);
    assert.equal(second.objective,undefined);
    const followUpDeadline=Date.now()+180_000;
    let secondEvents=[];
    while (Date.now()<followUpDeadline) {
      secondEvents=(await history()).split('\n').flatMap(line=>{try{return [JSON.parse(line)];}catch{return [];}});
      secondEvents=secondEvents.filter(event=>event.execution===second.executionId);
      if (secondEvents.some(event=>event.type==='smartdev.result')) break;
      await new Promise(resolve=>setTimeout(resolve,1500));
    }
    assert.equal(secondEvents.find(event=>event.type==='smartdev.result')?.code,0);
    assert.equal(secondEvents.find(event=>event.type==='thread.started')?.thread_id,thread);
    assert.deepEqual(await objectives(),before);
    console.log(JSON.stringify({status:'passed',executionId:run.executionId,followUp:second.executionId,checkpoint,changedFiles:saved.changedFiles,taskCount:before.length,taskDelta:0}));
  } finally {await browser.close();}
})().catch(error=>{console.error(error.message);process.exitCode=1;});
