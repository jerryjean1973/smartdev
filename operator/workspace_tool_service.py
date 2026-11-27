"""Serve the enrolled workspace's sandboxed Chromium and read-only Git transport."""
import hashlib, json, os, re, socket, socketserver, subprocess, threading, time
from pathlib import Path

INSTALL=Path("/opt/smartdev-workspace-tools")
SETTINGS=Path("/etc/smartdev/workspace-tools.json")
NAME=re.compile(r"command-dev-[0-9a-f-]{36}")
REPO=re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
def docker(*args,timeout=18):
    return subprocess.check_output(["docker",*args],text=True,stderr=subprocess.DEVNULL,timeout=timeout).strip()
def browser_name(cid):return "smartdev-chromium-"+cid[:16]
def browser_ready(name):
    probe = "fetch('http://127.0.0.1:19222/json/version',{signal:AbortSignal.timeout(1300)}).then(async r=>{if(!r.ok||!(await r.json()).webSocketDebuggerUrl)process.exit(1)}).catch(()=>process.exit(1))"
    try:
        docker("exec", name, "node", "-e", probe, timeout=3)
        return True
    except subprocess.SubprocessError:
        return False

def start_browser(record):
    with record["lock"]:
        record["used"]=time.monotonic()
        cid=record["id"];name=browser_name(cid)
        try:state=docker("inspect","--format","{{.State.Status}}",name)
        except subprocess.CalledProcessError:state=None
        if state=="running" and browser_ready(name):return
        if state:docker("rm","-f",name)
        docker("run","-d","--name",name,"--label","io.smartdev.tools=chromium","--label","io.smartdev.parent="+cid,
            "--user","1000:1000","--read-only","--cap-drop","ALL","--pids-limit","512","--cpus","2","--memory","512m",
            "--security-opt","no-new-privileges:true","--security-opt","apparmor="+record["apparmor"],
            "--security-opt","seccomp="+str(INSTALL/"chromium-seccomp.json"),
            "--tmpfs","/tmp:rw,nosuid,nodev,size=128m,mode=1777","--network","container:"+cid,
            "--mount","type=bind,src="+str(record["runtime"])+",dst=/opt/cc-browser,readonly",
            "-e","HOME=/tmp","-e","LD_LIBRARY_PATH=/opt/cc-browser/lib","-e","FONTCONFIG_FILE=/opt/cc-browser/fonts.conf",
            "--entrypoint","/opt/cc-browser/bin/chromium",record["image"],
            "--headless","--renderer-process-limit=3","--disable-extensions","--disable-dev-shm-usage","--disable-gpu","--disable-background-networking","--no-first-run",
            "--remote-debugging-port=19222","--remote-debugging-address=127.0.0.1","--user-data-dir=/tmp/profile","about:blank")
        for _ in range(35):
            if browser_ready(name):
                return
            time.sleep(.15)
        raise RuntimeError("Browser initialization failed")
class Handler(socketserver.StreamRequestHandler):
    rbufsize=0
    def handle(self):
        slots=self.server.slots
        if not slots.acquire(blocking=False):
            self.wfile.write(b"BUSY\n")
            return
        try:
            self.serve_request()
        finally:
            slots.release()

    def serve_request(self):
        self.connection.settimeout(10)
        try:
            msg=json.loads(self.rfile.readline(512))
            record=self.server.record
            if msg=={"action":"browser"}:
                start_browser(record);self.wfile.write(b"OK\n");return
            repo=msg.get("repository","").casefold()
            if msg.get("action")!="git-read" or repo not in record["repos"] or not REPO.fullmatch(repo):
                self.wfile.write(b"DENIED\n");return
            key=record["repos"][repo]
            if isinstance(key,dict) and "pinned" in key:
                self.connection.settimeout(None);self.wfile.write(b"OK\n")
                subprocess.run(["git","-c","uploadpack.allowFilter=false","-c","uploadpack.allowReachableSHA1InWant=true","upload-pack",key["pinned"]],
                               stdin=self.connection,stdout=self.connection,stderr=subprocess.DEVNULL,timeout=240)
                return
            if not Path(key).is_file():self.wfile.write(b"UNAVAILABLE\n");return
            self.connection.settimeout(None)
            command=["ssh","-T","-F","/dev/null","-i",key,"-o","IdentitiesOnly=yes","-o","BatchMode=yes",
                     "-o","StrictHostKeyChecking=yes","-o","UserKnownHostsFile="+str(INSTALL/"github_known_hosts"),
                     "-o","ConnectTimeout=8","git@github.com","git-upload-pack '"+repo+".git'"]
            self.wfile.write(b"OK\n")
            with subprocess.Popen(command,stdin=self.connection,stdout=self.connection,stderr=subprocess.DEVNULL) as child:
                try:child.wait(timeout=300)
                except subprocess.TimeoutExpired:child.kill()
        except (OSError,ValueError,RuntimeError,subprocess.SubprocessError):
            try:self.wfile.write(b"UNAVAILABLE\n")
            except OSError:pass
class Server(socketserver.ThreadingMixIn,socketserver.UnixStreamServer):
    daemon_threads=True
