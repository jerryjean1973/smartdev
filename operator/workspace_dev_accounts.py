"""Enroll workspace-scoped developer users through the target's standard sign-in API."""
import json,os,secrets,subprocess,sys,tempfile,time
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from urllib.parse import urlparse
from uuid import UUID

def write_config(file,data):
    with tempfile.NamedTemporaryFile(dir=file.parent,delete=False,mode='w') as tmp:
        json.dump(data,tmp);tmp.flush();os.fchown(tmp.fileno(),0,1000);os.fchmod(tmp.fileno(),0o640)
    os.replace(tmp.name,file)

def request(url,data=None,token=None):
    hdr={'Content-Type':'application/json'}
    if token:hdr['Coder-Session-Token']=token
    req=Request(url,data=json.dumps(data).encode() if data is not None else None,headers=hdr)
    try:
        with urlopen(req,timeout=10) as r:return r.status,json.load(r)
    except HTTPError as e:
        try:body=json.load(e)
        except ValueError:body={}
        return e.code,body

def owner_token(cfg):
    if 'coder_token_json' in cfg:
        obj=json.loads(Path(cfg['coder_token_json']).read_text())
        if isinstance(obj,dict) and 'email' in obj and 'password' in obj:
            status,login=request(cfg['coder_api']+'/api/v2/users/login',obj)
            if status not in (200,201):raise ValueError('Deployment owner authentication failed')
            return login['session_token']
        return obj if isinstance(obj,str) else obj['session_token']
    return next(l.partition('=')[2].strip() for l in Path(cfg['coder_env']).read_text().splitlines() if l.startswith('CODER_TOKEN='))

def coder_account(cfg,record,account,file,data):
    token=owner_token(cfg);base=cfg['coder_api'];wid=str(UUID(record['name'].removeprefix('command-dev-')))
    status,w=request(base+'/api/v2/workspaces/'+wid,token=token)
    if status!=200:raise ValueError('Workspace organization discovery HTTP '+str(status))
    organization=str(UUID(w['organization_id']))
    account.update(kind='coder',origin=cfg['coder_origin'],orgId=organization)
    write_config(file,data)
    status,result=request(base+'/api/v2/users/login',{'email':account['email'],'password':account['password']})
    if status!=201 and status!=200:
        name='dev-'+wid.replace('-','')[:17]
        code,response=request(base+'/api/v2/users',{'email':account['email'],'username':name,'name':'Workspace developer','password':account['password'],'organization_ids':[organization],'login_type':'password','user_status':'active'},token)
        if code not in (200,201,409):raise ValueError('Coder developer enrollment HTTP '+str(code)+' '+str(response.get('message',''))[:70])
        status,result=request(base+'/api/v2/users/login',{'email':account['email'],'password':account['password']})
    if status not in (200,201) or not result.get('session_token'):raise ValueError('Coder developer sign-in HTTP '+str(status))
    usercode,user=request(base+'/api/v2/users/me',token=result['session_token'])
    status,orgs=request(base+'/api/v2/users/me/organizations',token=result['session_token'])
    if usercode!=200 or user.get('email')!=account['email'] or user.get('roles') or status!=200 or organization not in [o['id'] for o in orgs]:
        raise ValueError('Coder developer organization/role verification failed')
    # The browser command obtains its own expiring session; retain no provisioning session.
    req=Request(base+'/api/v2/users/logout',method='POST',headers={'Coder-Session-Token':result['session_token']})
    try:urlopen(req,timeout=5).close()
    except HTTPError:pass

def app_query(cid,origin,route,body):
    script="""let s='';process.stdin.on('data',c=>s+=c);process.stdin.on('end',async()=>{const q=JSON.parse(s);try{const r=await fetch(q.url,{method:'POST',redirect:'error',headers:{'Content-Type':'application/json'},body:JSON.stringify(q.body),signal:AbortSignal.timeout(10000)});const d=await r.json();process.stdout.write(JSON.stringify({status:r.status,orgId:d.orgId||d.org?.id,role:d.platformRole,authenticated:!!d.token}))}catch(e){process.stdout.write('{"status":0}')}})"""
    raw=subprocess.check_output(['docker','exec','-i','-u','1000',cid,'node','-e',script],input=json.dumps({'url':origin+route,'body':body}),text=True,timeout=14)
    return json.loads(raw)

