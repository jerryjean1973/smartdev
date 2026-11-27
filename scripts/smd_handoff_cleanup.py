"""Erase deployment-local credential versions and retire credentials from the previous assignment."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from uuid import UUID

# This runs in the existing installation-local vault bootstrap container. Root tokens stay there.
VAULT_PROGRAM = """
import json,runpy,sys
from urllib.error import HTTPError
from pathlib import Path
b=runpy.run_path('/vault-bootstrap.py')
root=json.loads(b['RECOVERY_FILE'].read_text())['root_token']
projects,version=json.load(sys.stdin)
def children(path):
    try:
        return b['call']('smartdev/metadata/'+path,token=root,method='LIST')['data']['keys']
    except HTTPError as exc:
        if exc.code==404: return []
        raise
def clear(path,protect=False):
    for key in children(path):
        if '/' in key.rstrip('/') or key in ('.','..') or not key: raise ValueError('Invalid secret path')
        if protect and key=='assignment-'+str(version)+'/': continue
        if key.endswith('/'):
            clear(path+'/'+key[:-1])
        else:
            b['call']('smartdev/metadata/'+path+'/'+key,token=root,method='DELETE')
    left=children(path)
    if protect: left=[s for s in left if s!='assignment-'+str(version)+'/']
    if left: raise RuntimeError('Secret cleanup verification failed')
for project in projects:
    for env in ('DEV','QA','PREPROD','PROD'):
        clear('projects/'+project+'/'+env,True)
clear('integrations')
"""

REVOKE_PROGRAM = """
from store import queue
from coder_session import REVOKED,BROWSER_TTL,upstream
import json,sys
r=queue()
for entry in r.smembers('smd:tenant-coder:'+sys.argv[1]):
    key=entry.decode()
    with r.lock(key+':lock',timeout=30,blocking_timeout=5):
        token=r.get(key)
        if token and token!=REVOKED:
            with upstream() as client:
                res=client.post('/api/v2/users/logout',headers={'Coder-Session-Token':token.decode()})
                if res.status_code not in (200,204,401,403):res.raise_for_status()
        r.set(key,REVOKED,ex=BROWSER_TTL)
r.delete('smd:tenant-coder:'+sys.argv[1])
"""


def run(project):
    project=str(UUID(project))
    data=Path("/opt/smartdev")/project/"data"
    root=data/"portal-access"
    inbox=root/"generations"/"handoff.json"
    if not inbox.is_file():
        return
    # Read the untrusted mailbox exactly once, never following a symlink as root.
    fd=os.open(inbox,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,"rb") as f:
        raw=f.read(4097)
    if len(raw)>4096:
        raise ValueError("Invalid handoff request")
    obj=json.loads(raw)
    cfg=json.loads((root/"handoff-installation.json").read_text())
    msg=obj["payload"]
    version=msg["version"]
    if (set(msg)!={"task","instance","version","nonce","time"} or msg["task"]!=cfg["task"] or msg["instance"]!=cfg["instance"]
        or not isinstance(version,int) or not 0<version<2**31 or abs(time.time()-msg["time"])>90):
        raise ValueError("Invalid handoff request")
    UUID(msg["nonce"])
    message=json.dumps(msg,sort_keys=True,separators=(",",":")).encode("ascii")
    digest=hashlib.sha256(message).hexdigest()
    with tempfile.TemporaryDirectory(dir="/run") as folder:
        temp=Path(folder)
        (temp/"auth").write_bytes(bytes.fromhex("302a300506032b6570032100")+base64.b64decode(cfg["authority"],validate=True))
        (temp/"message").write_bytes(message)
        (temp/"signature").write_bytes(base64.b64decode(obj["signature"],validate=True))
        subprocess.run(["openssl","pkeyutl","-verify","-rawin","-pubin","-keyform","DER","-inkey",temp/"auth",
                        "-sigfile",temp/"signature","-in",temp/"message"],check=True,stdout=subprocess.DEVNULL)
    ack=root/"confirmations"/digest
    if ack.exists():
        return
    projects=[str(UUID(p["id"])) for p in json.loads((data/"verified-projects.json").read_text())]
    if any((data/"project-runtime"/(p+".env")).is_file() for p in projects):
        raise RuntimeError("Legacy application credentials require a verified clean runtime before transfer")
    def service(name):
        found=subprocess.check_output(["docker","ps","-q","--filter","label=com.docker.compose.project=smd-"+project,
                    "--filter","label=com.docker.compose.service="+name],text=True).strip()
        if not re.fullmatch("[a-f0-9]{12,64}",found): raise ValueError("Ambiguous service")
        return found
    subprocess.run(["docker","exec","-i",service("vault-bootstrap"),"python","-c",VAULT_PROGRAM],
                   input=json.dumps([projects,version]),text=True,check=True,timeout=30)
    api=service("api")
    subprocess.run(["docker","exec",api,"python","-c",REVOKE_PROGRAM,str(version-1)],check=True,timeout=20)
    # Refresh processes only in workspaces that actually received project runtime values.
    for p in projects:
        delivered=subprocess.check_output(["docker","exec",api,"python","-c",
            "from store import queue;import sys;print(int(bool(queue().exists(sys.argv[1]))))",
            f"smd:runtime-values:{version-1}:{p}"],text=True).strip()
        if delivered=="1":
            names=subprocess.check_output(["docker","ps","--filter","label=smartdev.project.id="+p,
                    "--format","{{.Names}}"],text=True).splitlines()
            for name in names:
                if re.fullmatch(r"command-dev-[0-9a-f-]{36}",name):
                    subprocess.run(["docker","restart",name],check=True,timeout=60,stdout=subprocess.DEVNULL)
    ack.write_text("verified\n")
    ack.chmod(0o444)


if __name__=="__main__":
    run(sys.argv[1])
