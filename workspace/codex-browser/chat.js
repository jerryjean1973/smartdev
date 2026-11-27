const vscode = acquireVsCodeApi();
const byId = id => document.getElementById(id);
const attachments = [];
const attachmentContexts = new Map();
let attachmentContext;
window.addEventListener('smartdev-attachment-context', ({detail}) => {
  if (detail === attachmentContext) return;
  if (attachmentContext) attachmentContexts.set(attachmentContext,[...attachments]);
  attachmentContext=detail;
  attachments.splice(0,attachments.length,...(attachmentContexts.get(detail) || []));
  renderFiles();
});
let preparing;
let uploading=0;
const encodeFile = file => new Promise((resolve,reject)=>{
  const reader=new FileReader();
  reader.onload=()=>resolve({name:file.name,type:file.type,data:reader.result.split(',')[1] || ''});
  reader.onerror=reject;
  reader.readAsDataURL(file);
});
function imagePreview(blob, name) {
  const dialog=document.createElement('dialog');
  const image=document.createElement('img');
  const close=document.createElement('button');
  const url=URL.createObjectURL(blob);
  image.src=url;
  image.alt=name;
  close.textContent='Close';
  close.onclick=()=>dialog.close();
  dialog.append(close,image);
  dialog.addEventListener('close',()=>{URL.revokeObjectURL(url);dialog.remove();},{once:true});
  document.body.append(dialog);
  dialog.showModal();
}
function persistDraft() {
  window.smartdevAttachmentDraft?.(attachments.map(file=>file.smartdevId).filter(Boolean));
}
window.addEventListener('smartdev-upload-draft',({detail})=>{
  const existing=new Set(attachments.map(file=>file.smartdevId));
  attachments.push(...detail.filter(file=>!existing.has(file.smartdevId)).slice(0,8-attachments.length));
  renderFiles();
  persistDraft();
});
function renderFiles() {
  byId('files').replaceChildren(...attachments.map((file, index) => {
    const item = document.createElement('li');
    if (/^image\/(png|jpeg|webp)$/.test(file.type)) {
      const image = document.createElement('img');
      const url = URL.createObjectURL(file);
      image.src = url;
      image.alt = file.name;
      image.onload = image.onerror = () => URL.revokeObjectURL(url);
      const open=document.createElement('button');
      open.type='button';
      open.setAttribute('aria-label','Open '+file.name);
      open.onclick=()=>imagePreview(file,file.name);
      open.append(image);
      item.append(open);
    }
    item.append(' ' + file.name + ' (' + file.size + ' bytes) ');
    if (file.type.startsWith('text/') || /\.(json|md|txt|csv|ya?ml)$/i.test(file.name)) {
      const preview = document.createElement('details');
      const label = document.createElement('summary');
      const content = document.createElement('pre');
      label.textContent = 'Preview text';
      preview.append(label, content);
      preview.addEventListener('toggle', async () => {
        if (preview.open) content.textContent = await file.slice(0, 4096).text();
      }, { once: true });
      item.append(preview);
    }
    const remove = document.createElement('button');
    remove.textContent = 'Remove';
    remove.onclick = () => { attachments.splice(index, 1); renderFiles(); persistDraft(); };
    item.append(remove);
    return item;
  }));
}
function attach(files) {
  byId('status').textContent = '';
  for (const file of files) {
    if (file.size > 20_000_000 || attachments.length >= 8) {
      byId('status').textContent = 'Attach up to eight files of 20 MB each.';
      continue;
    }
    attachments.push(file);
    if (window.smartdevUpload) {
      uploading++;
      byId('status').textContent='Uploading project attachments…';
      file.smartdevUpload=encodeFile(file).then(window.smartdevUpload).then(record=>{
        file.smartdevId=record.id;
        persistDraft();
        return record;
      }).catch(error=>{
        const index=attachments.indexOf(file);
        if (index!==-1) attachments.splice(index,1);
        byId('status').textContent=error.message;
        renderFiles();
        return null;
      }).finally(()=>{
        uploading--;
        if (!uploading && byId('status').textContent==='Uploading project attachments…') byId('status').textContent='';
      });
    }
  }
  renderFiles();
}
byId('upload').onchange = event => {
  attach(event.target.files);
  event.target.value = '';
};
document.addEventListener('paste', event => {
  const clipboard = event.clipboardData;
  if (!clipboard) return;
  const files = clipboard.files.length ? Array.from(clipboard.files) :
    Array.from(clipboard.items).filter(item => item.kind === 'file')
      .map(item => item.getAsFile()).filter(Boolean);
  if (!files.length) return;
  event.preventDefault();
  attach(files);
});
const dropZone=byId('prompt').closest('.composer') || byId('prompt');
const fileDrag=event=>Array.from(event.dataTransfer?.types || []).includes('Files');
dropZone.addEventListener('dragover',event=>{
  if (!fileDrag(event)) return;
  event.preventDefault();
  event.dataTransfer.dropEffect='copy';
  dropZone.classList.add('file-drag');
});
dropZone.addEventListener('dragleave',event=>{
  if (!dropZone.contains(event.relatedTarget)) dropZone.classList.remove('file-drag');
});
dropZone.addEventListener('drop',event=>{
  dropZone.classList.remove('file-drag');
  if (!fileDrag(event)) return;
  event.preventDefault();
  attach(event.dataTransfer.files);
});
function running(active) {
  for (const id of ['run', 'new']) { const control=byId(id); if (control) control.disabled=active; }
  byId('cancel').disabled = !active;
  byId('cancel').hidden = !active;
}
byId('run').onclick = async () => {
  const prompt = byId('prompt').value.trim() || (attachments.length ? 'Use the attached project files for this turn.' : '');
  const objective = byId('objective').value;
  const context = attachmentContext;
  if (!prompt) return;
  if (uploading) { byId('status').textContent='Files are still uploading.'; return; }
  running(true);
  const submission = Symbol();
  preparing = submission;
  try {
    const files = await Promise.all(attachments.map(async file=>{
      if (!file.smartdevUpload) return encodeFile(file);
      const uploaded=await file.smartdevUpload;
      if (!uploaded) throw Error('Attachment upload failed.');
      return {id:uploaded.id};
    }));
    if (preparing !== submission) return;
    preparing = undefined;
    if (objective !== byId('objective').value || context !== attachmentContext) { running(false); return; }
    vscode.postMessage({type: 'run', prompt, terms: byId('terms').value,
      objective, criterion: byId('criterion').value.trim(), files});
  } catch {
    if (preparing !== submission) return;
    preparing = undefined;
    byId('status').textContent = 'An attachment could not be read.';
    running(false);
  }
};
byId('cancel').onclick = () => {
  if (preparing) {
    preparing = undefined;
    running(false);
  } else {
    vscode.postMessage({type: 'cancel'});
  }
};
if (byId('new')) byId('new').onclick = () => vscode.postMessage({type: 'new', objective: byId('objective').value.trim()});
window.addEventListener('message', ({data}) => {
  if (data.type === 'output') {
    const output = byId('output');
    output.textContent = (output.textContent + data.text + '\n').slice(-100000);
  }
  if (data.type === 'objective') byId('objective').value = data.text;
  if (data.type === 'done') running(false);
});

window.addEventListener('smartdev-sent',()=>{ attachments.length=0; renderFiles(); persistDraft(); });
vscode.postMessage({type: 'ready'});
