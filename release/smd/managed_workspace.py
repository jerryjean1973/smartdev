"""Upgrade the coding portal and default previews in one verified managed install."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from uuid import UUID

from apps import prepare


def run(*args, input=None):
    return subprocess.check_output(args, input=input, text=True).strip()


def service(project, role):
    name = "smd-" + project
    ids = run("docker","ps","-q","--filter","label=com.docker.compose.project="+name,
              "--filter","label=com.docker.compose.service="+role).splitlines()
    if len(ids)!=1:
        raise ValueError("Expected one enrolled "+role+" service")
    return json.loads(run("docker","inspect",ids[0]))[0]


def stack(obj):
    labels = obj["Config"]["Labels"]
    root = Path(labels["com.docker.compose.project.working_dir"]).resolve(strict=True)
    cmd = ["docker","compose","--project-directory",str(root),"-p",labels["com.docker.compose.project"]]
    for file in (root/"images.env",root/".env"):
        if file.is_file():
            cmd.extend(["--env-file",str(file)])
    return root, cmd, labels["com.docker.compose.project.config_files"].split(",")


def apply_service(project, role, overlay, config, networks=None):
    obj=service(project,role)
    _,cmd,files=stack(obj)
    model={"services":{role:config}}
    if networks:
        model["networks"]=networks
    overlay.write_text(json.dumps(model))
    names=[f for f in files if f!=str(overlay)]
    args=[arg for file in names for arg in ("-f",file)]
    up=["up","-d","--no-deps","--no-build","--pull","never","--wait",role]
    try:
        run(*cmd,*args,"-f",str(overlay),*up)
    except subprocess.CalledProcessError:
        if str(overlay) not in files:
            run(*cmd,*args,*up)
        raise


def tenant_chat(data, files, project, manifest, edge, names):
    host=(data/"public-host").read_text().strip()
    if not re.fullmatch(r"[a-z0-9.-]+",host):
        raise ValueError("Invalid enrolled portal host")
    tenant="https://"+host
    owner="https://smartdev.youkandoit.com"
    template=data/"coder-template"/"deployment.json"
    config=json.loads(template.read_text())
    build=files/"chat-image"
    build.mkdir(exist_ok=True)
    base=run("docker","image","inspect",config["workspace_image"],"--format","{{.Id}}")
    changed=False
    for name in ("browser.js","index.html"):
        source=build/name
        run("docker","cp",names[0]+":/opt/command-chat/"+name,str(source))
        text=source.read_text()
        if name=="browser.js" and owner not in text and tenant not in text:
            raise ValueError("Unrecognized workspace chat origin")
        source.write_text(text.replace(owner,tenant))
        source.chmod(0o644)
        for workspace in names:
            run("docker","cp",str(source),workspace+":/opt/command-chat/"+name)
    label=run("docker","image","inspect",base,"--format",'{{index .Config.Labels "io.smartdev.chat-origin"}}')
    if label!=tenant:
        tag="local/smd-chat:"+hashlib.sha256((base+tenant).encode()).hexdigest()[:24]
        (build/"Dockerfile").write_text("FROM "+base+"\nCOPY browser.js index.html /opt/command-chat/\n")
        run("docker","build","--label","io.smartdev.chat-origin="+tenant,"-t",tag,str(build))
        config["workspace_image"]=run("docker","image","inspect",tag,"--format","{{.Id}}")
        template.write_text(json.dumps(config,sort_keys=True))
        changed=True
    if changed or not (data/"tenant-chat-template-ready").is_file():
        # Use the tenant's own Coder owner account. The CLI accepts its short-lived session over stdin.
        login="""import json,os,httpx
