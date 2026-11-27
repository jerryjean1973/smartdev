const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const os=require('node:os');
const {test}=require('node:test');
const {assetStore}=require('../workspace/chat/project-assets.cjs');

test('durable project bytes, five-way deduplication, versions and scoped deletion',async()=>{
  const tmp=await fs.mkdtemp(path.join(os.tmpdir(),'smartdev-assets-'));
  try {
    const root=path.join(tmp,'uploads'), index=path.join(tmp,'index'), other=path.join(tmp,'other');
    await Promise.all([root,index,other].map(dir=>fs.mkdir(dir)));
    const store=assetStore(root,index);
    const info={originalName:'full-leads.json',image:'',mimeType:'application/json'};
    const bytes=Buffer.from('[{"lead":1}]');
    const copies=await Promise.all(Array.from({length:5},()=>store.store(bytes,info)));
    assert.equal(new Set(copies.map(record=>record.id)).size,1);
    assert.equal((await fs.readdir(root)).length,1);
    const original=copies[0];
    assert.match(original.sha256,/^[a-f0-9]{64}$/);
    const alias=await store.store(bytes,{...info,originalName:'crm-export.json'});
    assert.notEqual(alias.id,original.id);
    assert.equal(alias.filename,original.filename);
    assert.equal((await fs.readdir(root)).length,1);
    const version=await store.store(Buffer.from('[{"lead":2}]'),info);
    assert.equal(version.version,2);
    assert.equal(version.previousId,original.id);
    const restarted=assetStore(root,index);
    for (let turn=0;turn<10;turn++)
      assert.deepEqual(await fs.readFile((await restarted.get(original.id)).location),bytes);
    await assert.rejects(assetStore(root,other).get(original.id),/does not belong/);
    await restarted.change(original.id,'delete');
    await assert.rejects(restarted.get(original.id),/unavailable/);
    assert.equal((await fs.readdir(root)).length,2);
    assert.equal(await fs.readFile((await restarted.get(alias.id)).location,'utf8'),'[{"lead":1}]');
    await restarted.change(alias.id,'delete');
    assert.equal((await restarted.list()).length,1);
    assert.equal((await fs.readdir(root)).length,1);
    assert.equal(await fs.readFile((await restarted.get(version.id)).location,'utf8'),'[{"lead":2}]');
  } finally {await fs.rm(tmp,{recursive:true,force:true});}
});

test('legacy record remains readable and a repeat upload retains its existing ID',async()=>{
  const temporary=await fs.mkdtemp(path.join(os.tmpdir(),'smartdev-legacy-assets-'));
  try {
    const root=path.join(temporary,'uploads'), index=path.join(temporary,'index');
    await Promise.all([root,index].map(dir=>fs.mkdir(dir)));
    const id='11111111-1111-4111-8111-111111111111';
    const filename=id+'-full-leads.json';
    await fs.writeFile(path.join(root,filename),'[]');
    await fs.writeFile(path.join(index,id+'.json'),JSON.stringify({id,filename,originalName:'full-leads.json',size:2,image:''}));
    const store=assetStore(root,index);
    const repeated=await store.store(Buffer.from('[]'),{originalName:'full-leads.json',image:'',mimeType:'application/json'});
    assert.equal(repeated.id,id);
    assert.equal((await store.get(id)).filename,filename);
    assert.equal(repeated.provenance,'legacy project upload');
    await fs.writeFile(path.join(root,filename),'{}');
    await assert.rejects(store.get(id),/checksum mismatch/);
  } finally {await fs.rm(temporary,{recursive:true,force:true});}
});
