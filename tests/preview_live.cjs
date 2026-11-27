const {acceptanceContext,fixtureRepository}=require('./acceptance_browser.cjs');
const assert = require('node:assert/strict');
const {chromium} = require(process.env.SMARTDEV_PLAYWRIGHT || 'playwright');
const {execFileSync} = require('node:child_process');

(async()=>{
  const project=process.env.SMARTDEV_PREVIEW_FIXTURE;
  const base=process.env.SMARTDEV_PREVIEW_URL;
  if (!project || !base) throw Error('Set the disposable fixture container and preview URL.');
  const browser=await chromium.launch({args:['--no-sandbox']});
  try {
    await acceptanceContext(browser);
    await fixtureRepository(project);
    const workspace=new URL(process.env.SMARTDEV_TEST_CHAT_URL).href.replace(/codex-chat\/$/,'dev-preview/');
    assert(new URL(base).href.startsWith(workspace),'Preview must belong to the verified fixture workspace.');
    const page=await browser.newPage();
    await page.goto(base);
    await page.getByRole('heading',{name:'Live fixture v1'}).waitFor({timeout:40000});
    await page.getByText('API and WebSocket connected').waitFor();
    const status=async()=>await (await page.request.get(new URL('.__smartdev/status',base).href)).json();
    const first=await status();
    assert.equal(first.status,'ready');
    assert.equal(first.port,43127);
    assert.match(first.logs,/Fixture listening on 43127/);
    console.log('PASS automatic startup, discovered port 43127, rendered UI, API, WebSocket and startup logs');
    execFileSync('docker',['exec',project,'sh','-c', 'printf "%s\\n" "Live fixture v2" > "/projects/$PROJECT_NAME/repository/title.txt"']);
    await page.getByRole('heading',{name:'Live fixture v2'}).waitFor({timeout:20000});
    console.log('PASS browser refresh after source edit');
    execFileSync('docker',['exec',project,'sh','-c','kill "$(cat /projects/$PROJECT_NAME/repository/server.pid)"']);
    await page.goto(base);
    await page.getByRole('heading',{name:'Live fixture v2'}).waitFor({timeout:30000});
    assert((await status()).generation>first.generation);
    console.log('PASS application process recovery after stop');
    const foreign=new URL(base);
    foreign.pathname=foreign.pathname.replace(new URL(workspace).pathname.split('/').at(-4),'p-other-project');
    assert.equal((await page.request.get(foreign.href)).status(),404);
    await page.close();
    execFileSync('docker',['exec',project,'sh','-c',String.raw`for process in /proc/[0-9]*; do
      if [ "$(tr '\000' ' ' < "$process/cmdline" 2>/dev/null)" = "node /validation-preview/server.cjs " ]; then
        kill "${'${process##*/}'}"
      fi
    done`]);
    await new Promise(resolve=>setTimeout(resolve,1200));
    execFileSync('docker',['exec','-d',project,'node','/validation-preview/server.cjs']);
    const restarted=await browser.newPage();
    await restarted.goto(base);
    await restarted.getByRole('heading',{name:'Live fixture v2'}).waitFor({timeout:30000});
    console.log('PASS fixture route isolation and preview manager restart');

  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
