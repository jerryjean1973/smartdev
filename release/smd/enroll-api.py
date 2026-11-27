"""Create the local Coder owner and persist verified workspace URLs."""
import json
import os
import sys
from uuid import UUID

import httpx

from core import workspace_name
from store import database


def start():
    request = json.load(sys.stdin)
    with httpx.Client(base_url=os.environ["CODER_API_URL"], timeout=10, trust_env=False) as coder:
        login = {"email": request["email"], "password": request["password"]}
        initial = coder.post("/api/v2/users/first", json={
            **login, "username": "smartdev", "name": "SmartDev Owner", "trial": False,
        })
        auth = coder.post("/api/v2/users/login", json=login)
        if not auth.is_success:
            raise RuntimeError(f"Coder owner enrollment failed (setup {initial.status_code}, login {auth.status_code})")
        print(auth.json()["session_token"])


def finish():
    projects = json.load(sys.stdin)
    with database() as db:
        for project in projects:
            name = workspace_name(UUID(project["id"]))
            url = f'{os.environ["CODER_PUBLIC_URL"].rstrip("/")}/@smartdev/{name}'
            db.execute("""INSERT INTO projects (id,name,repository,runtime,status,workspace_url)
                VALUES (%s,%s,%s,%s,'created',%s)
                ON CONFLICT (id) DO UPDATE SET status='created',workspace_url=EXCLUDED.workspace_url,error=NULL
                WHERE NOT projects.archived""",
                (project["id"], project["name"], project["repository"], project["runtime"], url))
            if project.get("hostname"):
                db.execute("""INSERT INTO project_preview_url(project_id,environment,mode,url) VALUES (%s,'DEV','url',%s)
                    ON CONFLICT(project_id,environment) DO NOTHING""",(project["id"],"https://"+project["hostname"]+"/"))
            db.execute("""INSERT INTO audit (project_id,action) SELECT %s,'standalone:workspace-verified'
                WHERE NOT EXISTS (SELECT 1 FROM audit WHERE project_id=%s AND action='standalone:workspace-verified')""",
                (project["id"], project["id"]))


if __name__ == "__main__":
    {"start": start, "finish": finish}[sys.argv[1]]()
