const {acceptancePortalContext}=require('./acceptance_browser.cjs');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {chromium}=require(process.env.SMARTDEV_PLAYWRIGHT || 'playwright');

(async()=>{
  const root=process.env.SMARTDEV_PORTAL_URL;
  const password=fs.readFileSync(process.env.SMARTDEV_TEST_PASSWORD_FILE,'utf8');
  const args=['--no-sandbox'];
  if (process.env.SMARTDEV_TEST_RESOLVER) args.push('--host-resolver-rules='+process.env.SMARTDEV_TEST_RESOLVER);
  const browser=await chromium.launch({args});
  const dev='https://www.openstreetmap.org/export/embed.html?bbox=-74.03%2C40.69%2C-73.94%2C40.78&layer=mapnik';
  const qa='https://www.openstreetmap.org/export/embed.html?bbox=-0.16%2C51.49%2C-0.07%2C51.54&layer=mapnik';
  try {
    const context=await acceptancePortalContext(browser,{},[new URL(dev).origin,'https://github.com']);
    const page=await context.newPage();
    await page.route('**/__smartdev/session',route=>route.abort());
    await page.goto(root);
    await page.getByLabel('Password').fill(password);
    await page.getByRole('button',{name:'Sign in',exact:true}).click();
    const select=page.getByLabel('Select project');
    await select.waitFor();
    const panel=page.locator('#preview-panel');
    await page.locator('.wb-menu > summary').click();
    await page.getByRole('button',{name:'Project settings',exact:true}).click();
    const id=process.env.SMARTDEV_TEST_PROJECT_ID;
    await select.selectOption(id);
    const base=root+'/api/projects/'+id;
    const state=async()=>await (await page.request.get(base+'/development')).json();
    assert.equal((await state()).status,'stopped');
    async function save(environment,url,mode) {
      const section=panel.locator('section').filter({has:page.getByRole('heading',{name:'Application Preview',exact:true})}).last();
      await section.getByLabel('Preview environment').selectOption(environment);
      await section.getByLabel('Preview URL').fill(url);
      await section.getByLabel('Preview mode').selectOption(mode);
      if(environment==='DEV') {
        await section.getByRole('button',{name:'Test connection'}).click();
        await section.getByRole('status').filter({hasText:/Browser connected|Application responded/}).waitFor({timeout:15000});
      }
      const response=page.waitForResponse(r=>r.url().endsWith('/preview/'+environment)&&r.request().method()==='PUT');
      await section.getByRole('button',{name:'Save preview settings'}).click();
      assert.equal((await response).status(),200);
    }
    await save('DEV',dev,'automatic');
    await save('QA',qa,'url');
    await panel.getByRole('button',{name:'Preview',exact:true}).click();
    const frame=page.frameLocator('iframe[title="Application Preview"]');
    const element=page.locator('iframe[title="Application Preview"]');
    await frame.getByText('OpenStreetMap contributors',{exact:false}).first().waitFor({timeout:30000});
    assert.equal(await element.getAttribute('src'),dev);
    await panel.getByLabel('More preview options').click();
    await panel.locator('.wb-preview-options select').last().selectOption({label:'Mobile'});
    assert.equal(await element.evaluate(node=>node.style.width),'390px');
    if (process.env.SMARTDEV_TEST_SCREENSHOT) await page.screenshot({path:process.env.SMARTDEV_TEST_SCREENSHOT});
    await panel.getByRole('button',{name:'Refresh preview',exact:true}).click();
    await frame.getByText('OpenStreetMap contributors',{exact:false}).first().waitFor({timeout:30000});
    await panel.getByRole('combobox',{name:'Preview environment'}).selectOption('QA');
    await frame.getByText('OpenStreetMap contributors',{exact:false}).first().waitFor({timeout:30000});
    assert.equal(await element.getAttribute('src'),qa);
    const popup=page.context().waitForEvent('page');
    await panel.getByRole('link',{name:'Open preview in new tab'}).click();
    const external=await popup;
    await external.waitForURL(qa);
    await external.close();
    await page.reload();
    await frame.getByText('OpenStreetMap contributors',{exact:false}).first().waitFor({timeout:30000});
    assert.equal(await element.getAttribute('src'),dev);
    assert.equal((await state()).status,'stopped');
    const stored=await (await page.request.get(base+'/preview')).json();
    assert.equal(stored.QA.url,qa);
    assert.equal(stored.DEV.url,dev);
    console.log('PASS real URL Preview with Coder disconnected and DEV stopped, DEV/QA switching, phone viewport, refresh, new tab, and persisted settings');
    const blocked='https://github.com/';
    const website=await page.request.get(blocked);
    assert.equal(website.headers()['x-frame-options']?.toLowerCase(),'deny');
    await page.locator('.wb-menu > summary').click();
    await page.getByRole('button',{name:'Project settings',exact:true}).click();
    await save('QA',blocked,'url');
    await panel.getByRole('button',{name:'Preview',exact:true}).click();
    await panel.getByRole('combobox',{name:'Preview environment'}).selectOption('QA');
    await page.waitForFunction(address=>document.querySelector('iframe[title="Application Preview"]')?.src===address,blocked);
    const fallback=page.context().waitForEvent('page');
    await panel.getByRole('link',{name:'Open preview in new tab'}).click();
    const tab=await fallback;
    await tab.waitForURL(blocked);
    await tab.close();
    console.log('PASS new-tab fallback for a real site with X-Frame-Options: deny');

  } finally {
    await browser.close();
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
