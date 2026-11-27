(() => {
  const configure=document.getElementById('configure-runtime');
  configure?.addEventListener('click',()=>window.parent.postMessage(
    {source:'smartdev-preview',action:'configure-secrets'},'https://smartdev.youkandoit.com'));
  const prefix=new URL(document.currentScript.src).pathname.replace(/\/\.__smartdev\/browser.js$/,'');
  function scoped(value) {
    const url=new URL(value,location.href);
    if (url.host!==location.host || !['http:','https:','ws:','wss:'].includes(url.protocol) ||
        url.pathname===prefix || url.pathname.startsWith(prefix+'/')) return value;
    url.pathname=prefix+url.pathname;
    return url.href;
  }
  const fetch=window.fetch.bind(window);
  let version;
  if (prefix) {
    const check=async()=>{
      try {
        const response=await fetch(prefix+'/.__smartdev/revision',{cache:'no-store'});
        if (!response.ok) return;
        const current=JSON.stringify(await response.json());
        if (version && version!==current) location.reload();
        version=current;
      } catch {}
    };
    void check();
    setInterval(check,1500);
  }
  window.fetch=(input,options)=>fetch(input instanceof Request ? new Request(scoped(input.url),input) : scoped(input),options);
  const open=XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open=function(method,url,...options){return open.call(this,method,scoped(url),...options);};
  for (const name of ['WebSocket','EventSource']) {
    const Constructor=window[name];
    window[name]=class extends Constructor { constructor(url,...options){super(scoped(url),...options);} };
  }
  const emit=(level,args)=>{
    if (window.parent===window) return;
    const message=level+': '+args.map(value=>typeof value==='string'?value:String(value)).join(' ').slice(0,1200);
    window.parent.postMessage({source:'smartdev-preview',message},'https://smartdev.youkandoit.com');
  };
  for (const level of ['log','warn','error']) {
    const original=console[level].bind(console);
    console[level]=(...args)=>{original(...args);try { emit(level,args); } catch {}};
  }
  window.addEventListener('error',event=>emit('error',[event.message]));
  window.addEventListener('unhandledrejection',event=>emit('error',[event.reason]));
})();
