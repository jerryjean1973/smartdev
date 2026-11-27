'use strict';
const fs=require('node:fs/promises');
const path=require('node:path');
const {createHash,randomUUID}=require('node:crypto');

const assetId=/^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$/i;
const digest=contents=>createHash('sha256').update(contents).digest('hex');

function assetStore(root,index) {
  async function metadata(id) {
    if (typeof id!=='string' || !assetId.test(id)) throw Error('Invalid attachment ID.');
    try {
      const record=JSON.parse(await fs.readFile(path.join(index,id+'.json'),'utf8'));
      if (record.deletedAt || record.id!==id) throw Error('Attachment is unavailable.');
      return record;
    } catch (error) {
      if (error.code!=='ENOENT') throw error;
      throw Object.assign(Error('Attachment does not belong to this project.'),{code:'UPLOAD_MISSING'});
    }
  }

  async function list() {
    const names=await fs.readdir(index);
    const records=await Promise.all(names.filter(name=>name.endsWith('.json')).map(async name=>{
      try { return await metadata(name.slice(0,-5)); } catch { return null; }
    }));
    return records.filter(Boolean).sort((a,b)=>String(b.lastUploadedAt || b.at).localeCompare(String(a.lastUploadedAt || a.at)));
  }

  async function get(id) {
    const record=await metadata(id);
    if (typeof record.filename!=='string' || path.basename(record.filename)!==record.filename ||
        await fs.realpath(root)!==root) throw Error('Invalid project attachment path.');
    const location=path.join(root,record.filename);
    const info=await fs.lstat(location);
    if (await fs.realpath(location)!==location || !info.isFile() || info.size!==record.size)
      throw Error('Invalid project attachment file.');
    await fs.access(location);
    if (record.sha256 && digest(await fs.readFile(location))!==record.sha256)
      throw Error('Attachment checksum mismatch.');
    return {...record,location};
  }

  async function write(record) {
    const temporary=path.join(index,'.'+randomUUID()+'.tmp');
    try {
      await fs.writeFile(temporary,JSON.stringify(record),{mode:0o600,flag:'wx'});
      await fs.rename(temporary,path.join(index,record.id+'.json'));
    } finally { await fs.rm(temporary,{force:true}); }
  }

  let changes=Promise.resolve();
  function serialize(work) {
    const result=changes.then(work);
    changes=result.catch(()=>{});
    return result;
  }

  async function store(contents, info) {
    return serialize(async()=>{
      const sha256=digest(contents);
      if (await fs.realpath(root)!==root) throw Error('Invalid project upload directory.');
      const existing=await list();
      let shared;
      for (const record of [...existing].reverse()) {
        if (record.sha256!==sha256 && (record.sha256 || record.size!==contents.length))
          continue;
        const found=await get(record.id).catch(()=>null);
        if (!found || (!record.sha256 && digest(await fs.readFile(found.location))!==sha256)) continue;
        shared ||= found;
        if (record.originalName!==info.originalName) continue;
        const updated={...record,sha256,mimeType:record.mimeType || info.mimeType,
          provenance:record.provenance || 'legacy project upload',lastUploadedAt:new Date().toISOString()};
        if (JSON.stringify(record)!==JSON.stringify(updated)) await write(updated);
        return updated;
      }
      const filename=shared?.filename || 'asset-'+sha256+info.image;
      const location=path.join(root,filename);
      if (!shared) {
        const temporary=path.join(root,'.upload-'+randomUUID());
        try {
          await fs.writeFile(temporary,contents,{mode:0o600,flag:'wx'});
          try { await fs.link(temporary,location); }
          catch(error) {
            if (error.code!=='EEXIST') throw error;
            const stat=await fs.lstat(location);
            if (!stat.isFile() || await fs.realpath(location)!==location || digest(await fs.readFile(location))!==sha256)
              throw Error('Existing attachment does not match the expected checksum.');
          }
        } finally { await fs.rm(temporary,{force:true}); }
      }
      const related=existing.filter(record=>record.originalName===info.originalName)
        .sort((a,b)=>(b.version || 1)-(a.version || 1) || String(b.at).localeCompare(String(a.at)));
      const previous=related[0];
      const highestVersion=Math.max(0,...related.map(record=>record.version || 1));
      const record={...info,id:randomUUID(),filename,sha256,size:contents.length,at:new Date().toISOString(),
        version:highestVersion+1,previousId:previous?.id,provenance:'project upload'};
      await write(record);
      return record;
    });
  }

  function change(id,action,name) {
    return serialize(async()=>{
      const {location,...record}=await get(id);
      if (action==='rename') {
        await write({...record,displayName:name});
      } else if (action==='delete') {
        await write({...record,deletedAt:new Date().toISOString()});
        const references=await list();
        if (!references.some(item=>item.filename===record.filename)) await fs.unlink(location);
      } else throw Error('Invalid project attachment action.');
    });
  }
  return {list,get,store,change};
}

module.exports={assetStore};
