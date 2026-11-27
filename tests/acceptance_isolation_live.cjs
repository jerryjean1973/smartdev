const assert = require('node:assert/strict');
const {randomUUID} = require('node:crypto');
const {chromium} = require(process.env.SMARTDEV_PLAYWRIGHT || 'playwright');
const {acceptanceContext} = require('./acceptance_browser.cjs');

async function main() {
  const browser = await chromium.launch();
  try {
    const context = await acceptanceContext(browser);
    const page = await context.newPage();
    const conversationId = process.env.SMARTDEV_TEST_CONVERSATION_ID;
    await page.goto(process.env.SMARTDEV_TEST_CHAT_URL);

    const api = (route, data) => page.evaluate(async ({route, data}) => {
      const response = await fetch(route, data === undefined ? {} : {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)
      });
      return {status: response.status, body: await response.json()};
    }, {route, data});
    const selection = async () => (await api('conversations')).body;
    const history = async () => (await api('task?project=1&tree=repository')).body.output
      .split('\n').flatMap(line => {
        try { return [JSON.parse(line)]; } catch { return []; }
      });
    const waitFor = async (read, predicate) => {
      const deadline = Date.now() + 120_000;
      while (Date.now() < deadline) {
        const value = await read();
        if (predicate(value)) return value;
        await page.waitForTimeout(500);
      }
      throw Error('Timed out waiting for fixture execution');
    };
    const send = async (prompt, extra = {}) => {
      const result = await api('run', {conversationId, requestId: randomUUID(),
        tree: 'repository', prompt, files: [], ...extra});
      assert.equal(result.status, 202, JSON.stringify(result.body));
      console.log(JSON.stringify({kind: 'receipt', ...result.body}));
      return result.body;
    };
    const sendMain = async prompt => {
      await page.locator('#tree option[value="repository"]').waitFor({state: 'attached'});
      const receipt = page.waitForResponse(response => response.url().endsWith('/run') &&
        response.request().method() === 'POST');
      await page.locator('#prompt').fill(prompt);
      await page.locator('#prompt').press('Enter');
      const response = await receipt;
      assert.equal(response.status(), 202, await response.text());
      const run = await response.json();
      assert.equal(run.conversationId, conversationId);
      assert.equal(run.objective, undefined);
      console.log(JSON.stringify({kind: 'receipt', ...run}));
      return run;
    };
    const finish = (executionId, prompt) => waitFor(history, events => {
      const message = events.findLastIndex(event => event.type === 'smartdev.user' &&
        event.execution === executionId && event.text === prompt);
      return message >= 0 && events.slice(message).some(event =>
        event.execution === executionId && event.type === 'smartdev.result');
    });

    assert.equal((await selection()).active.id, conversationId);
    for (const bad of [undefined, randomUUID()]) {
      const rejected = await api('run', {conversationId: bad, requestId: randomUUID(),
        tree: 'repository', prompt: 'Acceptance rejection probe', files: []});
      assert(rejected.status >= 400, JSON.stringify(rejected));
    }
    const attachment = await api('uploads', {name: 'acceptance.txt',
      type: 'text/plain', data: Buffer.from(randomUUID()).toString('base64')});
    assert.equal(attachment.status, 201, JSON.stringify(attachment.body));
    assert((await api('uploads')).body.some(file => file.id === attachment.body.id));
    console.log('PASS fixture upload', attachment.body.id);

    const ask = 'Use the shell to run git rev-parse --show-toplevel. Report the result and ask exactly this question on the last line: What is the acceptance reply marker? Do not edit files.';
    const first = await sendMain(ask);
    await page.reload();
    assert.equal((await selection()).active.id, conversationId);
    console.log('PASS refresh retained fixture Main during execution');

    const initialEvents = (await finish(first.executionId, ask)).filter(e => e.execution === first.executionId);
    const initialResult = initialEvents.findLast(e => e.type === 'smartdev.result');
    const thread = initialEvents.find(e => e.type === 'thread.started')?.thread_id;
    if (initialResult.code !== 0) {
      console.log(JSON.stringify({kind: 'initial-failure', result: initialResult,
        errors: initialEvents.filter(e => e.type === 'error' || e.type === 'smartdev.stderr').map(e => e.message || e.text)}));
    }

    const task = await send('Report the current branch in this background task. Do not edit files.', {newTask: true});
    assert(task.objective && task.conversationId !== conversationId);
    assert((await selection()).conversations.some(row => row.id === task.conversationId));
    console.log('PASS dedicated background task', task.objective, task.conversationId);

    assert.equal(initialResult.code, 0, 'Fixture Main could not start Codex');
    assert(initialResult.awaitingInput && thread);
    const reply = 'The acceptance reply marker is ' + randomUUID() + '. Report it exactly.';
    const resumed = await sendMain(reply);
    assert.equal(resumed.executionId, first.executionId);
    const replyEvents = await finish(first.executionId, reply);
    const replyIndex = replyEvents.findIndex(e => e.type === 'smartdev.user' && e.text === reply);
    assert.equal(replyEvents.filter(e => e.type === 'smartdev.user' && e.text === reply).length, 1);
    const replyTurn = replyEvents.slice(replyIndex).filter(e => e.execution === first.executionId);
    assert.equal(replyTurn.find(e => e.type === 'thread.started')?.thread_id, thread);
    assert.equal(replyTurn.findLast(e => e.type === 'smartdev.result').code, 0);
    assert.deepEqual((await selection()).conversations.filter(c => c.active_task_id).map(c => c.id), [task.conversationId]);
    console.log('PASS same-execution, same-thread Reply', first.executionId, thread);

    const marker = randomUUID();
    const command = "printf '%s\\n' 'START-" + marker + "'; sleep 6; printf '%s\\n' 'DONE-" + marker + "'";
    const instruction = 'Execute this exact shell command once and wait for it to finish. Do not edit files.\n' + command;
    await page.evaluate(() => {
      window.acceptanceStream = [];
      const stream = new EventSource('events');
      stream.onmessage = event => window.acceptanceStream.push(JSON.parse(event.data));
    });
    await waitFor(() => page.evaluate(() => window.acceptanceStream), events => events.length > 0);
    const streaming = await sendMain(instruction);
    await waitFor(() => page.evaluate(() => window.acceptanceStream), events =>
      events.some(event => event.runs.some(run => run.execution === streaming.executionId)));
    await waitFor(history, events => events.some(event => event.execution === streaming.executionId &&
      event.type === 'item.started' && event.item?.type === 'command_execution' && event.item.command.includes(marker)) &&
      !events.some(event => event.execution === streaming.executionId && event.type === 'smartdev.result'));
    const events = (await finish(streaming.executionId, instruction)).filter(e => e.execution === streaming.executionId);
    assert.equal(events.findLast(e => e.type === 'smartdev.result').code, 0);
    assert(events.some(e => e.item?.type === 'command_execution' &&
      e.item.aggregated_output?.includes('START-' + marker + '\nDONE-' + marker)));
    console.log('PASS command start streamed before completion', streaming.executionId);
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
