const fs=require('node:fs/promises');
const path=require('node:path');
const os=require('node:os');
const {createHash}=require('node:crypto');

const excluded=new Set(['node_modules','vendor','dist','build','coverage','tests','test','fixtures','examples','storybook','docs','__pycache__']);
const composeFiles=['compose.yaml','compose.yml','docker-compose.yaml','docker-compose.yml'];
const scripts=['preview:dev','dev','start','serve','preview'];
const exists=async file=>fs.access(file).then(()=>true,()=>false);
const optional=async file=>{
  try { return await fs.readFile(file,'utf8'); }
  catch(error) { if (error.code!=='ENOENT') throw error; }
};
class DiscoveryError extends Error {
  constructor(message,phase='configuration_required') { super(message); this.phase=phase; }
}
async function inside(root,file) {
  const real=await fs.realpath(file);
  if (real!==root && !real.startsWith(root+path.sep)) throw new DiscoveryError('Preview directory must be inside the selected checkout.');
  return real;
}
async function scan(root,skip) {
  const queue=[{directory:root,depth:0}], results=[];
  let truncated=false;
  while(queue.length && results.length<350) {
    const {directory,depth}=queue.shift();
    const entries=(await fs.readdir(directory,{withFileTypes:true})).sort((a,b)=>a.name.localeCompare(b.name));
    const files=new Set(entries.filter(entry=>entry.isFile()).map(entry=>entry.name));
    results.push({directory,files,depth});
    for(const entry of entries) {
      if (!entry.isDirectory() || entry.name.startsWith('.') || excluded.has(entry.name)) continue;
      if (skip.has(path.join(directory,entry.name))) continue;
      if (depth<7 && queue.length<350) queue.push({directory:path.join(directory,entry.name),depth:depth+1});
      else truncated=true;
    }
  }
  return {results,truncated:truncated || !!queue.length};
}
async function nodeApp(checkout, directory, pkg, prefix) {
  const script=scripts.find(key=>typeof pkg.scripts?.[key]==='string' && pkg.scripts[key].trim());
  if (!script) return;
  const text=pkg.scripts[script];
  const deps={...pkg.dependencies,...pkg.devDependencies};
  const framework=Object.hasOwn(deps,'vite') && (await exists(path.join(directory,'vite.config.ts')) || await exists(path.join(directory,'vite.config.js')) || await exists(path.join(directory,'vite.config.mjs')));
  const browser=framework || /\b(vite|next|nuxt|astro|react-scripts|ng\s+serve|svelte-kit)\b/.test(text);
  const server=Object.keys(deps).some(key=>['express','fastify','koa','hono','@nestjs/core','next','vite','@sveltejs/kit'].includes(key));
  const orchestrator=/\b(turbo|concurrently|npm-run-all|nx)\b|(?:npm|pnpm|yarn) .*(?:--workspace|--filter)/.test(text);
  if (directory!==checkout && !browser && !server && !orchestrator && script!=='preview:dev' &&
      !/\b(?:node|tsx|ts-node|nodemon)\s+.*(?:server|app|index|main)\./.test(text)) return;
  if (!browser && !server && !orchestrator && /^(?:tsc|eslint|prettier|vitest|jest|rollup)(?:\s|$)/.test(text.trim())) return;
  let installDirectory=directory;
  const localLock=async dir=>{
    for(const [file,manager] of [['pnpm-lock.yaml','pnpm'],['yarn.lock','yarn'],['npm-shrinkwrap.json','npm'],['package-lock.json','npm']])
      if (await exists(path.join(dir,file))) return manager;
  };
  let locked=await localLock(directory);
  if (!locked && directory!==checkout) {
    const rootPkg=JSON.parse(await optional(path.join(checkout,'package.json')) || '{}');
    if (rootPkg.workspaces || await exists(path.join(checkout,'pnpm-workspace.yaml'))) {
      locked=await localLock(checkout);
      if (locked) installDirectory=checkout;
    }
  }
  const declared=typeof pkg.packageManager==='string' ? pkg.packageManager.split('@')[0] : undefined;
  const manager=['npm','pnpm','yarn'].includes(declared) ? declared : locked || 'npm';
  if (locked && declared && manager!==locked) throw new DiscoveryError('The package manager declaration conflicts with the project lockfile.');
  const command=manager==='npm' ? ['npm','run',script] : ['corepack',manager,'run',script];
  const vite=/^\s*vite(?:\s|$)/.test(text);
  if (vite && prefix) command.push(...(manager==='npm'?['--']:[]),'--base',prefix+'/');
  const installationMarkers={
    npm:['node_modules/.package-lock.json'],
    pnpm:['node_modules/.modules.yaml'],
    yarn:['.pnp.cjs','.yarn/install-state.gz','node_modules/.yarn-integrity'],
  };
  const installed=(await Promise.all(installationMarkers[manager].map(marker=>exists(path.join(installDirectory,marker))))).some(Boolean);
  let setup;
  if (!installed && (locked || Object.keys(deps).length)) {
    if (!locked) throw new DiscoveryError('Node dependencies are missing. Commit the package manager lockfile for a reproducible install.');
    setup={
      npm:['npm','ci','--ignore-scripts','--no-audit','--no-fund'],
      pnpm:['corepack','pnpm','install','--frozen-lockfile','--ignore-scripts'],
      yarn:['corepack','yarn','install',...(((typeof pkg.packageManager==='string' && pkg.packageManager.startsWith('yarn@1.')) || /^# yarn lockfile v1/m.test(await optional(path.join(installDirectory,'yarn.lock')) || '')) ? ['--frozen-lockfile','--ignore-scripts'] : ['--immutable','--mode=skip-build'])],
    }[manager];
  }
  return {command,setup,setupDirectory:installDirectory,automaticNpmSetup:setup && manager==='npm',keepPrefix:vite,
    browserReload:!vite && !/\bnext dev\b/.test(text), rank:orchestrator?95:browser?80:script==='preview:dev'?75:55};
}
async function pythonApp(directory, files, prefix) {
  const entries=[];
  for (const name of ['app.py','main.py','server.py','api.py','streamlit_app.py']) {
    if (!files.has(name)) continue;
    const text=await optional(path.join(directory,name));
    const asgi=text.match(/^([a-zA-Z_]\w*)\s*=\s*FastAPI\s*\(/m)?.[1];
    const flask=text.match(/^([a-zA-Z_]\w*)\s*=\s*Flask\s*\(/m)?.[1];
    const module=name.slice(0,-3);
    if (asgi && /(?:from fastapi import|import fastapi)/.test(text))
      entries.push({args:['-m','uvicorn',module+':'+asgi,'--host','127.0.0.1','--reload',...(prefix?['--root-path',prefix]:[])],portFlag:'--port',rank:65,apiDocs:'/docs'});
    else if (flask && /(?:from flask import|import flask)/.test(text))
      entries.push({args:['-m','flask','--app',module+':'+flask,'run','--host','127.0.0.1'],portFlag:'--port',rank:65});
    else if (/^\s*(?:from streamlit import|import streamlit)/m.test(text))
      entries.push({args:['-m','streamlit','run',name,'--server.address','127.0.0.1',...(prefix?['--server.baseUrlPath',prefix]:[])],
        portFlag:'--server.port',keepPrefix:!!prefix,rank:80});
  }
  if (files.has('manage.py') && /django\.core\.management/.test(await optional(path.join(directory,'manage.py'))))
    entries.push({args:['manage.py','runserver'],portFlag:'django',rank:70});
  if (!entries.length) return;
  if (entries.length>1) throw new DiscoveryError('Multiple Python web entry points were detected. Configure the intended project application.');
  let interpreter=path.join(directory,'.venv','bin','python');
  let bootstrap,setup;
  if (!await exists(interpreter)) {
    if (files.has('requirements.txt')) {
      const id=createHash('sha256').update(directory).digest('hex').slice(0,24);
      interpreter=path.join(os.homedir(),'.cache','smartdev-preview',id,'bin','python');
      if (!await exists(interpreter)) bootstrap=['python3','-m','venv',path.dirname(path.dirname(interpreter))];
      setup=[interpreter,'-m','pip','install','--disable-pip-version-check','-r','requirements.txt'];
    } else interpreter='python3';
  }
  const {args,...entry}=entries[0];
  return {...entry,command:[interpreter,...args],bootstrap,setup};
}
async function infer(checkout,prefix,skip=new Set()) {
  const {results,truncated}=await scan(checkout,skip);
  const candidates=[], issues=[];
  let compose;
  for(const {directory,files,depth} of results) {
    if (!compose && composeFiles.some(name=>files.has(name))) compose=directory;
    try {
      const procfile=files.has('Procfile') && await optional(path.join(directory,'Procfile'));
      const web=procfile && procfile.match(/^web:\s*(.+)$/m)?.[1];
      const pkg=files.has('package.json') ? JSON.parse(await optional(path.join(directory,'package.json'))) : {};
      let app=web ? {command:['sh','-c',web],rank:100} : await nodeApp(checkout,directory,pkg,prefix);
      if (!app) app=await pythonApp(directory,files,prefix);
      if (!app && files.has('index.html') && !files.has('package.json')) {
        const page=await optional(path.join(directory,'index.html'));
        if (!/(?:src|href)=["'][^"']+\.(?:ts|tsx|jsx)["']|%PUBLIC_URL%/i.test(page))
          app={command:[process.execPath,path.join(__dirname,'static.cjs')],rank:60};
      }
      if (app) candidates.push({...app,cwd:directory,score:app.rank-depth});
    } catch(error) {
      if (!(error instanceof DiscoveryError) && !(error instanceof SyntaxError)) throw error;
      issues.push(error instanceof DiscoveryError ? error : new DiscoveryError('Invalid project manifest in '+(path.relative(checkout,directory)||'.')+'.'));
    }
  }
  candidates.sort((a,b)=>b.score-a.score);
  if (candidates[0] && candidates[1]?.score===candidates[0].score)
    throw new DiscoveryError('Multiple equally ranked web applications were found: '+candidates.filter(app=>app.score===candidates[0].score)
      .slice(0,5).map(app=>path.relative(checkout,app.cwd)||'.').join(', ')+'. Select the intended application directory in the project runtime configuration.');
  if (candidates.length) return candidates[0];
  if (issues.length) throw issues[0];
  if (compose) throw new DiscoveryError('Docker Compose was detected. This workspace has no authorized container runtime. Use the registered application URL when the services are running.');
  if (truncated) throw new DiscoveryError('The bounded scan could not inspect every directory. Select the application directory in project runtime configuration.');
  throw new DiscoveryError('No runnable web application was detected in the available checkout.','not_applicable');
}
module.exports={DiscoveryError,infer,inside,optional};
