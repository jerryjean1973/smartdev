const http = require('node:http');
const net = require('node:net');
const dns = require('node:dns/promises');
const {StringDecoder}=require('node:string_decoder');
const fs = require('node:fs/promises');
const path = require('node:path');
const browserScript = require('node:fs').readFileSync(path.join(__dirname,'browser.js'));
const {spawn,execFile} = require('node:child_process');
const git = require('node:util').promisify(execFile);

const {DiscoveryError,infer,inside,optional}=require('./discovery.cjs');

const project = path.join('/projects', process.env.PROJECT_NAME);
const repository = path.join(project, 'repository');
const control = '/.__smartdev';
const proxyPort = 13339;
let app;
let generation = 0;
let revision = 0;
let basePath = process.env.PROJECT_PREVIEW_PATH || '';
let source = 'repository';
async function runtime(payload) {
  const access=JSON.parse(await fs.readFile('/run/command-memory/memory.json','utf8'));
  const response=await fetch(access.url+'/preview-runtime',{method:'POST',redirect:'error',
    signal:AbortSignal.timeout(7000),headers:{Authorization:'Bearer '+access.token,'Content-Type':'application/json'},
    body:JSON.stringify(payload)});
  if (!response.ok) throw Error('Project runtime configuration is unavailable (HTTP '+response.status+').');
  return response.json();
}
const privateCookie = /^(?:coder_|__Host-coder|__Secure-coder)/i;
const sensitive = /-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)|\b(?:gh[pousr]_|github_pat_|sk-(?:proj-|svcacct-)?|hvs\.)[A-Za-z0-9_-]{12,}|(https?:\/\/)[^\s/@]+:[^\s/@]+@/gi;
const redact = value => String(value).replace(sensitive,'[redacted]');
const escape = value => String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function discover(prefix,tree) {
  const checkout=path.join(project,tree);
  if (tree!=='repository') {
    const {stdout}=await git('git',['worktree','list','--porcelain','-z'],{cwd:repository,timeout:5000});
    if (!stdout.split('\0').includes('worktree '+checkout) || await fs.realpath(checkout)!==checkout)
      throw new DiscoveryError('Select a registered worktree in this project.');
  }
  const root=await inside(project,checkout);
  const ownerConfig=await optional(path.join(project,'config','preview.json'));
  const config=JSON.parse(ownerConfig ?? await optional(path.join(root,'.smartdev-preview.json')) ?? '{}');
  if (config.port !== undefined && (!Number.isInteger(config.port) || config.port<1024 || config.port>65535))
    throw new DiscoveryError('Preview configuration contains an invalid port.');
  const health=config.healthPath || '/';
  if (typeof health!=='string' || !health.startsWith('/') || health.startsWith('//'))
    throw new DiscoveryError('Preview healthPath must be a local application path.');
  const cwd=await inside(root,path.resolve(root,config.directory || '.'));
  let app;
  if (config.command!==undefined) app={command:config.command,cwd};
  else {
    const reportFile=tree==='repository'?'git-sync.json':path.basename(tree)+'.git-sync.json';
    const diagnostics=JSON.parse(await optional(path.join(project,'config',reportFile)) || '{}');
    const submodules=await git('git',['submodule','status','--recursive'],{cwd:root,timeout:5000}).catch(()=>({stdout:''}));
    const missing=submodules.stdout.split('\n').flatMap(line=>{
      const entry=line.match(/^[-+U][0-9a-f]+ (.+?)(?: \([^)]*\))?$/);
      return entry ? [path.resolve(root,entry[1])] : [];
    }).filter(directory=>directory.startsWith(root+path.sep));
    const errors=(diagnostics.issues||[]).filter(issue=>issue.path && typeof issue.message==='string');
    const incomplete=new Set([...missing,...errors.map(issue=>path.resolve(root,issue.path))
      .filter(directory=>directory.startsWith(root+path.sep))]);
    try { app=await infer(cwd,prefix,incomplete); }
    catch(error) {
      if (error.phase==='not_applicable' && incomplete.size) {
        const available=errors.slice(0,3).map(issue=>issue.message);
        throw new DiscoveryError(available.join(' ') || 'No application was found outside the incomplete Git submodules. Check Git access and the recorded pins.');
      }
      throw error;
    }
  }
  const setup=config.setup ?? app.setup;
  for (const command of [app.command,...(setup===undefined?[]:[setup])])
    if (!Array.isArray(command) || !command.length || command.some(item=>typeof item!=='string' || !item.trim()))
      throw new DiscoveryError('The preview command and setup must be executable argument arrays.');
  return {...app,setup,explicitSetup:config.setup!==undefined,setupDirectory:config.setup ? app.cwd : app.setupDirectory,required:config.requiredEnvironment || [],
    port:config.port,health,apiDocs:config.healthPath ? undefined : app.apiDocs,keepPrefix:app.keepPrefix || config.preserveBasePath===true,
    browserReload:config.browserReload ?? app.browserReload ?? true};
}

