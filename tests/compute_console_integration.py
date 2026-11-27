"""Exercise Compute ownership, tenant isolation and audit history in the isolated integration database."""

import os
from uuid import UUID, uuid4
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
from core import issue_session, project_token
from store import database
from main import app

ORIGIN = os.environ["PORTAL_ORIGIN"]
ADMIN = "/admin/compute/deployments"


def run():
    projects = [uuid4() for _ in range(3)]
    first, second, outsider = projects
    job, instance = uuid4(), uuid4()
    names = [f"Tenant {str(value)[:8]}" for value in projects]
    sample = {
        "ipv4": "192.0.2.12",
        "portal_url": "https://portal.example.test",
        "coder_url": "https://coder.example.test",
        "ssh_user": "ubuntu",
        "apps": [
            {
                "project_id": str(p),
                "url": "https://app.example.test",
                "revision": "a" * 40,
                "runtime_status": "awaiting_tenant_configuration",
            }
            for p in projects[:2]
        ],
    }
    with database() as db:
        assert db.execute("SELECT current_database() AS name").fetchone()["name"] == "cc_compute_integration"
        for project, tenant in zip(projects, names):
            db.execute(
                "INSERT INTO projects(id,name,repository,runtime,status) VALUES (%s,%s,'test/fixture','node','created')",
                (project, tenant),
            )
            db.execute("INSERT INTO command_center_project_tenant VALUES (%s,%s)", (project, tenant))
        tenants = {r["name"]: r["id"] for r in db.execute("SELECT id,name FROM command_center_tenant")}
        db.execute(
            "INSERT INTO smd_deploy_task (id,project_id,instance_id,status,target_tenant,project_manifest) VALUES (%s,%s,%s,'processing',%s,%s)",
            (
                job,
                first,
                instance,
                names[0],
                Jsonb(
                    [
                        {"id": str(p), "name": n, "repository": "test/fixture", "revision": "a" * 40}
                        for p, n in zip(projects[:2], names[:2])
                    ]
                ),
            ),
        )
        db.execute(
            "INSERT INTO smd_deploy_receipt(task_id,instance_id,public_key,connections) VALUES (%s,%s,%s,%s)",
            (job, instance, bytes(32), Jsonb(sample)),
        )
    client = TestClient(app, base_url=ORIGIN)
    cookie = {"__Host-command": issue_session(bytes.fromhex(os.environ["SESSION_KEY"]))}
    headers = {"Origin": ORIGIN}
    route = f"{ADMIN}/{job}/tenant"

    def set_tenant(tenant, version):
        return client.put(
            route,
            json={"tenant_id": str(tenant) if tenant else None, "version": version},
            cookies=cookie,
            headers=headers,
        )

    def access(principal, destination=None):
        token = project_token(bytes.fromhex(os.environ["MEMORY_ACCESS_KEY"]), principal)
        return client.post(
            f"/workspace-memory/{destination or principal}/deployment-access",
            headers={"Authorization": "Bearer " + token},
            json={"deployment_id": str(job)},
        )

    export_url = f"/admin/projects/{first}/smd-export"
    assert client.get(export_url).status_code == 401
    guest_download = client.get(export_url, headers={"Sec-Fetch-Mode": "navigate"}, follow_redirects=False)
    assert guest_download.status_code == 303
    assert guest_download.headers["location"] == f"{ORIGIN}/?view=images&project={first}"
    assert client.get("/admin/docker-export").status_code == 401
    assert client.get(ADMIN).status_code == 401
    assert client.put(route, json={"tenant_id": None, "version": 0}, headers=headers).status_code == 401
    assert client.put(route, json={"tenant_id": None, "version": 0}, cookies=cookie).status_code == 403
    inv = client.get(ADMIN, cookies=cookie).json()
    row = next(r for r in inv["deployments"] if r["id"] == str(job))
    default, = (t for t in inv["tenants"] if t["is_default"])
    assert row["tenant_id"] == default["id"] and row["connections"] is None
    assert set_tenant(None, 0).json()["version"] == 0
    assert set(row["project_manifest"][0]) == {"id", "name", "repository", "revision"}
    assert access(first).status_code == 404
    assert set_tenant(uuid4(), 0).status_code == 422
    assert set_tenant(tenants[names[0]], 0).status_code == 200
    assert access(first).status_code == 404
    with database() as db:
        db.execute("UPDATE smd_deploy_task SET status='deployed',updated_at=now() WHERE id=%s", (job,))
        db.execute(
            "UPDATE smd_deploy_receipt SET verified_at=now(),signature=%s WHERE task_id=%s", (bytes(64), job)
        )
    assert [app["project_id"] for app in access(first).json()["apps"]] == [str(first)]
    assert access(second).status_code == 404 and access(outsider).status_code == 404
    with database() as db:
        for migration in ("056_compute_console.sql", "058_admin_deployment_tenant.sql"):
            db.execute((Path("/app") / migration).read_text())
        assert db.execute("SELECT tenant_id FROM smd_deployment_assignment WHERE task_id=%s", (job,)).fetchone()["tenant_id"] == tenants[names[0]]
    assert access(outsider, first).status_code == 401
    assert set_tenant(tenants[names[1]], 1).status_code == 200
    assert access(first).status_code == 404 and access(second).status_code == 200
    assert set_tenant(tenants[names[0]], 1).status_code == 409
    with ThreadPoolExecutor(max_workers=2) as pool:
        updates = list(pool.map(lambda tenant: set_tenant(tenant, 2).status_code, [None, tenants[names[0]]]))
    assert sorted(updates) == [200, 409]
    assert set_tenant(None, 3).status_code == 200
    assert access(first).status_code == 404 and access(second).status_code == 404
    with database() as db:
        db.execute(Path("/app/056_compute_console.sql").read_text())
        assert (
            db.execute("SELECT tenant_id FROM smd_deployment_assignment WHERE task_id=%s", (job,)).fetchone()[
                "tenant_id"
            ]
            == UUID(default["id"])
        )
        assert (
            db.execute("SELECT status FROM smd_deploy_task WHERE id=%s", (job,)).fetchone()["status"]
            == "deployed"
        )
    history = client.get(f"{ADMIN}/{job}/history", cookies=cookie).json()
    assert history["proof"] and any(e["kind"] == "receipt" for e in history["events"])
    changes = [e for e in history["events"] if e["kind"] == "assignment"]
    assert changes[0]["next_tenant"] == default["name"]
    assert changes[1]["previous_tenant"] == default["name"] and changes[1]["next_tenant"] == names[0]
    assert changes[2]["previous_tenant"] == names[0] and changes[2]["next_tenant"] == names[1]
    assert changes[-1]["next_tenant"] == default["name"]
    print(
        "Passed real PostgreSQL and HTTP checks for assignment, lost-update conflict, default restoration, audit, signed-record gating, project isolation and owner / origin checks."
    )


if __name__ == "__main__":
    run()
