// Seeds only an empty, disposable checkout. Run against that exact mounted checkout.
const {acceptanceContext,fixtureRepository}=require('./acceptance_browser.cjs');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const {randomUUID}=require('node:crypto');
const {setTimeout:delay}=require('node:timers/promises');

async function seed(root,marker) {
  for (const dir of ['docs','src','.agents/skills/smartdev-acceptance'])
    await fs.mkdir(path.join(root,dir),{recursive:true});
  for (const [name,value] of Object.entries({
    'AGENTS.md': 'For this fixture, include ROOT-'+marker+' in the final response. Project facts live in docs/architecture.md. Test with node --test src/widget.test.cjs.\n',
    'docs/architecture.md':'The current project fact is FACT-'+marker+'\n',
    'src/AGENTS.md':'When editing widget.txt, preserve its first line, NESTED-'+marker+'.\n',
    'src/widget.txt':'NESTED-'+marker+'\n',
    'src/widget.test.cjs':"const {test}=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');test('greeting preserves governed first line',()=>{const lines=fs.readFileSync(__dirname+'/widget.txt','utf8').trim().split('\\n');assert.equal(lines[0],'NESTED-"+marker+"');assert.equal(lines[1],'Hello fixture');});\n",
    '.agents/skills/smartdev-acceptance/SKILL.md':'---\nname: smartdev-acceptance\ndescription: Use for the SmartDev controlled project acceptance fixture.\n---\nInclude SKILL-'+marker+' in the final reply.\n',
  })) await fs.writeFile(path.join(root,name),value,{flag:'wx'});
}

(async()=>{
  const {SMARTDEV_CONTEXT_REPOSITORY:root,SMARTDEV_PORTAL_URL:url,SMARTDEV_TEST_PROJECT:project,
    }=process.env;
  assert(root && url && project,'Specify the mounted disposable checkout, DEV URL, project and password file.');
  assert.deepEqual((await fs.readdir(root)).filter(name=>name!=='.git'),[],
    'The context fixture requires an empty disposable checkout.');
  const marker=randomUUID();
  const {chromium}=require(process.env.SMARTDEV_PLAYWRIGHT||'playwright');
  const browser=await chromium.launch();
  try {
    const context=await acceptanceContext(browser);
    const page=await context.newPage();
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
    const count=async()=>{
      const response=await page.request.get(new URL('/api/projects/'+projectId+'/objectives',url).href);
      assert(response.ok());
      return (await response.json()).length;
    };
    assert.equal(await fs.realpath(root),await fixtureRepository(process.env.SMARTDEV_TEST_FIXTURE_CONTAINER),
      'The source fixture must be mounted in the verified acceptance workspace.');
    await seed(root,marker);
    const before=await count();
    const chat=page;
    const prompt=chat.locator('#prompt');
    async function send(text){
      const receipt=page.waitForResponse(r=>r.url().endsWith('/run') && r.request().method()==='POST');
      await prompt.fill(text);
      await prompt.press('Enter');
      const response=await receipt;
      assert.equal(response.status(),202);
      const run=await response.json();
      assert.equal(run.objective,undefined);
      assert.equal(run.tree,'repository');
      return run;
    }
    async function events(runId,predicate) {
      const deadline=Date.now()+180000;
      while(Date.now()<deadline){
        const output=await chat.locator('#output').evaluate(async()=>{
          const response=await fetch('task?tree=repository&project=1');
          if(!response.ok) throw Error('Main transcript unavailable');
          return (await response.json()).output;
        });
        const rows=output.split('\n').flatMap(line=>{try{return [JSON.parse(line)];}catch{return [];}})
          .filter(row=>row.execution===runId);
        if(predicate(rows)) return rows;
        await delay(1000);
      }
      throw Error('Timed out waiting for execution events');
    }
    const initial=await send('Use the native smartdev-acceptance skill. Inspect the project architecture fact. Add a greeting to src/widget.txt, but ask me which greeting to use before editing. Follow the project instructions and run the fixture tests after my answer.');
    const question=await events(initial.executionId,rows=>rows.some(row=>row.type==='smartdev.result' && row.awaitingInput));
    const thread=question.find(row=>row.type==='thread.started')?.thread_id;
    assert(thread,'The original Codex thread was not recorded.');
    await page.reload();
    await chat.locator('#run-label').getByText(/Waiting for input/).waitFor({timeout:45000});
    const resumed=await send('Use Hello fixture as the greeting.');
    assert.equal(resumed.executionId,initial.executionId);
    assert.equal(resumed.status,'resuming');
    const finished=await events(initial.executionId,rows=>rows.filter(row=>row.type==='smartdev.result').length>=2);
    assert.equal(finished.filter(row=>row.type==='smartdev.result').at(-1).code,0);
    const answerIndex=finished.findIndex(row=>row.type==='smartdev.user' && row.text==='Use Hello fixture as the greeting.');
    assert(answerIndex>=0);
    assert.equal(finished.slice(answerIndex+1).find(row=>row.type==='thread.started')?.thread_id,thread,
      'Native Codex did not report the original thread on Reply');
    assert.equal(finished.filter(row=>row.type==='smartdev.user' && row.text==='Use Hello fixture as the greeting.').length,1);
    assert.equal(await fs.readFile(path.join(root,'src/widget.txt'),'utf8'),'NESTED-'+marker+'\nHello fixture\n');
    const testEvent=finished.some(row=>row.item?.type==='command_execution' && row.item.exit_code===0 && /node --test/.test(row.item.command));
    assert(testEvent,'No successful fixture test command appeared in the managed transcript');
    const messages=finished.map(row=>row.item?.text || '').join('\n');
    for (const prefix of ['ROOT-','SKILL-','FACT-']) assert(messages.includes(prefix+marker),'Missing native fixture evidence for '+prefix);
    const revision='FACT-'+randomUUID();
    await fs.writeFile(path.join(root,'docs/architecture.md'),'The current project fact is '+revision+'\n');
    const updated=await send('Read the current architecture document again and report its current project fact. Do not change files.');
    const updatedRows=await events(updated.executionId,rows=>rows.some(row=>row.type==='smartdev.result'));
    assert.equal(updatedRows.find(row=>row.type==='smartdev.result').code,0);
    assert(updatedRows.some(row=>row.item?.type==='agent_message' && row.item.text.includes(revision)),'Updated architecture fact was not used');
    assert.equal(await count(),before,'Main created an unexpected task');
    console.log(JSON.stringify({status:'passed',main:initial.executionId,thread,contextRefresh:updated.executionId}));
  } finally {await browser.close();}
})().catch(error=>{console.error(error.message);process.exitCode=1;});
