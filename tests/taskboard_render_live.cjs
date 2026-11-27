const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');

const origin = process.env.SMARTDEV_TEST_ORIGIN || 'https://smartdev.youkandoit.com';
const output = process.env.SMARTDEV_SCREENSHOT_DIR;
const passwordFile = process.env.SMARTDEV_TEST_PASSWORD_FILE;

async function run() {
  assert.ok(output && passwordFile, 'Set SMARTDEV_SCREENSHOT_DIR and SMARTDEV_TEST_PASSWORD_FILE.');
  await fs.mkdir(output, {recursive: true});
  const browser = await chromium.launch({args: ['--no-sandbox']});
  const errors = [];
  try {
    const page = await browser.newPage({
      viewport: {width: 1440, height: 1000}, timezoneId: 'America/New_York',
    });
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(new URL('/?view=taskboard', origin).href);
    await page.getByLabel('Password').fill((await fs.readFile(passwordFile, 'utf8')).trim());
    await page.getByRole('button', {name: 'Sign in', exact: true}).click();
    const board = page.getByRole('region', {name: 'Taskboard'});
    const bo1 = board.getByRole('button', {name: 'P0 · BO-01 — Multi-environment deployment', exact: true});
    await bo1.waitFor();
    const projects = page.getByRole('navigation', {name: 'Projects and tasks'});
    await projects.waitFor();
    const sidebar = await projects.boundingBox();
    const taskboard = await board.boundingBox();
    assert.ok(sidebar && taskboard && sidebar.x + sidebar.width <= taskboard.x,
      'Projects sidebar should be beside Taskboard on desktop.');
    assert.equal(await board.locator('.wb-business-objectives button').count(), 9);
    await page.screenshot({path: path.join(output, 'taskboard-overview.png')});

    await bo1.click();
    assert.equal(await bo1.getAttribute('aria-pressed'), 'true');
    const group = board.locator('.wb-taskboard-group').filter({
      has: page.getByRole('heading', {name: 'BO-01 — Multi-environment deployment', exact: true}),
    });
    const p0 = group.getByRole('heading', {name: /Make Provision SMD Host operational/});
    await p0.waitFor();
    const row = board.locator('.wb-taskboard-row').filter({has: page.getByRole('heading', {name: /Make Provision SMD Host operational/})});
    const awaiting = board.locator('.wb-taskboard-row').filter({
      has: page.getByText(/· Complete · awaiting approval$/),
    });
    for (const task of await awaiting.all()) {
      assert.equal(await task.getByRole('progressbar').getAttribute('value'), '100');
      assert.equal(await task.getByText('Awaiting owner approval', {exact: true}).count(), 1);
    }
    const evidenceNeeded = board.locator('.wb-taskboard-row').filter({
      has: page.getByText(/· Evidence needed$/),
    });
    for (const task of await evidenceNeeded.all()) {
      assert.equal(await task.getByRole('button', {name: /^Approve /}).isEnabled(), false);
    }
    const created = row.locator('time');
    assert.ok(Date.parse(await created.getAttribute('datetime')));
    assert.match(await created.textContent(), /\d{1,2}:\d{2}:\d{2}.*E[DS]T/);

    const order = board.locator('.wb-taskboard-options select').nth(2);
    await order.selectOption('oldest');
    for (const section of await board.locator('.wb-taskboard-section').all()) {
      const times = await section.locator('time').evaluateAll(nodes => nodes.map(node => Date.parse(node.dateTime)));
      assert.ok(times.every((value, index) => index === 0 || value >= times[index - 1]), 'Oldest-first sort failed.');
    }
    await page.screenshot({path: path.join(output, 'taskboard-bo01.png'), fullPage: true});
    const title = row.getByRole('button', {name: (await p0.textContent()).trim(), exact: true});
    await title.click();
    assert.equal(await title.getAttribute('aria-expanded'), 'true');
    const details = row.getByRole('region', {name: 'Task details'});
    await details.getByRole('heading', {name: 'Expected outcome', exact: true}).waitFor();
    await details.getByRole('heading', {name: 'Acceptance criteria', exact: true}).waitFor();
    await details.getByRole('heading', {name: 'Latest run', exact: true}).waitFor();
    await details.getByRole('heading', {name: 'Verified evidence', exact: true}).waitFor();
    await details.getByRole('heading', {name: 'Screenshot proof', exact: true}).waitFor();
    assert.equal(await details.getByText('10b332ba-ec84-43b8-8323-02f61b0602e1', {exact: true}).count(), 1);
    await details.screenshot({path: path.join(output, 'taskboard-task-details.png')});
    await title.click();
    await details.waitFor({state: 'hidden'});

    await page.setViewportSize({width: 390, height: 844});
    const showProjects = page.getByRole('button', {name: 'Show Projects', exact: true});
    await showProjects.click();
    await projects.waitFor();
    await page.getByRole('button', {name: 'Hide Projects', exact: true}).click();
    await projects.waitFor({state: 'hidden'});
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'Mobile layout overflows.');
    await page.screenshot({path: path.join(output, 'taskboard-mobile.png'), fullPage: true});
    assert.deepEqual(errors, [], 'Browser emitted an uncaught error.');
    console.log('PASS: rendered objectives, BO-01 filter, creation timestamps, approval and evidence-needed states, oldest-first sorting, desktop sidebar, task details, mobile toggle and width and screenshots.');
  } finally {
    await browser.close();
  }
}

run().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
