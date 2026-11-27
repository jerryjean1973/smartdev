const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');

const origin = process.env.SMARTDEV_TEST_ORIGIN || 'https://smartdev.youkandoit.com';
const output = process.env.SMARTDEV_SCREENSHOT_DIR;
const passwordFile = process.env.SMARTDEV_TEST_PASSWORD_FILE;

async function run() {
  assert.ok(output && passwordFile, 'Set SMARTDEV_SCREENSHOT_DIR and SMARTDEV_TEST_PASSWORD_FILE.');
  await fs.mkdir(output, {recursive: true});
  const browser = await chromium.launch({args:['--no-sandbox']});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
    const denied = await page.request.get(origin + '/api/cc-codex');
    assert.equal(denied.status(), 401);
    await page.goto(origin + '/?view=taskboard');
    await page.getByLabel('Password').fill((await fs.readFile(passwordFile, 'utf8')).trim());
    await page.getByRole('button', {name:'Sign in', exact:true}).click();

    const send = async (dialog, text, expected) => {
      await dialog.getByRole('textbox', {name:'Message Codex'}).fill(text);
      const accepted = page.waitForResponse(response => response.url().includes('/api/cc-codex') &&
        response.request().method() === 'POST' && response.status() === 202);
      await dialog.getByRole('button', {name:'Send', exact:true}).click();
      const {id} = await (await accepted).json();
      await fs.appendFile(path.join(output,'test-turns.txt'), id + '\n');
      await dialog.locator('[data-speaker="codex"]').getByText(expected, {exact:true}).waitFor({timeout:90000});
    };

    await page.getByRole('button', {name:'Ask CC', exact:true}).click();
    const main = page.getByRole('dialog', {name:'Ask CC · Main'});
    await send(main, 'Read-only browser test. Reply exactly CC_BROWSER_READY. Do not use tools or save memory.', 'CC_BROWSER_READY');
    await main.screenshot({path:path.join(output,'cc-main-conversation.png')});
    await main.getByRole('button', {name:'Close CC conversation'}).click();

    const board = page.getByRole('region', {name:'Taskboard', exact:true});
    const kanban = board.getByRole('region', {name:'Business objective Kanban'});
    const bo1 = board.getByRole('button', {name:/P0 · BO-01 — Multi-environment deployment/});
    const bo2 = board.getByRole('button', {name:/P0 · BO-02 — Tenant and deployment management/});
    await bo1.click();
    await board.getByRole('button', {name:'Kanban', exact:true}).click();
    assert.deepEqual(await kanban.locator('.wb-kanban-lane').evaluateAll(lanes => lanes.map(lane=>lane.ariaLabel)),
      ['Requested','Queued','Reviewed','Processed','Tested','Requeued','Completed']);
    const p0 = kanban.getByRole('button', {name:/^Ask CC about P0 TOP PRIORITY: Make Provision SMD Host/});
    await p0.waitFor();
    await bo2.click();
    await p0.waitFor({state:'hidden'});
    await kanban.getByRole('button', {name:/^Ask CC about Implement SMD deployment identity/}).waitFor();
    await bo1.click();
    await p0.waitFor();
    await page.screenshot({path:path.join(output,'bo01-kanban.png')});

    await p0.click();
    const task = page.getByRole('dialog', {name:'Ask CC · Task conversation'});
    assert.equal(await task.getByText('CC_BROWSER_READY',{exact:true}).count(),0);
    const marker = process.env.SMARTDEV_TEST_MARKER || 'browser-pilot-maple-68';
    await task.getByText(/^Memory bank/).click();
    await task.getByRole('textbox', {name:'Memory note'}).fill('Synthetic browser test marker: '+marker);
    await task.getByRole('button', {name:'Save memory', exact:true}).click();
    await task.getByText('Synthetic browser test marker: '+marker, {exact:false}).waitFor();
    await task.getByRole('button', {name:'Close CC conversation'}).click();
    await p0.click();
    await send(task, 'Read-only browser test. Reply with just the synthetic browser test marker from this task memory. Do not use tools or save memory.', marker);
    await task.getByText(/^Memory bank/).click();
    await task.getByText('Synthetic browser test marker: '+marker, {exact:false}).waitFor();
    await task.screenshot({path:path.join(output,'cc-task-memory.png')});
    console.log('PASS: authenticated host reply, seven Kanban lanes, objective switching, isolated task chat and persisted memory recall.');
  } finally {await browser.close();}
}
run().catch(error=>{console.error(error);process.exitCode=1;});
