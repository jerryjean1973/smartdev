"""Reconcile owner MCP submissions with their public user events. Never replay a request."""
import hashlib
import json
from pathlib import Path
import subprocess
from uuid import UUID
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
from work_messages import visible_text

API = "smart-dev-ide-browser-api-1"
FETCH = """
import json
from store import database
from core import workspace_name
with database() as db:
    rows=db.execute('''SELECT p.id project,s.request_id,s.message_id,s.released,s.task_payload_sha256,
        s.result,c.id submitted_in,c.active_task_id,co.original_request,co.display_name
        FROM conversation_submission s JOIN projects p ON p.id=s.task_project_id
        JOIN project_conversation c ON c.id=s.conversation_id AND c.project_id=p.id
        LEFT JOIN objective_contract co ON co.project_id=p.id AND co.id=s.message_id
        WHERE s.environment='user' AND NOT p.archived AND substr(s.request_id::text,15,1)='5'
        AND s.task_payload_sha256 IS NOT NULL
        AND NOT EXISTS (SELECT 1 FROM acceptance_fixture f WHERE f.project_id=p.id)''').fetchall()
for row in rows:
    row['workspace']=workspace_name(row['project'])
print(json.dumps(rows,default=str))
"""
SAVE = """
import sys,json
from store import database
from mcp_request_history import execution_reference
from uuid import UUID
rows=json.load(sys.stdin)
with database() as db:
    for row in rows:
        if row.get('execution'):
            row['receipt']=execution_reference(UUID(row['project']),row['execution'])
        db.execute('''INSERT INTO mcp_task_request
            (project_id,request_id,task_id,message_id,execution_ref,kind,received_at,instruction,title,delivery,payload_digest)
            VALUES (%(project)s,%(request)s,%(task)s,%(message)s,%(receipt)s,%(kind)s,%(time)s,%(text)s,%(title)s,%(delivery)s,%(digest)s)
            ON CONFLICT(project_id,request_id) DO UPDATE SET
            payload_digest=EXCLUDED.payload_digest,
            kind=CASE WHEN mcp_task_request.kind='submission' THEN EXCLUDED.kind ELSE mcp_task_request.kind END,
            received_at=COALESCE(mcp_task_request.received_at,EXCLUDED.received_at),
            instruction=COALESCE(mcp_task_request.instruction,EXCLUDED.instruction),
            task_id=COALESCE(mcp_task_request.task_id,EXCLUDED.task_id),
            message_id=COALESCE(mcp_task_request.message_id,EXCLUDED.message_id),
            execution_ref=COALESCE(mcp_task_request.execution_ref,EXCLUDED.execution_ref),
            delivery=CASE WHEN EXCLUDED.delivery='accepted' THEN 'accepted' ELSE mcp_task_request.delivery END''',row)
print('Reconciled',len(rows),'MCP requests.')
"""


def command(*args, data=None):
    return subprocess.run(args,input=data,text=True,check=True,capture_output=True).stdout


def workspace_logs():
    ids=command("docker","ps","-aq","--filter","name=command-dev-").split()
    locations={}
    for cid in ids:
        # Limit Docker output to the workspace name and mounted home. Its environment contains credentials.
        info=command("docker","inspect","--format",'{{range .Config.Env}}{{if eq (index (split . "=") 0) "PROJECT_NAME"}}{{.}}{{end}}{{end}} {{range .Mounts}}{{if eq .Destination "/home/node"}}{{.Source}}{{end}}{{end}}',cid).strip().split()
        if len(info)==2 and info[0].startswith("PROJECT_NAME="):
            locations.setdefault(info[0].split("=",1)[1],set()).add(Path(info[1])/".local/state/command-web-chat")
    return locations


def user_events(directory, wanted):
    events={}
    # The conversation transcript and task log can overlap. Bind to the exact stored message ID.
    for log in [*directory.glob("*.conversation*"),*directory.glob("*.log")]:
        if not log.is_file() or log.is_symlink():
            continue
        with log.open(errors="replace") as stream:
            for line in stream:
                if '"smartdev.user"' not in line:
                    continue
                try:
                    event=json.loads(line)
                except ValueError:
                    continue
                if event.get("type")=="smartdev.user" and event.get("message") in wanted:
                    events[event["message"]]=event
    return events


def main():
    rows=json.loads(command("docker","exec",API,"python","-c",FETCH))
    locations=workspace_logs()
    sources={}
    for workspace in {r["workspace"] for r in rows}:
        wanted={r["message_id"] for r in rows if r["workspace"]==workspace}
        events={}
        for directory in locations.get(workspace,[]):
            events.update(user_events(directory,wanted))
        sources[workspace]=events
    data=[]
    known_payloads={}
    for r in rows:
        result=r["result"] or {}
        event=sources[r["workspace"]].get(r["message_id"],{})
        if result.get("executionId") and event.get("execution")!=result["executionId"]:
            event={}
        task=result.get("objective") or event.get("objective")
        created=task==r["message_id"]
        prompt=r["original_request"] if created and r["original_request"] else event.get("text")
        kind="create" if created else "message" if task else "submission"
        if task and prompt and not created:
            digest=hashlib.sha256(json.dumps([task,True,prompt,[]],ensure_ascii=False,separators=(",",":")).encode()).hexdigest()
            if digest==r["task_payload_sha256"]:
                kind="reply"
        data.append(dict(project=r["project"],request=r["request_id"],message=r["message_id"],task=task,
                         execution=result.get("executionId"),receipt=None,kind=kind,digest=r["task_payload_sha256"],
                         time=event.get("at") or event.get("smartdev_at"),
                         text=visible_text(prompt) or None,title=visible_text(r["display_name"]) if created else None,
                         delivery="accepted" if result.get("executionId") else "unconfirmed"))
        if task and prompt and result.get("executionId") and not created:
            # Only a follow-up digest binds a specific target task, instruction and reply mode.
            payload=[task,kind=="reply",prompt,[]]
            fingerprint=hashlib.sha256(json.dumps(payload,ensure_ascii=False,separators=(",",":")).encode()).hexdigest()
            if fingerprint==r["task_payload_sha256"]:
                known_payloads[(r["project"],fingerprint)]=(task,kind,visible_text(prompt))
    for record in data:
        match=known_payloads.get((record["project"],record["digest"]))
        if match and not record["task"] and record["delivery"]=="unconfirmed":
            record["task"],record["kind"],record["text"]=match
    print(command("docker","exec","-i",API,"python","-c",SAVE,data=json.dumps(data)).strip())


if __name__=="__main__":
    main()
