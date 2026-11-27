"""Owner Compute inventory and assignment-bound workspace connection discovery."""

from uuid import UUID
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from store import database

router = APIRouter()
INVENTORY = """
 SELECT t.id,t.project_id,p.name AS project_name,t.project_manifest,t.status,t.instance_id,
        t.release_revision,t.queued_at,t.updated_at,t.reconciled_at,t.region,t.plan,t.dns_zone,
        t.deployment_hostname,t.note,t.installer_sha256,t.observed_revision,
        a.version AS assignment_version,c.id AS tenant_id,c.name AS tenant_name,
        r.enrolled_at,r.verified_at,
        CASE WHEN r.verified_at IS NOT NULL THEN r.connections END AS connections
 FROM smd_deploy_task t JOIN projects p ON p.id=t.project_id
 JOIN smd_deployment_assignment a ON a.task_id=t.id
 LEFT JOIN command_center_tenant c ON c.id=a.tenant_id
 LEFT JOIN smd_deploy_receipt r ON r.task_id=t.id AND r.instance_id=t.instance_id
"""


@router.get("/admin/compute/deployments")
def inventory():
    with database() as db:
        rows = db.execute(INVENTORY + " ORDER BY t.queued_at DESC,t.id").fetchall()
        tenants = db.execute("SELECT id,name FROM command_center_tenant ORDER BY lower(name),id").fetchall()
    # Build metadata belongs to the installer. Only publish the fields displayed by the console.
    for row in rows:
        row["project_manifest"] = [
            {key: project[key] for key in ("id", "name", "repository", "revision") if key in project}
            for project in row["project_manifest"] or []
        ]
    return {"deployments": rows, "tenants": tenants}


@router.get("/admin/compute/deployments/{task_id}/history")
def history(task_id: UUID):
    with database() as db:
        record = db.execute(INVENTORY + " WHERE t.id=%s", (task_id,)).fetchone()
        if not record:
            raise HTTPException(404, "Deployment unavailable.")
        events = db.execute(
            """SELECT e.at,e.kind,e.label,p.name AS previous_tenant,n.name AS next_tenant
                             FROM smd_deployment_event e LEFT JOIN command_center_tenant p ON p.id=e.previous_tenant
                             LEFT JOIN command_center_tenant n ON n.id=e.next_tenant WHERE e.task_id=%s ORDER BY e.at,e.id""",
            (task_id,),
        ).fetchall()
        proof = db.execute(
            """SELECT encode(public_key,'hex') AS public_key,encode(signature,'base64') AS signature
                            FROM smd_deploy_receipt WHERE task_id=%s AND verified_at IS NOT NULL
                            AND instance_id=%s""",
            (task_id, record["instance_id"]),
        ).fetchone()
    # Older releases only retained the last state. Expose its actual observation alongside the new ledger.
    events.append({"at": record["queued_at"], "kind": "request", "label": "Deployment requested"})
    if record["enrolled_at"]:
        events.append(
            {
                "at": record["enrolled_at"],
                "kind": "enrollment",
                "label": "Instance enrolled for signed receipts",
            }
        )
    if record["verified_at"]:
        events.append(
            {"at": record["verified_at"], "kind": "receipt", "label": "Signed installation receipt verified"}
        )
    updated = record["reconciled_at"] or record["updated_at"]
    if updated != record["queued_at"] and not any(
        e["kind"] == "state" and e["at"] == updated for e in events
    ):
        events.append(
            {
                "at": updated,
                "kind": "state",
                "label": (
                    "Reconciled"
                    if record["reconciled_at"]
                    else record["note"] or record["status"].replace("_", " ")
                ),
            }
        )
    return {"events": sorted(events, key=lambda e: e["at"]), "proof": proof}


class Assignment(BaseModel, extra="forbid"):
    tenant_id: UUID | None
    version: int = Field(ge=0)


@router.put("/admin/compute/deployments/{task_id}/tenant")
def assign(task_id: UUID, body: Assignment):
    with database() as db:
        current = db.execute(
            "SELECT tenant_id,version FROM smd_deployment_assignment WHERE task_id=%s FOR UPDATE", (task_id,)
        ).fetchone()
        if not current:
            raise HTTPException(404, "Deployment unavailable.")
        if current["version"] != body.version:
            raise HTTPException(409, "The assignment changed. Refresh and review it again.")
        if body.tenant_id == current["tenant_id"]:
            return current
        if (
            body.tenant_id
            and not db.execute(
                "SELECT 1 FROM command_center_tenant WHERE id=%s", (body.tenant_id,)
            ).fetchone()
        ):
            raise HTTPException(422, "Choose a registered tenant.")
        updated = db.execute(
            """UPDATE smd_deployment_assignment SET tenant_id=%s,version=version+1
                             WHERE task_id=%s RETURNING tenant_id,version""",
            (body.tenant_id, task_id),
        ).fetchone()
        db.execute(
            """INSERT INTO smd_deployment_event(task_id,kind,label,previous_tenant,next_tenant)
                      VALUES (%s,'assignment','Owner changed tenant assignment',%s,%s)""",
            (task_id, current["tenant_id"], body.tenant_id),
        )
    return updated


class WorkspaceDeployment(BaseModel, extra="forbid"):
    deployment_id: UUID


@router.post("/workspace-memory/{project_id}/deployment-access")
def tenant_access(project_id: UUID, body: WorkspaceDeployment):
    with database() as db:
        record = db.execute(
            """SELECT c.name AS tenant,r.connections FROM smd_deploy_task t
            JOIN smd_deployment_assignment a ON a.task_id=t.id
            JOIN command_center_tenant c ON c.id=a.tenant_id
            JOIN command_center_project_tenant p ON p.project_id=%s AND p.tenant_name=c.name
            JOIN projects live ON live.id=p.project_id AND NOT live.archived
            JOIN smd_deploy_receipt r ON r.task_id=t.id AND r.instance_id=t.instance_id AND r.verified_at IS NOT NULL
            WHERE t.id=%s AND t.status='deployed' AND t.reconciled_at IS NULL AND
              (t.project_id=p.project_id OR EXISTS (SELECT 1 FROM jsonb_array_elements(t.project_manifest) m WHERE m->>'id'=p.project_id::text))""",
            (project_id, body.deployment_id),
        ).fetchone()
    if not record or not record["connections"]:
        raise HTTPException(404, "Deployment access unavailable.")
    links = record["connections"]
    return {
        "tenant": record["tenant"],
        "portal_url": links["portal_url"],
        "coder_url": links["coder_url"],
        "apps": [item for item in links["apps"] if item["project_id"] == str(project_id)],
    }
