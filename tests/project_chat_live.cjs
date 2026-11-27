const {acceptanceContext}=require('./acceptance_browser.cjs');
const assert=require('node:assert/strict');
const {randomUUID}=require('node:crypto');
const {chromium}=require(process.env.SMARTDEV_PLAYWRIGHT || 'playwright');

(async()=>{
  const base=process.env.SMARTDEV_TEST_CHAT_URL;
  if (!base) throw Error('Set SMARTDEV_TEST_CHAT_URL to a disposable, authenticated validation workspace.');
  const browser=await chromium.launch({args:['--no-sandbox']});
  try {
    const context=await acceptanceContext(browser,{permissions:['clipboard-read','clipboard-write']});
    const page=await context.newPage();
    await page.goto(base);
    await page.locator('#tree option').first().waitFor({state:'attached'});
    const prompt=page.locator('#prompt');
    const marker=randomUUID().slice(0,8);
    await page.evaluate(async ()=>{
      const canvas=document.createElement('canvas');
      canvas.width=12; canvas.height=12;
      const blob=await new Promise(resolve=>canvas.toBlob(resolve));
      await navigator.clipboard.write([new ClipboardItem({'image/png':blob})]);
    });
    await prompt.focus();
    const pasted=page.waitForResponse(response=>response.url().endsWith('/uploads') && response.status()===201);
    await prompt.press('Control+v');
    await pasted;
    await page.locator('#files li img').waitFor();
    await page.getByRole('button',{name:'Remove',exact:true}).click();
    console.log('PASS Chromium Ctrl+V from the browser image clipboard, preview and removal');

    const drop=await page.evaluateHandle(marker=>{
      const canvas=document.createElement('canvas');
      canvas.width=400;
      canvas.height=80;
      const pen=canvas.getContext('2d');
      pen.fillStyle='white'; pen.fillRect(0,0,400,80);
      pen.fillStyle='black'; pen.font='32px sans-serif'; pen.fillText('BROWSER ROCKET',10,50);
      const data=new DataTransfer();
      const pixels=Uint8Array.from(atob(canvas.toDataURL().split(',')[1]),char=>char.charCodeAt(0));
      data.items.add(new File([pixels],marker+'.png',{type:'image/png'}));
      data.items.add(new File(['%PDF-1.4\n%%EOF\n'],marker+'.pdf',{type:'application/pdf'}));
      data.items.add(new File(['export const marker = "'+marker+'";\n'],marker+'.js',{type:'text/javascript'}));
      return data;
    },marker);
    const files=page.waitForResponse(response=>response.url().endsWith('/uploads') && response.status()===201);
    await prompt.dispatchEvent('dragover',{dataTransfer:drop});
    assert(await page.locator('.composer.file-drag').count());
    await prompt.dispatchEvent('drop',{dataTransfer:drop});
    await files;
    await page.waitForFunction(async marker=>{
      const rows=await (await fetch('uploads')).json();
      return rows.filter(row=>row.originalName.startsWith(marker)).length===3;
    },marker);
    assert.equal(await page.locator('#files li').count(),3);
    await page.reload();
    await page.locator('#files li').nth(2).waitFor();
    assert.equal(await page.locator('#files li').count(),3);
    const explorer=await context.newPage();
    await explorer.goto(new URL('?uploads=1',base).href);
    await explorer.getByLabel('Search uploads').fill(marker);
    await explorer.locator('#project-uploads li').nth(2).waitFor();
    assert.equal(await explorer.locator('#project-uploads li').count(),3);
    console.log('PASS Chromium composer drag-and-drop of PNG, PDF and JS; persistent explorer lists all three');

    const request='For browser validation '+marker+', inspect the attached PNG and state the exact two words shown. Create a file named '+marker+'.js in the task worktree containing export const value = 1; and verify its contents. Do not commit.';
    await page.getByRole('button',{name:'New task',exact:true}).click();
    await prompt.fill(request);
    const admission=page.waitForResponse(response=>response.url().endsWith('/run') && response.status()===202);
    await prompt.press('Enter');
    const first=await (await admission).json();
    let outcome;
    for (let attempt=0; attempt<300 && !outcome; attempt++) {
      outcome=await page.evaluate(async first=>{
        const state=await (await fetch('task?'+new URLSearchParams(first))).json();
        if (!state.running) return state.output.split('\n').filter(line=>line.includes('"type":"smartdev.result"')).at(-1) || false;
      }, first);
      if (!outcome) await page.waitForTimeout(500);
    }
    assert(outcome,'The runner did not report an execution result.');
    const result=JSON.parse(outcome);
    const sent=page.locator('.chat-event.user').filter({hasText:request});
    await sent.waitFor();
    assert.equal(await sent.locator('a').count(),3);
    if (result.code===78 && process.env.SMARTDEV_ALLOW_AUTH_PREFLIGHT_ONLY==='1') {
      console.log('Codex authentication unavailable; multimodal and runner checkpoint checks skipped');
    } else {
      assert.equal(result.code,0);
      await page.locator('.chat-event.agent').filter({hasText:/BROWSER ROCKET/i}).last().waitFor();
      const rows=await (await page.request.get(new URL('history',base).href)).json();
      const versions=rows.filter(row=>row.objective===first.objective);
      assert(versions.some(row=>row.phase==='after-execution' && row.exitCode===0 && row.changedFiles.includes(marker+'.js')));
      assert(versions.some(row=>row.phase==='before-execution'));
      console.log('PASS real Codex multimodal execution and recoverable before/after source checkpoints');
    }
    await page.reload();
    await page.locator('.chat-event.user').filter({hasText:request}).waitFor();
    assert(await page.locator('#run').isEnabled());
    const next=page.waitForResponse(response=>response.url().endsWith('/run') && response.status()===202);
    await prompt.fill('Now investigate this different subject: report the Git branch name. Do not modify files. Reference '+marker+'.');
    await prompt.press('Enter');
    const second=await (await next).json();
    assert.notEqual(first.objective,second.objective);
    assert.notEqual(first.tree,second.tree);
    await page.waitForFunction(async second=>!(await (await fetch('task?'+new URLSearchParams(second))).json()).running,second,{timeout:150000});
    const resume=page.waitForResponse(response=>response.url().endsWith('/run') && response.status()===202);
    await prompt.fill('Continue '+first.objective+' and summarize the existing attachments.');
    await prompt.press('Enter');
    const resumed=await (await resume).json();
    assert.equal(resumed.objective,first.objective);
    assert.equal(resumed.tree,first.tree);
    await page.locator('#objective').evaluate((input,id)=>new Promise(resolve=>{
      const timer=setInterval(()=>{ if(input.value===id) {clearInterval(timer); resolve();} },20);
    }),first.objective);
    const subtask=page.waitForResponse(response=>response.url().endsWith('/run') && response.status()===202);
    await prompt.fill('Also check that the README exists as a separate subtask.');
    await prompt.press('Enter');
    const child=await (await subtask).json();
    assert.equal(child.parentTaskId,first.objective);
    assert.notEqual(child.tree,first.tree);
    console.log('PASS reload, new worktree, explicit continuation, and parent-linked subtask');

  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