function allocate() {
  return new Promise((resolve,reject)=>{
    const socket=net.createServer().once('error',reject);
    socket.listen(0,'127.0.0.1',()=>{const {port}=socket.address(); socket.close(()=>resolve(port));});
  });
}

// Inspect only sockets belonging to the application process group.
async function listeningPorts(group) {
  const pids=(await fs.readdir('/proc')).filter(name=>/^\d+$/.test(name));
  const groups=await Promise.all(pids.map(async pid=>{
    try {
      const stat=await fs.readFile('/proc/'+pid+'/stat','utf8');
      if (Number(stat.slice(stat.lastIndexOf(')')+2).split(' ')[2])!==group) return [];
      const directory='/proc/'+pid+'/fd/';
      return (await Promise.all((await fs.readdir(directory)).map(fd=>fs.readlink(directory+fd).catch(()=>''))))
        .map(link=>/^socket:\[(\d+)\]$/.exec(link)?.[1]).filter(Boolean);
    } catch { return []; }
  }));
  const sockets=new Set(groups.flat());
  const tables=await Promise.all(['/proc/net/tcp','/proc/net/tcp6'].map(file=>optional(file)));
  return [...new Set(tables.flatMap(table=>(table || '').trim().split('\n').slice(1).flatMap(line=>{
    const fields=line.trim().split(/\s+/);
    return fields[3]==='0A' && sockets.has(fields[9]) ? [parseInt(fields[1].split(':').at(-1),16)] : [];
  })))];
}
const delay = ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function probe(port, pathname) {
  for (const host of ['127.0.0.1','[::1]']) {
    try {
      const response=await fetch('http://'+host+':'+port+pathname,{redirect:'manual',signal:AbortSignal.timeout(1800)});
      await response.body?.cancel();
      if (response.status>=200 && response.status<400) return host;
    } catch {}
  }
}

