"""Provision and close disposable runs using operator-only database access."""
import argparse
import hashlib
import json
import os
import secrets
from urllib.parse import quote
from uuid import UUID, uuid4

import httpx

from core import workspace_name
from store import database, notify


def create(token_file):
    project_id, conversation_id = uuid4(), uuid4()
    token = secrets.token_urlsafe(48)
    descriptor = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        output.write(token + "\n")
    with database() as db:
        user_id = db.execute("SELECT id FROM smartdev_owner WHERE singleton").fetchone()["id"]
        db.execute("""INSERT INTO projects (id,name,repository,runtime,status)
                      VALUES (%s,%s,'','other','queued')""", (project_id, f"Acceptance {project_id}"))
        db.execute("""INSERT INTO project_conversation (id,user_id,project_id,name)
                      VALUES (%s,%s,%s,'Acceptance Main')""", (conversation_id,user_id,project_id))
        db.execute("""INSERT INTO project_conversation_pointer (user_id,project_id,conversation_id)
                      VALUES (%s,%s,%s)""", (user_id,project_id,conversation_id))
        db.execute("""INSERT INTO acceptance_fixture (project_id,user_id,conversation_id,token_digest)
                      VALUES (%s,%s,%s,%s)""",
                   (project_id,user_id,conversation_id,hashlib.sha256(token.encode()).hexdigest()))
        db.execute("INSERT INTO audit (project_id,action) VALUES (%s,'acceptance_create')", (project_id,))
    notify()
    return {"project_id": str(project_id), "conversation_id": str(conversation_id),
            "workspace_name": workspace_name(project_id)}


def close(project_id):
    with database() as db:
        project = db.execute("""SELECT p.status,f.closed_at FROM projects p
            JOIN acceptance_fixture f ON f.project_id=p.id WHERE p.id=%s FOR UPDATE OF p""",
            (project_id,)).fetchone()
        if not project:
            raise ValueError("No acceptance fixture for this project")
        if project["closed_at"]:
            return
        if project["status"] == "provisioning":
            raise ValueError("Retry cleanup after workspace provisioning settles")
        if db.execute("""SELECT 1 FROM conversation_execution e JOIN project_conversation c
                       ON c.id=e.conversation_id WHERE c.project_id=%s
                       AND e.finished_at IS NULL AND e.expires_at>now()""", (project_id,)).fetchone():
            raise ValueError("Retry cleanup after the active execution stops")
        if db.execute("""SELECT 1 FROM objective_run WHERE project_id=%s
                       AND finished_at IS NULL AND lease_until>now()""", (project_id,)).fetchone():
            raise ValueError("Retry cleanup after the active task stops")
        db.execute("UPDATE acceptance_fixture SET closed_at=now() WHERE project_id=%s", (project_id,))
        db.execute("UPDATE projects SET archived=true,status=CASE WHEN status='queued' THEN 'failed' ELSE status END WHERE id=%s", (project_id,))
        db.execute("INSERT INTO audit (project_id,action) VALUES (%s,'acceptance_close')", (project_id,))


def stop(project_id):
    """Run from the worker, which holds the Coder provisioning credential."""
    with database() as db:
        fixture = db.execute("SELECT closed_at FROM acceptance_fixture WHERE project_id=%s", (project_id,)).fetchone()
    if not fixture or not fixture["closed_at"]:
        raise ValueError("Close this acceptance fixture before stopping its workspace")
    name = workspace_name(project_id)
    owner = quote(os.environ["CODER_OWNER"], safe="")
    with httpx.Client(base_url=os.environ["CODER_API_URL"], timeout=15,
                      headers={"Coder-Session-Token": os.environ["CODER_TOKEN"]}) as client:
        response = client.get(f"/api/v2/users/{owner}/workspace/{name}")
        if response.status_code == 404:
            return
        response.raise_for_status()
        workspace = response.json()
        if (workspace["name"] != name
                or workspace["template_name"] != os.getenv("CODER_ACCEPTANCE_TEMPLATE", "smartdev-acceptance")):
            raise ValueError("Coder workspace does not match the acceptance fixture")
        if workspace["latest_build"]["status"] in {"stopped", "stopping", "deleted"}:
            return
        response = client.post(f"/api/v2/workspaces/{workspace['id']}/builds", json={"transition": "stop"})
        response.raise_for_status()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("create").add_argument("token_file")
    sub.add_parser("close").add_argument("project_id", type=UUID)
    sub.add_parser("stop").add_argument("project_id", type=UUID)
    args = parser.parse_args()
    if args.action == "create":
        print(json.dumps(create(args.token_file)))
    elif args.action == "close":
        close(args.project_id)
    else:
        stop(args.project_id)
