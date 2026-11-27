// Capture the deployed sidebar using tasks seeded in the acceptance fixture.
const {acceptancePortalContext}=require('./acceptance_browser.cjs');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require(process.env.SMARTDEV_PLAYWRIGHT || 'playwright');

(async()=>{
  const {SMARTDEV_PORTAL_URL:url,SMARTDEV_TEST_PASSWORD_FILE:passwordFile,
    SMARTDEV_SIDEBAR_SCREENSHOTS:directory, SMARTDEV_TEST_PROJECT_ID:projectId}=process.env;
  const project='Acceptance '+projectId;
  assert(url && passwordFile && directory,'Set test portal, password file and screenshot directory.');
  fs.mkdirSync(directory,{recursive:true,mode:0o700});
  const args=process.env.SMARTDEV_TEST_RESOLVER?['--host-resolver-rules='+process.env.SMARTDEV_TEST_RESOLVER]:[];
  const browser=await chromium.launch({args});
  try {
    const context=await acceptancePortalContext(browser,{viewport:{width:1440,height:900}});
    const page=await context.newPage();
    await page.goto(url);
    await page.getByLabel('Password').fill(fs.readFileSync(passwordFile,'utf8'));
    await page.getByRole('button',{name:'Sign in',exact:true}).click();
    const tree=page.getByRole('navigation',{name:'Projects and tasks'});
    const sidebar=page.locator('#projects-panel');
    const section=name=>tree.locator('.wb-tree-project').filter({has:tree.getByRole('button',{name,exact:false}).and(page.locator('.wb-tree-heading[title="'+name+'"]'))});
    const expand=async name=>{
      const heading=section(name).locator('.wb-tree-heading');
      await heading.waitFor();
      if (await heading.getAttribute('aria-expanded')!=='true') await heading.click();
      await section(name).getByRole('button',{name:'Main',exact:true}).waitFor();
    };
    const geometry=async()=>{
      const html=await sidebar.evaluate(side=>{
        const tree=side.querySelector('.wb-project-tree');
        const active=Array.from(side.querySelectorAll('.wb-tree-select[aria-current=true]'));
        const out=Array.from(side.querySelectorAll('.wb-tree-select')).filter(el=>{
          const r=el.getBoundingClientRect(), edge=side.getBoundingClientRect();
          return r.left<edge.left-.5 || r.right>edge.right+.5;
        });
        return {overflows:tree.scrollWidth>tree.clientWidth, outside:out.length,
          selections:active.length, projectSelected:active.some(x=>x.classList.contains('wb-tree-heading'))};
      });
      assert.deepEqual(html,{overflows:false,outside:0,selections:1,projectSelected:false});
    };
    const capture=name=>sidebar.screenshot({path:path.join(directory,name+'.png')});
    await expand(project);
    await section(project).getByRole('button',{name:'Main',exact:true}).click();
    await section(project).locator('.wb-tree-select[aria-current=true]').getByText('Main',{exact:true}).waitFor();
    await geometry();
    await capture('main-selected');
    const task=section(project).locator('button.wb-tree-select:has(.wb-navigation-dot)').first();
    await task.click();
    await section(project).locator('.wb-tree-select[aria-current=true]:has(.wb-navigation-dot)').waitFor();
    await geometry();
    await capture('task-selected');
    const left=page.locator('.wb-divider').first();
    await left.focus();
    const initialWidth=(await sidebar.boundingBox()).width;
    for (let count=0;count<5;count++) await left.press('ArrowLeft');
    assert((await sidebar.boundingBox()).width<initialWidth,'Sidebar did not resize');
    await geometry();
    await capture('narrow');
    const hasTruncated=await tree.locator('.wb-tree-title').evaluateAll(labels=>labels.some(label=>
      label.scrollWidth>label.clientWidth && getComputedStyle(label).textOverflow==='ellipsis'));
    assert(hasTruncated,'Seed a long task title in the DEV project to verify truncation');
    await capture('long-title');
    assert(await tree.evaluate(el=>el.scrollHeight>el.clientHeight),'Seed more tasks to verify vertical scrolling');
    await tree.evaluate(el=>{el.scrollTop=el.scrollHeight;});
    await geometry();
    await capture('scrolled');
    await page.reload();
    await section(project).locator('.wb-tree-select[aria-current=true]:has(.wb-navigation-dot)').waitFor();
    await geometry();
  } finally { await browser.close(); }
})().catch(error=>{console.error(error.message);process.exitCode=1;});
