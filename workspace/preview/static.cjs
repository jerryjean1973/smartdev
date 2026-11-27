/* Serve web assets without exposing checkout metadata or arbitrary files. */
const http=require('node:http');
const fs=require('node:fs/promises');
const path=require('node:path');
const root=process.cwd();
const types={'.html':'text/html; charset=utf-8','.css':'text/css; charset=utf-8','.js':'text/javascript; charset=utf-8',
  '.mjs':'text/javascript; charset=utf-8','.svg':'image/svg+xml','.png':'image/png','.jpg':'image/jpeg',
  '.jpeg':'image/jpeg','.gif':'image/gif','.webp':'image/webp','.ico':'image/x-icon','.woff':'font/woff','.woff2':'font/woff2'};
http.createServer(async (request,response)=>{
  if (!['GET','HEAD'].includes(request.method)) return response.writeHead(405).end();
  try {
    const name=decodeURIComponent(new URL(request.url,'http://localhost').pathname);
    const parts=name.split('/').filter(Boolean);
    if (parts.some(part=>part.startsWith('.') || /[\\\x00]/.test(part))) return response.writeHead(404).end();
    if (!path.extname(parts.at(-1)||'')) parts.push('index.html');
    const type=types[path.extname(parts.at(-1)).toLowerCase()];
    if (!type) return response.writeHead(404).end();
    const file=path.join(root,...parts);
    if (await fs.realpath(file)!==file || !(await fs.stat(file)).isFile()) return response.writeHead(404).end();
    const bytes=await fs.readFile(file);
    response.writeHead(200,{'Content-Type':type,'Content-Length':bytes.length,'X-Content-Type-Options':'nosniff'});
    response.end(request.method==='HEAD'?undefined:bytes);
  } catch {response.writeHead(404).end();}
}).listen(Number(process.env.PORT),'127.0.0.1');
