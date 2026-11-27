const {acceptancePortalContext}=require('./acceptance_browser.cjs');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {chromium}=require(process.env.SMARTDEV_PLAYWRIGHT || 'playwright');

(async()=>{
  const root=process.env.SMARTDEV_PORTAL_URL;
  const password=fs.readFileSync(process.env.SMARTDEV_TEST_PASSWORD_FILE,'utf8');
  const argumentsForChromium=['--no-sandbox'];
  if(process.env.SMARTDEV_TEST_RESOLVER) argumentsForChromium.push('--host-resolver-rules='+process.env.SMARTDEV_TEST_RESOLVER);
  const browser=await chromium.launch({args:argumentsForChromium});
  try {
    async function open(mobile) {
      const context=await acceptancePortalContext(browser,mobile?{viewport:{width:430,height:890},isMobile:true,hasTouch:true}:{});
      const page=await context.newPage();
      const sockets=new Set(),successful=new Set();
      page.on('websocket',socket=>socket.on('framereceived',()=>sockets.add(new URL(socket.url()).pathname)));
      page.on('response',response=>{
        const path=new URL(response.url()).pathname;
        if(response.ok() && path.includes('/apps/dev-preview/api/')) successful.add(path);
      });
      await page.goto(root);
      await page.getByLabel('Password').fill(password);
      await page.getByRole('button',{name:'Sign in',exact:true}).click();
      const select=page.getByLabel('Select project');
      await select.selectOption(process.env.SMARTDEV_TEST_PROJECT_ID);
      const base=root+'/api/projects/'+await select.inputValue();
      const panel=page.locator('#preview-panel');
      await panel.getByRole('button',{name:'Preview',exact:true}).click();
      const frame=page.frameLocator('iframe[title="DEV application preview"]');
      await frame.getByRole('heading',{name:process.env.SMARTDEV_TEST_PREVIEW_HEADING || 'Live fixture v1'}).waitFor({timeout:150000});
      const publication=async()=>await (await page.request.get(base+'/dev-publishing')).json();
      const state=await publication();
      assert(state.gateway && state.ready && state.url);
      const dev=new URL(state.url);
      assert.equal(new URL(await page.locator('iframe[title="DEV application preview"]').getAttribute('src')).pathname,dev.pathname);
      const liveness=await page.request.get(new URL('health/live',dev).href);
      assert.equal((await liveness.json()).status,'alive');
      const app=async()=>await (await page.request.get(new URL('.__smartdev/status',dev).href)).json();
      return {context,page,panel,frame,publication,dev,app,sockets,successful};
    }
    const desktop=await open(false);
    assert(desktop.successful.size>0);
    console.log('PASS actual disposable project desktop UI, HTTP liveness, application APIs and registry URL');
    console.log('WebSockets with received frames:',[...desktop.sockets]);
    const before=await desktop.app();
    await desktop.page.locator('.wb-menu > summary').click();
    await desktop.page.getByRole('button',{name:'Project settings',exact:true}).click();
    await desktop.panel.getByRole('button',{name:'Restart application',exact:true}).click();
    let after;
    for(let attempt=0;attempt<120;attempt++){
      after=await desktop.app();
      if(after.status==='ready' && after.generation>before.generation) break;
      await desktop.page.waitForTimeout(1000);
    }
    assert(after.status==='ready' && after.generation>before.generation);
    assert.equal((await desktop.publication()).url,desktop.dev.href);
    console.log('PASS real application restart retained URL; upstream ports',before.port,after.port);
    const mobile=await open(true);
    assert.equal(mobile.dev.href,desktop.dev.href);
    console.log('PASS independently authenticated mobile Chromium context on the same HTTPS DEV URL');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1});