from pathlib import Path
a=json.loads(Path('/run/command-coder/owner.json').read_text())
r=httpx.post(os.environ['CODER_API_URL']+'/api/v2/users/login',json={k:a[k] for k in ('email','password')},timeout=8)
r.raise_for_status()
print(r.json()['session_token'])
"""
        token=run("docker","exec",service(project,"api")["Id"],"python","-c",login)
        run("docker","exec","-i",service(project,"coder")["Id"],"sh","-c",
            'read -r CODER_SESSION_TOKEN; export CODER_SESSION_TOKEN; exec coder --url http://127.0.0.1:7080 templates push smartdev-deployed --directory /standalone-template --yes',input=token+"\n")
        (data/"tenant-chat-template-ready").write_text(config["workspace_image"]+"\n")
    for filename in ("Caddyfile","Caddyfile-enrolled"):
        path=data/filename
        if not path.exists():
            continue
        original=path.read_text()
        if "@tenant_coder_apps" in original:
            continue
        needle="    reverse_proxy coder:7080\n"
        if original.count(needle)!=1:
            raise ValueError("Expected the verified Coder proxy")
        import base64
        app_paths=["/@smartdev/p-"+base64.b32encode(UUID(item["id"]).bytes).decode().rstrip("=").lower()+"/apps/*" for item in manifest]
        rules=("    @tenant_coder_apps path "+" ".join(app_paths)+"\n"
               "    reverse_proxy @tenant_coder_apps coder:7080 {\n"
               "        header_down Content-Security-Policy \"frame-ancestors 'self' https://smartdev[.]youkandoit[.]com\" \"frame-ancestors 'self' "+tenant+"\"\n    }\n")
        updated=original.replace(needle,rules+needle)
        candidate=files/(filename+".tenant")
        candidate.write_text(updated)
        tmp="/tmp/smd-tenant-chat.Caddyfile"
        run("docker","cp",str(candidate),edge["Id"]+":"+tmp)
        run("docker","exec",edge["Id"],"caddy","validate","--adapter","caddyfile","--config",tmp)
        if filename=="Caddyfile":
            run("docker","exec",edge["Id"],"caddy","reload","--adapter","caddyfile","--config",tmp)
        path.write_text(updated)


def install(project,version):
    project=str(UUID(project))
    if not re.fullmatch("[a-f0-9]{64}",version):
        raise ValueError("Invalid workbench digest")
    files=Path(__file__).resolve().parent
    data=Path("/opt/smartdev")/project/"data"
    manifest=json.loads((data/"verified-projects.json").read_text())
    if project not in {str(UUID(row["id"])) for row in manifest}:
        raise ValueError("Missing enrolled project")
    api=service(project,"api")
    edge=service(project,"edge")
    root,_,_=stack(api)
    os.chdir(files)
    routes=prepare(data,manifest)
    for row in manifest:
        host=row.get("hostname")
        name="smd-app-"+UUID(row["id"]).hex
        proxy=re.search(re.escape(host)+r":443 \{[^}]*?reverse_proxy ("+name+r"-preview:\d+)\s*\}",routes)
        if not proxy:
            continue
        if name not in edge["NetworkSettings"]["Networks"]:
            run("docker","network","connect",name,edge["Id"])
        endpoint=proxy[1]
        for file in ("Caddyfile","Caddyfile-enrolled"):
            target=data/file
            if not target.is_file():
                continue
            previous=target.read_text()
            needle='respond "Application installed. Tenant configuration is pending." 503'
            pattern=r"(?m)^("+re.escape(host)+r":443 \{\n[^}]*?)"+re.escape(needle)
            updated,count=re.subn(pattern,lambda m:m[1]+"reverse_proxy "+endpoint,previous)
            if count==0:
                continue
            if count!=1:
                raise ValueError("Ambiguous application route")
            candidate=files/(file+".candidate")
            candidate.write_text(updated)
            config="/tmp/smd-preview.Caddyfile"
            run("docker","cp",str(candidate),edge["Id"]+":"+config)
            run("docker","exec",edge["Id"],"caddy","validate","--adapter","caddyfile","--config",config)
            if file=="Caddyfile":
                run("docker","exec",edge["Id"],"caddy","reload","--adapter","caddyfile","--config",config)
            target.write_text(updated)
    (root/"app-networks.json").write_bytes((files/"app-networks.json").read_bytes())
    run("docker","exec","-i",api["Id"],"python","-c",(files/"enroll-api.py").read_text(),"finish",input=json.dumps(manifest))
    portal=service(project,"portal")
    tag="local/smd-tenant-portal:"+version[:24]
    build=files/"image"
    build.mkdir(exist_ok=True)
    target=build/"next.tar.gz"
    if not target.exists():
        os.link(files/"next.tar.gz",target)
    (build/"Dockerfile").write_text("FROM "+portal["Image"]+"\nADD --chown=node:node next.tar.gz /app/\n")
    if portal["Config"]["Image"]!=tag:
        run("docker","build","-t",tag,str(build))
    apply_service(project,"portal",files/"portal.json",{"image":tag})
    networks={}
    workspaces=[]
    for row in manifest:
        names=run("docker","ps","--filter","label=smartdev.project.id="+str(UUID(row["id"])),"--format","{{.Names}}").splitlines()
        workspace=[name for name in names if re.fullmatch(r"command-dev-[a-f0-9-]{36}",name)]
        if len(workspace)!=1:
            raise ValueError("Expected one running workspace for the verified project")
        workspaces.extend(workspace)
        net="command-net-"+str(UUID(workspace[0][12:]))
        attached=json.loads(run("docker","inspect",workspace[0],"--format","{{json .NetworkSettings.Networks}}"))
        if net not in attached:
            raise ValueError("Workspace network differs from its verified identity")
        networks[net]={"external":True,"name":net}
    apply_service(project,"coder",files/"coder.json",{"environment":{"CODER_DERP_FORCE_WEBSOCKETS":"true"},
                  "networks":["default",*networks]},networks)
    tenant_chat(data,files,project,manifest,edge,workspaces)


if __name__=="__main__":
    install(*sys.argv[1:])
