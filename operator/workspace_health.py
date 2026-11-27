"""Exercise the workspace agent and repair its managed tools on a failed check."""
import json
from pathlib import Path
import subprocess
import time

TOOLS=Path("/opt/smartdev-workspace-tools")
STATE=Path("/var/lib/smartdev-workspace-tools")
REPAIR=Path("/run/smartdev-workspace-tool-repair")
ENTRY='cd "/projects/$PROJECT_NAME/repository" && exec codex sandbox -c sandbox_mode=workspace-write -c sandbox_workspace_write.network_access=true -- workspace-tools-check'

def verify(cid):
    try:
        # The MCP server is a registered workspace process. Check its real Git directory,
        # alongside the existing agent-shell transport and browser checks below.
        tool = subprocess.run(
            ["docker", "exec", "--user", "1000:1000", cid, "/usr/local/bin/workspace_git.py", "--health"],
            capture_output=True, timeout=15,
        )
        registration = subprocess.run(
            ["docker", "exec", "--user", "1000:1000", cid, "sh", "-c",
             'cd "/projects/$PROJECT_NAME/repository" && exec codex mcp get workspace_git --json'],
            capture_output=True, timeout=12,
        )
        if tool.returncode or registration.returncode:
            return "git"
        try:
            registered = json.loads(registration.stdout)
            if not registered["enabled"] or registered["transport"]["command"] != "/usr/local/bin/workspace_git.py":
                return "git"
        except (KeyError, TypeError, ValueError):
            return "git"
        result=subprocess.run(["docker","exec","--user","1000:1000",cid,"sh","-c",ENTRY],
                              capture_output=True,text=True,timeout=145)
    except subprocess.TimeoutExpired:
        return "timeout"
    if not result.returncode and result.stdout.strip()=="healthy":
        return "healthy"
    stage=result.stdout.strip()
    return stage if stage in {"account","git","browser","network","tools"} else "agent"

def main():
    STATE.mkdir(parents=True,exist_ok=True)
    REPAIR.mkdir(mode=0o700,exist_ok=True)
    ids=subprocess.check_output(["docker","ps","--no-trunc","-q","--filter","name=command-dev-"],text=True).split()
    report={}
    for cid in ids:
        obj=json.loads(subprocess.check_output(["docker","inspect",cid]))[0]
        mount=next((m for m in obj["Mounts"] if m["Destination"]=="/run/command-memory" and not m["RW"]),None)
        if not mount:
            continue
        workspace=Path(mount["Source"]).name
        stage=verify(cid)
        if stage!="healthy":
            if stage=="account":
                subprocess.run(["python3",str(TOOLS/"accounts.py"),cid,"--verify"],timeout=100)
            marker=REPAIR/cid
            marker.touch(mode=0o600)
            for _ in range(30):
                if not marker.exists():
                    break
                time.sleep(1)
            second=verify(cid)
        else:
            second=stage
        report[workspace]={"status":second,"first_check":stage,"checked_at":int(time.time())}
        print(workspace,second,flush=True)
    tmp=STATE/"health.tmp"
    tmp.write_text(json.dumps(report)+"\n")
    tmp.replace(STATE/"health.json")
    return int(any(row["status"]!="healthy" for row in report.values()))

if __name__=="__main__":
    raise SystemExit(main())
