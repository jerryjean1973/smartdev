const {acceptanceContext}=require('./acceptance_browser.cjs');
const assert=require('node:assert/strict');
const {randomUUID}=require('node:crypto');
const {chromium}=require(process.env.SMARTDEV_PLAYWRIGHT || 'playwright');

(async()=>{
  const browser=await chromium.launch();
  try {
    const base=process.env.SMARTDEV_TEST_CHAT_URL;
    const mobile=await acceptanceContext(browser,{viewport:{width:430,height:900},isMobile:true,hasTouch:true});
    const desktop=await acceptanceContext(browser);
    const [first,second]=await Promise.all([mobile.newPage(),desktop.newPage()]);
    await Promise.all([first.goto(base),second.goto(base)]);
    async function focus(page,taskId) {
      await page.evaluate(async id=>{
        const state=await (await fetch('conversations')).json();
        const response=await fetch('conversations',{method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({action:'focus',version:state.version,task_id:id})});
        if (!response.ok) throw Error('Could not focus the isolated conversation.');
      },taskId);
    }
    await focus(first,null);
    const prompt=first.locator('#prompt');
    const marker='Report the current Git branch. Acceptance '+randomUUID();
    const receipt=first.waitForResponse(response=>response.url().endsWith('/run') && response.request().method()==='POST');
    await prompt.fill(marker);
    await prompt.press('Enter');
    const response=await receipt;
    assert.equal(response.status(),202);
    const main=await response.json();
    assert.equal(main.objective,undefined);
    await second.locator('.chat-event.user').getByText(marker,{exact:true}).waitFor({timeout:45000});
    const task=await second.evaluate(async ({conversationId,requestId})=>{
      const response=await fetch('run',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({conversationId,requestId,tree:'repository',newTask:true,
          prompt:'Read the repository branch in this explicit acceptance task. Do not edit files.',files:[]})});
      if (response.status!==202) throw Error('Explicit task admission failed.');
      return response.json();
    },{conversationId:main.conversationId,requestId:randomUUID()});
    assert(task.objective && task.conversationId!==main.conversationId);
    await focus(second,task.objective);
    await first.locator('.chat-event.user').getByText('Read the repository branch in this explicit acceptance task. Do not edit files.',{exact:true}).waitFor({timeout:45000});
    await first.reload();
    await first.locator('.chat-event.user').getByText('Read the repository branch in this explicit acceptance task. Do not edit files.',{exact:true}).waitFor({timeout:45000});
    await focus(second,null);
    await first.locator('.chat-event.user').getByText(marker,{exact:true}).waitFor({timeout:45000});
    assert.equal(await first.locator('.chat-event.user').getByText(marker,{exact:true}).count(),1);
    console.log('PASS isolated Main, dedicated task, shared test pointer and reconnect');
  } finally {await browser.close();}
})().catch(error=>{console.error(error.message);process.exitCode=1;});
