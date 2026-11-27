const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const os=require('node:os');
const path=require('node:path');
const {test}=require('node:test');
const {infer,inside}=require('../workspace/preview/discovery.cjs');

async function repository(t,files) {
  const root=await fs.mkdtemp(path.join(os.tmpdir(),'smartdev-preview-test-'));
  t.after(()=>fs.rm(root,{recursive:true,force:true}));
  for(const [relative,contents] of Object.entries(files)) {
    const file=path.join(root,relative);
    await fs.mkdir(path.dirname(file),{recursive:true});
    await fs.writeFile(file,typeof contents==='string'?contents:JSON.stringify(contents));
  }
  return root;
}
const web={scripts:{dev:'vite'},devDependencies:{vite:'5.4.0'}};
test('root Node application uses the locked manager and scoped Vite base',async t=>{
  const root=await repository(t,{'package.json':web,'package-lock.json':{}});
  const found=await infer(root,'/dev');
  assert.equal(found.cwd,root);
  assert.deepEqual(found.command,['npm','run','dev','--','--base','/dev/']);
  assert.deepEqual(found.setup,['npm','ci','--ignore-scripts','--no-audit','--no-fund']);
});
test('monorepo discovers frontend and preserves root lockfile',async t=>{
  const root=await repository(t,{'package.json':{workspaces:['apps/*']},'package-lock.json':{},
    'apps/api/package.json':{scripts:{start:'node server.js'},dependencies:{express:'4.0.0'}},
    'apps/web/package.json':web});
  const found=await infer(root,'/preview');
  assert.equal(found.cwd,path.join(root,'apps/web'));
  assert.equal(found.setupDirectory,root);
});
test('discovers a web entry inside an initialized submodule',async t=>{
  const root=await repository(t,{'.gitmodules':'[submodule "site"]\n path = sources/site\n url = https://github.com/example/site.git\n',
    'sources/site/.git':'gitdir: ../../.git/modules/site\n','sources/site/Procfile':'web: python3 project.py\n'});
  assert.deepEqual((await infer(root,'')).command,['sh','-c','python3 project.py']);
});
test('discovers a Python API and detects its docs and dependency setup',async t=>{
  const root=await repository(t,{'services/api/main.py':'from fastapi import FastAPI\napi = FastAPI()\n',
    'services/api/requirements.txt':'fastapi==0.115.0\nuvicorn==0.34.0\n'});
  const found=await infer(root,'/preview');
  assert.equal(found.cwd,path.join(root,'services/api'));
  assert.equal(found.apiDocs,'/docs');
  assert(found.command.includes('main:api'));
  assert(found.setup.includes('requirements.txt'));
  assert(!found.command[0].startsWith(root+path.sep));
});
test('reports Compose when a workspace container runtime is unavailable',async t=>{
  const root=await repository(t,{'compose.yml':'services:\n  web:\n    build: .\n'});
  await assert.rejects(infer(root,''),{phase:'configuration_required',message:/no authorized container runtime/});
});
test('serves static entry points without following symlinks during discovery',async t=>{
  const root=await repository(t,{'sites/home/index.html':'<h1>Site</h1>'});
  await fs.symlink('/tmp',path.join(root,'outside'),'dir');
  const found=await infer(root,'');
  assert.match(found.command[1],/static\.cjs$/);
  assert.equal(found.cwd,path.join(root,'sites/home'));
  await assert.rejects(inside(root,path.join(root,'outside')),/inside the selected checkout/);
});
test('does not start test scripts or library compile jobs',async t=>{
  const root=await repository(t,{'package.json':{scripts:{dev:'tsc --watch'}},'examples/site/index.html':'example'});
  await assert.rejects(infer(root,''),{phase:'not_applicable'});
});
test('requires a lockfile when inferred Node dependencies are missing',async t=>{
  const root=await repository(t,{'frontend/package.json':web});
  await assert.rejects(infer(root,''),{phase:'configuration_required',message:/lockfile/});
});
test('reports ambiguous equal candidates without choosing alphabetically',async t=>{
  const root=await repository(t,{'apps/one/index.html':'one','apps/two/index.html':'two'});
  await assert.rejects(infer(root,''),{phase:'configuration_required',message:/apps\/one, apps\/two/});
});
test('skips uninitialized submodules while discovering an independent application',async t=>{
  const root=await repository(t,{
    'private/site/Procfile':'web: never-use-this-uninitialized-source',
    'web/index.html':'<h1>Independent website</h1>',
  });
  const found=await infer(root,'',new Set([path.join(root,'private/site')]));
  assert.equal(found.cwd,path.join(root,'web'));
});
test('does not reuse a partial npm dependency install',async t=>{
  const root=await repository(t,{'package.json':web,'package-lock.json':{},'node_modules/partial/index.js':'partial'});
  assert.deepEqual((await infer(root,'')).setup,['npm','ci','--ignore-scripts','--no-audit','--no-fund']);
  await fs.writeFile(path.join(root,'node_modules/.package-lock.json'),'{}');
  assert.equal((await infer(root,'')).setup,undefined);
});

test('scopes a production Vite preload base without rewriting unrelated scripts',async ()=>{
  const vm=require('node:vm');
  const server=await fs.readFile(path.join(__dirname,'../workspace/preview/server.cjs'),'utf8');
  const helper=server.slice(server.indexOf('function scopeVitePreloads('),server.indexOf('function scopedModules('));
  const scoped=vm.runInNewContext(helper+'\nscopeVitePreloads',{basePath:'/workspace/dev'});
  const vite='const tag="modulepreload",base=function(x){return"/"+x}; console.log("Unable to preload CSS","vite:preloadError");';
  assert.match(scoped(vite), /return"\/workspace\/dev\/"\+x/);
  assert.equal(scoped(scoped(vite)),scoped(vite));
  assert.equal(scoped('function asset(x){return"/"+x}'),'function asset(x){return"/"+x}');
});