function append(run,chunk) {
  let output=String(chunk);
  for (const value of run.sensitive || []) if (value.length) output=output.split(value).join('[redacted]');
  run.log=(run.log+redact(output)).slice(-24000);
}
function logStream(run,stream) {
  const decoder=new StringDecoder('utf8');
  let pending='', oversized=false;
  function consume(text) {
    const lines=text.split('\n');
    for (const line of lines.slice(0,-1)) {
      if (!oversized && pending.length+line.length<=65536) append(run,pending+line+'\n');
      else append(run,'[Oversized log line omitted]\n');
      pending='';
      oversized=false;
    }
    const tail=lines.at(-1);
    if (!oversized) {
      pending+=tail;
      if (pending.length>65536) {pending='';oversized=true;}
    }
  }
  stream.on('data',chunk=>consume(decoder.write(chunk)));
  stream.on('end',()=>{consume(decoder.end());if (!oversized && pending) append(run,pending);});
}
function terminate(run) {
  for (const watcher of run.watchers || []) watcher.close();
  if (!run.child?.pid || run.terminated) return;
  run.terminated=true;
  try { process.kill(-run.child.pid,'SIGTERM'); } catch {}
  const pid=run.child.pid;
  setTimeout(()=>{try { process.kill(-pid,'SIGKILL'); } catch {}},4000).unref();
}
function watchSource(run,cwd) {
  const watch=require('node:fs').watch;
  run.watchers=[];
  let settle;
  const changes=(event,name)=>{
    if (!name || /(^|[\\/])(?:\.|node_modules|dist|build|__pycache__|generated-artifacts)/.test(name)) return;
    clearTimeout(settle);
    settle=setTimeout(()=>{if (app===run) revision++;},400);
  };
  for (const directory of ['.','app','src','client','pages','public','static','templates']) {
    try {
      const watcher=watch(path.join(cwd,directory),{recursive:directory!=='.'},changes);
      watcher.on('error',()=>watcher.close());
      run.watchers.push(watcher);
    } catch {}
  }
}
async function npmSetupReason(cwd) {
  try {
    const lock=JSON.parse(await optional(path.join(cwd,'package-lock.json')));
    const hosts=new Set();
    for (const entry of Object.values(lock.packages || {})) {
      if (!/^https?:\/\//i.test(entry.resolved || '')) continue;
      try { hosts.add(new URL(entry.resolved).hostname); } catch {}
    }
    const missing=await Promise.all([...hosts].slice(0,16).map(host=>Promise.race([
      dns.lookup(host).then(()=>null,error=>['ENOTFOUND','EAI_AGAIN'].includes(error.code)?host:null),
      delay(1500).then(()=>null)
    ])));
    const names=missing.filter(Boolean);
    return names.length ? ' Package registry DNS unavailable: '+names.join(', ')+'.' : '';
  } catch { return ''; }
}
let reports=Promise.resolve();
function report(run,phase,extra={}) {
  run.phase=phase;
  if (app!==run) return;
  const status=run.status;
  reports=reports.then(()=>app===run && runtime({action:'report',status,phase,...extra})).catch(()=>{});
}
function fail(run,error,phase='failed') {
  if (app!==run || run.status==='failed') return;
  run.status='failed';
  run.error=redact(error);
  report(run,phase);
  terminate(run);
  if (phase!=='failed' || run.failures>=2) return;
  const wait=5000 * 2 ** run.failures;
  run.retryAt=Date.now()+wait;
  run.retry=setTimeout(()=>{
    run.retry=undefined;
    run.retryAt=undefined;
    if (app===run) void launch(run.failures+1);
  },wait);
  run.retry.unref();
}
async function monitor(run,pathname) {
  let failures=0;
  while (app===run && run.status==='ready') {
    await delay(15000);
    if (app!==run || run.status!=='ready') return;
    const healthy=await probe(run.port,pathname);
    failures=healthy ? 0 : failures+1;
    const phase=healthy ? 'running' : 'degraded';
    if (run.phase!==phase) report(run,phase);
    if (failures>=3) fail(run,'Application readiness stopped responding.');
  }
}
async function start(run) {
  try {
    report(run,'discovering');
    if (process.env.PROJECT_REPOSITORY) {
      const sync=spawn('/usr/local/bin/git-sync',['--recent',...(run.source==='repository'?[]:['--preview-worktree',run.source])],{stdio:['ignore','pipe','pipe']});
      sync.stdout.on('data',chunk=>append(run,chunk));
      sync.stderr.on('data',chunk=>append(run,chunk));
      const result=await new Promise((resolve,reject)=>{sync.once('error',reject);sync.once('exit',resolve);});
      if (result!==0 && !(await fs.stat(path.join(project,run.source)).catch(()=>null))?.isDirectory())
        throw new DiscoveryError('Application source is unavailable. Verify Git repository access and retry.');
    }
    if (app!==run) return;
    const config=await discover(basePath,run.source);
    const desired=config.port || await allocate();
    if (app!==run) return;
    const env = Object.fromEntries(['PATH','HOME','USER','LANG','LC_ALL','TZ','TMPDIR','TERM','NODE_EXTRA_CA_CERTS']
      .filter(key=>process.env[key]!==undefined).map(key=>[key,process.env[key]]));
    Object.assign(env,{PORT:String(desired),HOST:'127.0.0.1',HOSTNAME:'127.0.0.1',
      SMARTDEV_PREVIEW_BASE_PATH:basePath,NODE_ENV:'development',PYTHONUNBUFFERED:'1',BROWSER:'none'});
    const setupEnv={...env};
    const runtimeEnv=JSON.parse(await optional(path.join(project,'config','preview.env.json')) || '{}');
    const fromVault=await runtime({action:'resolve'});
    if (fromVault.missing.length) throw new DiscoveryError('Missing project secret: '+fromVault.missing.join(', '));
    Object.assign(runtimeEnv,fromVault.values);
    run.sensitive=Object.values(runtimeEnv).filter(value=>typeof value==='string')
      .flatMap(value=>value.split(/\r?\n/)).filter(Boolean);
    if (!Array.isArray(config.required) || config.required.some(key=>typeof key!=='string' || !/^[A-Z_][A-Z0-9_]*$/.test(key)))
      throw new DiscoveryError('Invalid requiredEnvironment configuration.');
    const missing=config.required.filter(key=>!runtimeEnv[key] && !env[key]);
    if (missing.length) throw new DiscoveryError('Missing project secret: '+missing.join(', '));
    const observation={executable:path.basename(config.command[0]),directory:path.relative(project,config.cwd),healthPath:config.health};
    report(run,'preparing',observation);
    for (const [key,value] of Object.entries(runtimeEnv)) {
      if (!/^[A-Z][A-Z0-9_]*$/.test(key) || /^(?:CODER_|COMMAND_MEMORY_|GIT_|NODE_OPTIONS$|PATH$|HOME$|LD_)/.test(key)
          || typeof value!=='string') throw new DiscoveryError('Invalid environment entry in the project DEV configuration.');
      env[key]=value;
    }
    append(run,'Starting '+path.basename(config.command[0])+' in '+path.relative(project,config.cwd)+'\n');
    const flag=config.portFlag || (config.command[2]==='uvicorn'?'--port':null);
    if (flag==='django') config.command.push('127.0.0.1:'+desired);
    else if (flag && !config.command.some(argument=>argument===flag || argument.startsWith(flag+'=')))
      config.command.push(flag,String(desired));
    function spawnProcess(command, cwd=config.cwd, variables=env) {
      const child=spawn(command[0],command.slice(1),{cwd,env:variables,detached:true,stdio:['ignore','pipe','pipe']});
      run.child=child;
      logStream(run,child.stdout);
      logStream(run,child.stderr);
      return child;
    }
    for (const command of [config.bootstrap,config.setup].filter(Boolean)) {
      if (app!==run) return;
      append(run,'Preparing project dependencies.\n');
      try { await new Promise((resolve,reject)=>{
        const setup=spawnProcess(command,config.setupDirectory || config.cwd,config.explicitSetup ? env : setupEnv);
        const timeout=setTimeout(()=>{terminate(run);reject(Error('Project setup timed out.'));},300000);
        setup.once('error',error=>{clearTimeout(timeout);reject(error);});
        setup.once('exit',code=>{clearTimeout(timeout);if(code===0) resolve(); else reject(Error('Project setup exited with status '+code+'.'));});
      }); } catch(error) {
        if (config.automaticNpmSetup) error.message+=await npmSetupReason(config.setupDirectory || config.cwd);
        throw new DiscoveryError(error.message+' Review the dependency installation log.');
      }
    }
    if (app!==run) return;
    report(run,'starting',observation);
    const child=spawnProcess(config.command);
    child.once('error',error=>fail(run,error.message));
    child.once('close',(code,signal)=>{
      const missing=run.log.match(/(?:missing (?:required )?(?:environment variable|secret)|required environment variable)\s*:?\s*([A-Z][A-Z0-9_]{2,})/);
      if (missing) fail(run,'Missing project secret: '+missing[1],'configuration_required');
      else fail(run,'Application stopped ('+(signal || 'exit '+code)+').');
    });
    const deadline=Date.now()+180000;
    while (app===run && run.status==='starting' && Date.now()<deadline) {
      const ports=await listeningPorts(child.pid);
      ports.sort((a,b)=>Number(b===desired)-Number(a===desired));
      for (const port of config.port ? ports.filter(port=>port===desired) : ports) {
        const originalHealth=config.keepPrefix ? basePath+config.health : config.health;
        const rootHost=await probe(port,originalHealth);
        const prefixedHost=!rootHost && !config.keepPrefix && basePath && await probe(port,basePath+config.health);
        const apiHost=!rootHost && !prefixedHost && config.apiDocs && await probe(port,config.apiDocs);
        const host=rootHost || prefixedHost || apiHost;
        const health=apiHost ? config.apiDocs : prefixedHost ? basePath+config.health : originalHealth;
        if (host && app===run && run.status==='starting') {
          Object.assign(run,{port,host,keepPrefix:config.keepPrefix,status:'ready',landing:apiHost ? config.apiDocs : undefined});
          if (config.browserReload) watchSource(run,config.cwd);
          void monitor(run,health);
          const head=await git('git',['rev-parse','HEAD'],{cwd:config.cwd,timeout:5000}).catch(()=>({stdout:''}));
          const verified={source:run.source,commit:head.stdout.trim() || null,directory:path.relative(project,config.cwd),
            executable:path.basename(config.command[0]),environmentKeys:Object.keys(runtimeEnv),port,
            healthPath:health,verifiedAt:new Date().toISOString()};
          report(run,'running',{...observation,port,...(verified.commit?{commit:verified.commit}:{})});
          await fs.writeFile(path.join(project,'config','preview-runtime.json'),JSON.stringify(verified),{mode:0o600})
            .catch(error=>append(run,'Could not persist verified runtime metadata: '+error.code+'\n'));
          return;
        }
      }
      await delay(700);
    }
    if (run.status==='starting') throw Error('Application did not respond successfully on its HTTP port within three minutes.');
  } catch(error) {
    fail(run,error.message,error.phase || (error instanceof SyntaxError?'configuration_required':'failed'));
  }
}
let admission;
let held=false;
let appliedRevision;
function launch(failures=0, force=false) {
  if (admission) return admission;
  if (app?.status==='starting' && !force) return Promise.resolve();
  admission=(async()=>{
    let allowed=true;
    if (basePath) {
      try {
        const policy=await runtime({action:'preview_policy'});
        held=policy.enabled===false;
        allowed=policy.automatic===true && !held;
        if (allowed) appliedRevision=policy.revision ?? 0;
      } catch { allowed=false; }
    }
    if (!allowed) return;
    const prior=app;
    app={status:'starting',phase:'discovering',log:'',generation:++generation,source,failures};
    if (prior) {
      clearTimeout(prior.retry);
      terminate(prior);
    }
    void start(app);
  })().finally(()=>{admission=undefined;});
  return admission;
}
function status() {
  return {status:held ? 'stopped' : app?.status || 'idle',phase:held?'stopped':app?.phase || 'discovering',source:app?.source || source,port:app?.port,generation,error:app?.error && redact(app.error),logs:redact(app?.log || '')};
}
function internal(response,code,body,type) {
  response.writeHead(code,{'Content-Type':type,'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',
    'Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'; script-src 'self'; form-action 'self'; frame-ancestors 'self' https://smartdev.youkandoit.com"});
  response.end(body);
}
function startupPage(response,logs=false) {
  const detail=status();
  const pending=detail.status==='starting' || Boolean(admission);
  const root=basePath+'/';
  const content='<!doctype html><meta charset="utf-8">'+(pending?'<meta http-equiv="refresh" content="2">':'')+
    '<style>:root{color-scheme:light dark;font:15px system-ui}body{padding:1.5rem}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>'+
    '<h2>'+escape(logs?'DEV application logs':held?'DEV application stopped':({
      discovering:'Discovering the DEV application',preparing:'Preparing project dependencies',starting:'Starting DEV application',
      running:'DEV application running',degraded:'DEV application health check delayed',
      configuration_required:'Application requires configuration',not_applicable:'No web application detected',
      failed:'Application failed to start'
    }[detail.phase] || 'Discovering the DEV application'))+'</h2>'+
    (detail.error?'<p>'+escape(detail.error)+'</p>':'')+
    (!logs && app?.retryAt ? '<p>SmartDev will retry automatically.</p>' : '')+
    '<p><a href="'+escape(root)+'">Open application</a></p>'+
    (!pending?'<form method="post" action="'+escape(root+'.__smartdev/retry')+'"><button>Retry application</button></form>':'')+
    (detail.phase==='configuration_required' && /secret/i.test(detail.error || '') && !logs ? '<button id="configure-runtime">Configure project secrets</button>' : '')+
    (logs?'<pre>'+escape(detail.logs)+'</pre>':'')+
    '<script src='+JSON.stringify(root+'.__smartdev/browser.js')+'></script>';
  internal(response,200,content,'text/html; charset=utf-8');
}
function scopeUrl(value) {
  return basePath && value.startsWith('/') && !value.startsWith('//') &&
    value!==basePath && !value.startsWith(basePath+'/') ? basePath+value : value;
}
function upstreamOptions(request, url) {
  const headers={...request.headers};
  for (const key of Object.keys(headers)) {
    if (/^(?:coder-|x-coder-)/i.test(key)) delete headers[key];
  }
  if (headers.cookie) headers.cookie=headers.cookie.split(';').filter(piece=>!privateCookie.test(piece.trim())).join(';');
  if (headers.origin) {
    try {
      if (new URL(headers.origin).host===(headers['x-forwarded-host'] || request.headers.host))
        headers.origin='http://'+app.host+':'+app.port;
    } catch {}
  }
  headers.host=app.host+':'+app.port;
  headers['x-forwarded-prefix']=basePath;
  if (basePath) headers['accept-encoding']='identity';
  return {hostname:app.host.replace(/^\[|\]$/g,''),port:app.port,method:request.method,path:(app.keepPrefix ? scopeUrl(url.pathname) : url.pathname)+url.search,headers};
}
function scopeVitePreloads(body) {
  if (!body.includes('Unable to preload CSS') || !body.includes('vite:preloadError')) return body;
  return body.replace(/(["']modulepreload["'],[$\w]+=function\(([$\w]+)\)\{return)(["'])\/\3\+\2\}/,
    (_,prefix,argument)=>prefix+JSON.stringify(basePath+'/')+'+'+argument+'}');
}
function scopedModules(body) {
  return body
    .replace(/(\b(?:from\s+|import\s*\(?\s*))(["'])(\/(?!\/)[^"'\r\n]+)\2/g,
      (_,verb,quote,url)=>verb+quote+scopeUrl(url)+quote)
    .replace(/(\bexport\s+default\s+)(["'])(\/(?!\/)[^"'\r\n]+)\2/g,
      (_,verb,quote,url)=>verb+quote+scopeUrl(url)+quote);
}
function proxy(request,response,url) {
  const run=app;
  const upstream=http.request(upstreamOptions(request,url),incoming=>{
    const headers={...incoming.headers};
    if (headers.location) headers.location=scopeUrl(headers.location);
    if (headers['set-cookie']) headers['set-cookie']=headers['set-cookie']
      .filter(cookie=>!privateCookie.test(cookie))
      .map(cookie=>basePath ? cookie.replace(/;\s*(?:Domain|Path)=[^;]*/gi,'')+'; Path='+basePath+'/' : cookie);
    const html=basePath && incoming.statusCode>=200 && incoming.statusCode<300 && /^text\/html\b/i.test(headers['content-type'] || '') && !headers['content-encoding'];
    const module=basePath && !run.keepPrefix && !headers['content-encoding'] &&
      /^(?:text|application)\/(?:javascript|ecmascript)\b/i.test(headers['content-type'] || '') &&
      (/^\/(?:src\/|@vite\/|@react-refresh(?:$|\/)|@id\/|@fs\/|node_modules\/)/.test(url.pathname) ||
        /^\/assets\/[^/]+\.m?js$/.test(url.pathname));
    if (!html && !module) {response.writeHead(incoming.statusCode,headers);incoming.pipe(response);return;}
    delete headers['content-length'];
    delete headers.etag;
    response.writeHead(incoming.statusCode,headers);
    incoming.setEncoding('utf8');
    let body='';
    incoming.on('data',chunk=>{body+=chunk; if (body.length>16_000_000) incoming.destroy(Error('Preview response exceeds the proxy limit.'));});
    incoming.on('error',()=>response.destroy());
    incoming.on('end',()=>{
      if (module) return response.end(url.pathname.startsWith('/assets/') ? scopeVitePreloads(body) : scopedModules(body));
      if (!run.keepPrefix && body.includes('/@vite/client')) body=scopedModules(body);
      response.end(body
        .replace(/\b(src|href|action|poster)=(["'])(\/[^"'\s]*)\2/gi,(_,key,quote,value)=>key+'='+quote+scopeUrl(value)+quote)
        .replace(/<head\b[^>]*>/i,tag=>tag+'<base href="'+escape(basePath+'/')+'"><script src='+JSON.stringify(basePath+control+'/browser.js')+'></script>'));
    });
  });
  upstream.on('error',error=>{
    if (error.code==='ECONNREFUSED') fail(run,'Application connection failed: '+error.code);
    if (!response.headersSent) startupPage(response); else response.destroy();
  });
  request.pipe(upstream);
}
function normalize(request) {
  const url=new URL(request.url,'http://localhost');
  const prefix=url.searchParams.get('__smartdev_base');
  if (!basePath && prefix?.endsWith('/'+process.env.PROJECT_NAME+'/apps/dev-preview') &&
      /^\/@[\w.-]+\/[\w-]+\/apps\/dev-preview$/.test(prefix)) basePath=prefix;
  const tree=url.searchParams.get('__smartdev_tree');
  if (url.pathname==='/' && tree && (tree==='repository' || /^worktrees\/task-[a-f0-9-]{36}$/.test(tree)) && tree!==source) {
    source=tree;
    if (app) void launch(0,true);
  }
  url.searchParams.delete('__smartdev_tree');
  url.searchParams.delete('__smartdev_base');
  return url;
}

const server=http.createServer((request,response)=>{
  const url=normalize(request);
  if (url.pathname===control+'/browser.js') return internal(response,200,browserScript,'text/javascript; charset=utf-8');
  if (url.pathname===control+'/revision') return internal(response,200,JSON.stringify({generation,revision,state:app?.status}),'application/json');
  if (url.pathname===control+'/health') return internal(response,200,'ok','text/plain');
  if (url.pathname===control+'/status' && (request.headers['sec-fetch-site']==='cross-site'))
    return internal(response,403,'Restricted workspace route.','text/plain');
  if (url.pathname===control+'/status') return internal(response,200,JSON.stringify(status()),'application/json');
  if (url.pathname===control+'/logs') return startupPage(response,true);
  if (url.pathname===control+'/retry' && request.method==='POST') {
    if (['cross-site','same-site'].includes(request.headers['sec-fetch-site']))
      return internal(response,403,'Use the project preview controls.','text/plain');
    void launch(0,true);
    response.writeHead(303,{Location:basePath+'/'}).end();
    return;
  }
  if (!app && !held) void launch();
  if (app?.status==='ready') {
    if (app.landing && request.method==='GET' && url.pathname==='/')
      return response.writeHead(302,{Location:basePath+app.landing}).end();
    return proxy(request,response,url);
  }
  return startupPage(response);
});
server.on('upgrade',(request,socket,head)=>{
  if (app?.status!=='ready') return socket.destroy();
  const upstream=http.request(upstreamOptions(request,normalize(request)));
  upstream.on('upgrade',(response,connection,buffer)=>{
    socket.write('HTTP/1.1 101 Switching Protocols\r\n'+
      response.rawHeaders.reduce((text,value,index)=>text+value+(index%2?'\r\n':': '),'')+'\r\n');
    if (buffer.length) socket.write(buffer);
    if (head.length) connection.write(head);
    socket.pipe(connection).pipe(socket);
    socket.on('error',()=>connection.destroy());
    connection.on('error',()=>socket.destroy());
  });
  upstream.on('response',response=>{response.resume();socket.destroy();});
  upstream.on('error',()=>socket.destroy());
  socket.on('error',()=>upstream.destroy());
  upstream.end();
});
process.on('SIGTERM',()=>{if(app) terminate(app); server.close(()=>process.exit());});
server.listen(proxyPort,'127.0.0.1',()=>{
  if (!basePath) return;
  const keepAvailable=async()=>{
    try {
      const policy=await runtime({action:'preview_policy'});
      held=policy.enabled===false;
      if (held && app) {
        const previous=app;
        app=undefined;
        revision++;
        clearTimeout(previous.retry);
        terminate(previous);
        void runtime({action:'report',status:'stopped'}).catch(()=>{});
      }
      if (!policy.automatic || held) return;
      if (appliedRevision!==undefined && policy.revision!==appliedRevision) {
        void launch(0,true);
      } else if (!app) {
        void launch();
      }
    } catch {}
  };
  void keepAvailable();
  setInterval(keepAvailable,10000).unref();
});
