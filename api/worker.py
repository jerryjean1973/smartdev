import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
import time
from urllib.parse import quote

import httpx
from psycopg import Error
from redis.exceptions import RedisError

from core import project_token, workspace_name
from store import database, queue

def write_workspace_access(project_id):
    """Reconcile protected bootstrap configuration, including existing workspaces."""
    name = workspace_name(project_id)
    config = Path("/workspace-config") / name
    config.mkdir(mode=0o755, exist_ok=True)
    with NamedTemporaryFile(mode="w", dir=config, delete=False) as pending:
        json.dump({
            "token": project_token(bytes.fromhex(os.environ["MEMORY_ACCESS_KEY"]), project_id),
            "url": f'{os.getenv("MEMORY_WORKSPACE_ORIGIN", os.environ["PORTAL_ORIGIN"])}/api/workspace-memory/{project_id}',
            "environment": os.getenv("SMARTDEV_ENVIRONMENT", "DEV").upper(),
        }, pending)
        pending.flush()
        os.fchmod(pending.fileno(), 0o444)
    os.replace(pending.name, config / "memory.json")


def reconcile_workspace_access():
    with database() as db:
        projects = db.execute("SELECT id FROM projects WHERE NOT archived AND status='created'").fetchall()
    for project in projects:
        write_workspace_access(project["id"])


def provision(client, project):
    name = workspace_name(project["id"])
    write_workspace_access(project["id"])
    owner = quote(os.environ["CODER_OWNER"], safe="")
    lookup = f"/api/v2/users/{owner}/workspace/{name}"
    response = client.get(lookup)
    if response.status_code == 404:
        organization = quote(os.environ["CODER_ORGANIZATION_ID"], safe="")
        template_id = os.environ["CODER_TEMPLATE_ID"]
        if project["acceptance"]:
            template_name = quote(os.getenv("CODER_ACCEPTANCE_TEMPLATE", "smartdev-acceptance"), safe="")
            template = client.get(f"/api/v2/organizations/{organization}/templates/{template_name}")
            template.raise_for_status()
            template_id = template.json()["id"]
        response = client.post(f"/api/v2/organizations/{organization}/members/{owner}/workspaces", json={
            "name": name,
            "template_id": template_id,
            "rich_parameter_values": [
                {"name": "repository", "value": project["repository"]},
                {"name": "runtime", "value": project["runtime"]},
            ],
        })
        if response.status_code == 409:
            response = client.get(lookup)
    response.raise_for_status()
    return f'{os.environ["CODER_PUBLIC_URL"].rstrip("/")}/@{owner}/{name}'

def run_one(client):
    with database() as db:
        project = db.execute("""SELECT p.*, EXISTS (SELECT 1 FROM acceptance_fixture f WHERE f.project_id=p.id) AS acceptance
            FROM projects p
            WHERE NOT archived AND (status='queued' OR (status='provisioning' AND attempted_at < now() - interval '5 minutes'))
            ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1""").fetchone()
        if not project:
            return False
        db.execute("UPDATE projects SET status='provisioning',attempted_at=now() WHERE id=%s", (project["id"],))
    try:
        url = provision(client, project)
        status, error = "created", None
    except httpx.HTTPStatusError as exc:
        url, status = None, "failed"
        error = f"Coder returned HTTP {exc.response.status_code}. Check workspace permissions, template and GitHub authorization in Coder."
    except OSError:
        url, status, error = None, "failed", "Workspace configuration storage is unavailable."
    except httpx.RequestError:
        url, status, error = None, "failed", "Coder could not be reached. Check connectivity and retry."
    with database() as db:
        db.execute("UPDATE projects SET status=%s,workspace_url=%s,error=%s WHERE id=%s",
                   (status, url, error, project["id"]))
        db.execute("INSERT INTO audit (project_id,action) VALUES (%s,%s)", (project["id"], status))
    return True

def main():
    reconcile_workspace_access()
    with httpx.Client(base_url=os.environ["CODER_API_URL"], timeout=20, headers={
        "Coder-Session-Token": os.environ["CODER_TOKEN"],
    }) as client:
        while True:
            try:
                if not run_one(client):
                    queue().blpop("provision", timeout=4)
            except (Error, RedisError):
                time.sleep(3)

if __name__ == "__main__":
    main()
