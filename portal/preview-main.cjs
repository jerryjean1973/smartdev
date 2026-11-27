// Main follows the deployed owner DEV release. Task worktrees run their edited source.
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const {spawn,spawnSync} = require('node:child_process');

const project = 'p-xssqdt37lbgnnmsugo7u7zempq';
const main = '/projects/'+project+'/repository/portal';
const prefix = '/@validation/'+project+'/apps/dev-preview';
if (fs.realpathSync('.') === main && process.env.SMARTDEV_PREVIEW_BASE_PATH === prefix) {
  const published = 'https://smartdev.youkandoit.com/';
  http.createServer((request,response)=>{
    if (!['GET','HEAD'].includes(request.method)) return response.writeHead(405).end();
    response.writeHead(200,{'Content-Type':'text/html; charset=utf-8','Cache-Control':'no-store',
      'Referrer-Policy':'no-referrer','Content-Security-Policy':"default-src 'none'; base-uri 'none'"});
    response.end('<!doctype html><meta charset="utf-8"><title>Command Center</title>'+
      '<meta http-equiv="refresh" content="0;url='+published+'"><a href="'+published+'">Open Command Center</a>');
  }).listen(Number(process.env.PORT), '127.0.0.1');
} else {
  const next = path.resolve('node_modules/next/dist/bin/next');
  if (!fs.existsSync(next)) {
    const installed = spawnSync('npm',['ci','--no-audit','--no-fund'],{stdio:'inherit'});
    if (installed.status !== 0) process.exit(installed.status || 1);
  }
  const child=spawn(process.execPath,[next,'dev','--hostname','127.0.0.1','--port',process.env.PORT],{stdio:'inherit'});
  child.on('exit',code=>process.exit(code ?? 1));
  process.on('SIGTERM',()=>child.kill('SIGTERM'));
}
