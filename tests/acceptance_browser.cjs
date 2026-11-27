const assert=require('node:assert/strict');
const {readFile,realpath}=require('node:fs/promises');
const {execFileSync}=require('node:child_process');
const path=require('node:path');

async function acceptanceContext(browser, options={}) {
  const {SMARTDEV_TEST_CHAT_URL:target, SMARTDEV_TEST_PROJECT_ID:projectId,
    SMARTDEV_TEST_CONVERSATION_ID:conversationId, SMARTDEV_TEST_ACCEPTANCE_TOKEN_FILE:tokenFile, SMARTDEV_TEST_BROWSER_STATE:storageState}=process.env;
  assert(target && projectId && conversationId && tokenFile && storageState,
    'Set the isolated acceptance chat URL, project ID, acceptance credential file and dedicated browser state.');
  const base=new URL(target);
  assert(base.protocol==='https:' && base.pathname.endsWith('/apps/codex-chat/') &&
    !base.username && !base.password && !base.search && !base.hash,'Use an exact HTTPS acceptance chat URL.');
  const token=(await readFile(tokenFile,'utf8')).trim();
  assert(/^[!-~]{32,256}$/.test(token),'Invalid acceptance credential file.');
  const uuid=/^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$/;
  assert(uuid.test(projectId) && uuid.test(conversationId),'Invalid fixture IDs.');
  const workspace='p-'+Buffer.from(projectId.replaceAll('-',''),'hex').reduce(
    (bits,byte)=>bits+byte.toString(2).padStart(8,'0'),'').padEnd(130,'0').match(/.{5}/g)
    .map(bits=>'abcdefghijklmnopqrstuvwxyz234567'[parseInt(bits,2)]).join('');
  assert(base.pathname.split('/').at(-4)===workspace,'Chat URL must identify the fixture workspace.');
  const context=await browser.newContext({...options,storageState,serviceWorkers:'block'});
  await context.route('**/*',async route=>{
    const request=route.request();
    const url=new URL(request.url());
    const chat=url.origin===base.origin && url.pathname.startsWith(base.pathname);
    const mutates=!['GET','HEAD','OPTIONS'].includes(request.method());
    if (!chat) return route.abort('blockedbyclient');
    if ((!mutates && url.pathname!==base.pathname+'acceptance')) return route.continue();
    if (mutates) {
      const source=new URL(request.frame().url());
      if (source.origin!==base.origin || !source.pathname.startsWith(base.pathname))
        return route.abort('blockedbyclient');
    }
    const response=await route.fetch({maxRedirects:0,headers:{...await request.allHeaders(),...(mutates ? {'sec-fetch-site':'same-origin'} : {}),'x-smartdev-acceptance':token,'x-smartdev-fixture-conversation':conversationId}});
    if (response.status()>=300 && response.status()<400) return route.abort('blockedbyclient');
    return route.fulfill({response});
  });
  const probe=await context.newPage();
  try {
    await probe.goto(base.href);
    assert.equal(new URL(probe.url()).pathname,base.pathname,'Dedicated Coder browser state did not authenticate.');
    const identity=await probe.evaluate(async()=>{
      const response=await fetch('acceptance',{redirect:'error'});
      if (!response.ok) throw Error('Server rejected the acceptance identity');
      return response.json();
    });
    assert.equal(identity.environment,'acceptance');
    assert.equal(identity.conversation_id,conversationId);
    assert.equal(identity.workspace_name,workspace);
    assert.equal(identity.project_id,projectId,'Acceptance workspace project identity mismatch');
  } catch(error) {
    await context.close();
    throw error;
  } finally {
    if (!probe.isClosed()) await probe.close();
  }
  return context;
}

async function fixtureRepository(container) {
  assert(container,'Set the disposable fixture container ID.');
  const base=new URL(process.env.SMARTDEV_TEST_CHAT_URL);
  const workspace=base.pathname.split('/').at(-4);
  const project=execFileSync('docker',['exec',container,'printenv','PROJECT_NAME'],{encoding:'utf8'}).trim();
  assert.equal(project,workspace,'Docker container does not belong to the fixture workspace.');
  const mounts=JSON.parse(execFileSync('docker',['inspect','--format','{{json .Mounts}}',container],{encoding:'utf8'}));
  const mount=mounts.find(item=>item.Type==='volume' && item.Destination==='/projects');
  assert(mount,'The fixture lacks a dedicated project volume.');
  const expected=path.join(await realpath(mount.Source),workspace,'repository');
  assert.equal(await realpath(expected),expected,'The fixture checkout must not redirect to another path.');
  return expected;
}

async function acceptancePortalContext(browser, options={}, externalOrigins=[]) {
  const context=await acceptanceContext(browser,options);
  const portal=new URL(process.env.SMARTDEV_PORTAL_URL);
  const chat=new URL(process.env.SMARTDEV_TEST_CHAT_URL);
  const workspaceUrl=chat.href.slice(0,-'/apps/codex-chat/'.length);
  const projectId=process.env.SMARTDEV_TEST_PROJECT_ID;
  const fixture={id:projectId,name:'Acceptance '+projectId,repository:'',runtime:'other',
    status:'created',workspace_url:workspaceUrl,error:null,archived:false};
  const token=(await readFile(process.env.SMARTDEV_TEST_ACCEPTANCE_TOKEN_FILE,'utf8')).trim();
  await context.route('**/*',async route=>{
    const request=route.request();
    const url=new URL(request.url());
    const read=['GET','HEAD'].includes(request.method());
    if (url.origin!==portal.origin) {
      const allowedPreview=url.href.startsWith(workspaceUrl+'/apps/dev-preview/');
      if (read && (allowedPreview || externalOrigins.includes(url.origin))) return route.continue();
      return route.fallback();
    }
    if (!url.pathname.startsWith('/api/')) return read ? route.continue() : route.abort();
    if (url.pathname==='/api/login' && request.method()==='POST') return route.continue();
    if (url.pathname==='/api/projects' && read) {
      const response=await route.fetch({maxRedirects:0});
      if (!response.ok()) return route.fulfill({response});
      return route.fulfill({json:[fixture]});
    }
    if (url.pathname==='/api/account/project-selection' && read) return route.fulfill({json:{id:projectId}});
    if (url.pathname==='/api/account/project-selection/'+projectId && request.method()==='PUT')
      return route.fulfill({status:200,json:{ok:true}});
    if (url.pathname==='/api/task-navigation' && read) {
      const response=await route.fetch({maxRedirects:0});
      if (!response.ok()) return route.fulfill({response});
      const rows=await response.json();
      return route.fulfill({json:rows.filter(row=>row.project_id===projectId)});
    }
    if (url.pathname.startsWith('/api/projects/'+projectId+'/')) {
      const operation=url.pathname.slice(('/api/projects/'+projectId+'/').length);
      if (/^(?:objectives|development|dev-publishing|preview)(?:\/[^/]+){0,2}$/.test(operation)) {
        const response=await route.fetch({maxRedirects:0,headers:{...await request.allHeaders(),
          'x-smartdev-acceptance':token,'x-smartdev-fixture-conversation':process.env.SMARTDEV_TEST_CONVERSATION_ID,
          'x-smartdev-fixture-workspace':chat.pathname.split('/').at(-4)}});
        return response.status()>=300 && response.status()<400 ? route.abort() : route.fulfill({response});
      }
    }
    return route.abort('blockedbyclient');
  });
  return context;
}

module.exports={acceptanceContext,acceptancePortalContext,fixtureRepository};