def app_account(record,spec,account,file,data):
    origin=spec['origin']
    if origin=='workspace-preview':
        port=json.loads((record['projects']/record['workspace']/'config/preview-runtime.json').read_text())['port']
        origin='http://127.0.0.1:'+str(int(port))
    account.update(kind='application',origin=origin)
    write_config(file,data)
    payload={'email':account['email'],'password':account['password']}
    if account.get('orgId'):payload['orgId']=account['orgId']
    result=app_query(record['id'],origin,'/api/auth/login',payload)
    if result['status']==401 and not account.get('orgId'):
        result=app_query(record['id'],origin,'/api/auth/signup',{**payload,'name':'Workspace developer','orgName':spec['organization']})
        if result['status'] not in (200,201,409):raise ValueError('Application developer enrollment HTTP '+str(result['status']))
        result=app_query(record['id'],origin,'/api/auth/login',payload)
    if not result.get('orgId') or not result.get('authenticated') or result.get('role')=='PLATFORM_ADMIN':
        raise ValueError('Application developer sign-in HTTP '+str(result['status']))
    if account.get('orgId') and account['orgId']!=result['orgId']:raise ValueError('Application organization changed')
    account['orgId']=result['orgId']
    confirmed=app_query(record['id'],origin,'/api/auth/login',{**payload,'orgId':account['orgId']})
    if not confirmed.get('authenticated') or confirmed.get('orgId')!=account['orgId']:raise ValueError('Default application organization was not confirmed')

def enroll(record,cfg):
    file=record['mount']/'dev-login.json'
    data=json.loads(file.read_text()) if file.exists() else {}
    for key in ['coder','application']:
        if key=='application' and record['workspace'] not in cfg.get('applications',{}):continue
        present=data.get(key,{})
        if "--verify" not in sys.argv and present.get("orgId") and time.time()-present.get("verifiedAt",0)<86400:
            if key=="coder" and present.get("origin")==cfg['coder_origin']:continue
            if key=="application":
                origin=cfg['applications'][record['workspace']]['origin']
                if origin=='workspace-preview':
                    state=record['projects']/record['workspace']/'config/preview-runtime.json'
                    if state.exists():origin='http://127.0.0.1:'+str(json.loads(state.read_text()).get('port'))
                if present.get('origin')==origin:continue
        if key not in data:
            data[key]={'email':'workspace-'+record['name'].removeprefix('command-dev-')+'@smartdev.invalid','password':secrets.token_urlsafe(28)+'aQ2!'}
            write_config(file,data)
        if key=='coder':coder_account(cfg,record,data[key],file,data)
        else:app_account(record,cfg['applications'][record['workspace']],data[key],file,data)
        data[key]["verifiedAt"]=int(time.time())
        write_config(file,data)
if __name__=='__main__':
    cfg=json.loads(Path('/etc/smartdev/workspace-tools.json').read_text())
    ids=subprocess.check_output(['docker','ps','-q','--filter','name=command-dev-'],text=True).split()
    for cid in ids:
        if len(sys.argv)>1 and not sys.argv[1].startswith(cid):continue
        obj=json.loads(subprocess.check_output(['docker','inspect',cid]))[0];mounts={m['Destination']:Path(m['Source']) for m in obj['Mounts']};mount=mounts.get('/run/command-memory')
        if not mount:continue
        rec=dict(id=cid,name=obj['Name'][1:],mount=mount,workspace=mount.name,projects=mounts['/projects'])
        try:enroll(rec,{**cfg,**cfg.get("workspace_coders",{}).get(mount.name,{})});print(mount.name,'developer sign-in verified')
        except Exception as e:print(mount.name,type(e).__name__,str(e)[:155])