def provision(cid,settings):
    inspect=json.loads(docker("inspect",cid))[0]
    mount=next((Path(m["Source"]) for m in inspect["Mounts"] if m["Destination"]=="/run/command-memory" and not m["RW"]),None)
    if not mount:return None
    apparmor=settings.get("browser_apparmor") or inspect.get("AppArmorProfile") or "docker-default"
    name=mount.name
    configured=dict(settings.get("repositories",{}).get(name,{}))
    keys=settings.get("registered_git_keys")
    if keys:
        from base64 import b32decode
        from uuid import UUID
        project_name=name.removeprefix("p-")
        if re.fullmatch("[a-z2-7]{26}",project_name):
            project_id=UUID(bytes=b32decode(project_name.upper()+"======"))
            key=Path(keys)/str(project_id)
            env=dict(item.split("=",1) for item in inspect["Config"]["Env"] if "=" in item)
            repo=env.get("PROJECT_REPOSITORY","")
            if REPO.fullmatch(repo) and key.is_file() and not key.is_symlink():
                configured.setdefault(repo,str(key))
    repos={k.casefold():v for k,v in configured.items() if REPO.fullmatch(k)}
    record=dict(id=cid,image=inspect["Image"],mount=mount,apparmor=apparmor,runtime=Path(settings.get("browser_runtime",INSTALL/"chromium")),repos=repos,used=time.monotonic(),lock=threading.Lock())
    endpoint=mount/"tools.sock"
    endpoint.unlink(missing_ok=True)
    # Signed SMD release directories can exceed Linux's Unix socket pathname limit.
    directory_fd = os.open(mount, os.O_DIRECTORY)
    try:
        server=Server(f"/proc/self/fd/{directory_fd}/tools.sock",Handler)
    finally:
        os.close(directory_fd)
    server.record=record
    server.slots=threading.BoundedSemaphore(12)
    os.chown(endpoint,0,1000);endpoint.chmod(0o660)
    assets=INSTALL/"clients"
    for name in ["workspace-browser-start","workspace-screenshot","git-remote-smartdev","workspace-dev-login","command-dev-login.cjs","codex-governance-hook","workspace-git-credential","workspace-tools-check"]:
        source=assets/name
        if source.exists():
            if name=="codex-governance-hook":
                # Preserve custom/newer assignment policies when backfilling an older workspace image.
                original="c228793ad649b868cf17ef5b51d69a5d114ae70ca3dff42ae8ead04c4cfb77c2"
                installed=subprocess.run(["docker","exec",cid,"sha256sum","/usr/local/bin/"+name],capture_output=True,text=True).stdout.split()
                if installed and installed[0] not in {original,hashlib.sha256(source.read_bytes()).hexdigest()}:
                    continue
            dest="/usr/local/lib/" if name.endswith(".cjs") else "/usr/local/bin/"
            docker("cp",str(source),cid+":"+dest+name)
    docker("cp",str(record["runtime"]/"bin/wget"),cid+":/usr/local/bin/wget")
    docker("exec","-u","0",cid,"ln","-sfn","/usr/local/bin/workspace-screenshot","/usr/local/bin/screenshot")
    docker("exec","-u","0",cid,"ln","-sfn","/usr/local/bin/workspace-screenshot","/usr/local/bin/chromium")
    helper = "/usr/local/bin/workspace-git-credential"
    config_key = "credential.https://github.com.helper"
    existing = subprocess.run(["docker","exec","-u","0",cid,"git","config","--system","--get-all",config_key],capture_output=True,text=True).stdout.splitlines()
    if helper not in existing:
        docker("exec","-u","0",cid,"git","config","--system","--add",config_key,helper)
    docker("exec","-u","0",cid,"git","config","--system","credential.https://github.com.useHttpPath","true")
    for repo in repos:
        for url in ["https://github.com/"+repo,"git@github.com:"+repo]:
            # Core keeps upstream spelling; match the registered spelling as well as lowercase.
            original=next((s for s in configured if s.casefold()==repo),repo)
            for remote in {url,url[:-len(repo)]+original}:
                config="url.smartdev::"+repo+".insteadOf"
                existing=subprocess.run(["docker","exec","-u","0",cid,"git","config","--system","--get-all",config],capture_output=True,text=True).stdout.splitlines()
                if remote not in existing:docker("exec","-u","0",cid,"git","config","--system","--add",config,remote)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    return server
def main():
    active={}
    while True:
        settings=json.loads(SETTINGS.read_text())
        current=set(docker("ps","--no-trunc","--format","{{.ID}} {{.Names}}").splitlines())
        ids={row.split()[0] for row in current if NAME.fullmatch(row.split()[1])}
        for cid in ids-active.keys():
            try:
                server=provision(cid,settings)
                if server:active[cid]=server
            except (OSError,ValueError,subprocess.SubprocessError) as exc:print("Workspace tool enrollment failed for "+cid[:12]+": "+type(exc).__name__+" "+str(exc)[:120],flush=True)
        for cid,server in list(active.items()):
            r=server.record
            repair = Path("/run/smartdev-workspace-tool-repair")/cid
            if cid in ids and repair.exists():
                server.shutdown();server.server_close();active.pop(cid)
                try:
                    refreshed=provision(cid,settings)
                    if refreshed:
                        active[cid]=refreshed
                        repair.unlink(missing_ok=True)
                except (OSError,ValueError,subprocess.SubprocessError):
                    pass
                continue
            if cid not in ids:
                server.shutdown();server.server_close();active.pop(cid)
            if cid not in ids or (r["used"] and time.monotonic()-r["used"]>900):
                with r["lock"]:
                    if cid not in ids or time.monotonic()-r["used"]>900:
                        subprocess.run(["docker","rm","-f",browser_name(cid)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                        r["used"]=0
        time.sleep(12)
if __name__=="__main__":main()
