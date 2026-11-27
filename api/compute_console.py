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
        a.version AS assignment_version,a.portal_account,
        (SELECT count(*) FROM smd_tenant_default_secret x WHERE x.tenant_id=a.tenant_id) AS tenant_default_count,
        CASE WHEN c.id IS DISTINCT FROM smd_admin_tenant_id() THEN 'tenant'
             WHEN t.status!='deployed' OR t.reconciled_at IS NOT NULL THEN 'inactive'
             WHEN i.owner_version=a.version AND i.instance_id=t.instance_id THEN 'owner_active'
             ELSE 'owner_pending' END AS configuration_state,
        (SELECT COALESCE(jsonb_object_agg(env.project_id::text,env.domains),'{}'::jsonb) FROM
            (SELECT d.project_id,jsonb_object_agg(d.environment,d.hostname) AS domains
             FROM smd_custom_preview d WHERE d.task_id=t.id AND d.assignment_version=a.version
             GROUP BY d.project_id) env) AS custom_domains,
        (a.portal_ready_instance=t.instance_id OR r.connections->>'portal_access'='owner-pkce-v1') IS TRUE AS portal_ready,c.id AS tenant_id,c.name AS tenant_name,
        r.enrolled_at,r.verified_at,
        CASE WHEN r.verified_at IS NOT NULL THEN r.connections END AS connections
 FROM smd_deploy_task t JOIN projects p ON p.id=t.project_id
 JOIN smd_deployment_assignment a ON a.task_id=t.id
 LEFT JOIN smd_install_identity i ON i.task_id=t.id
 LEFT JOIN command_center_tenant c ON c.id=a.tenant_id
 LEFT JOIN smd_deploy_receipt r ON r.task_id=t.id AND r.instance_id=t.instance_id
"""


def tenant_catalog(db):
    return db.execute("""SELECT id,name,id=smd_admin_tenant_id() AS is_default FROM command_center_tenant
                         ORDER BY is_default DESC,lower(name),id""").fetchall()


@router.get("/admin/tenants")
def registered_tenants():
    with database() as db:
        return tenant_catalog(db)


@router.get("/admin/compute/deployments")
def inventory():
    with database() as db:
        rows = db.execute(INVENTORY + " ORDER BY t.queued_at DESC,t.id").fetchall()
        tenants = tenant_catalog(db)
    # Build metadata belongs to the installer. Only publish the fields displayed by the console.
    for row in rows:
        row["project_manifest"] = [
            {key: project[key] for key in ("id", "name", "repository", "revision", "hostname", "preview_hostname", "tenant_setup") if key in project}
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
        # Older clients clear the field to return a deployment to its default owner.
        tenant = db.execute(
            "SELECT id FROM command_center_tenant WHERE id=coalesce(%s,smd_admin_tenant_id())", (body.tenant_id,)
        ).fetchone()
        if not tenant:
            raise HTTPException(422, "Choose a registered tenant.")
        if tenant["id"] == current["tenant_id"]:
            return current
        live=db.execute("""SELECT t.status,t.instance_id,t.reconciled_at,r.connections->>'portal_url' AS portal,
                          i.public_key,t.dns_zone FROM smd_deploy_task t LEFT JOIN smd_deploy_receipt r
                          ON r.task_id=t.id AND r.instance_id=t.instance_id AND r.verified_at IS NOT NULL
                          LEFT JOIN smd_install_identity i ON i.task_id=t.id AND i.instance_id=t.instance_id
                          WHERE t.id=%s""",(task_id,)).fetchone()
        staged=None
        if live["status"]=="deployed" and not live["reconciled_at"]:
            expected=f"https://smd-{task_id.hex}.{live['dns_zone']}"
            if not live["public_key"] or (live["portal"] or "").rstrip("/")!=expected:
                raise HTTPException(409,"Enroll this installation before handing it to a tenant.")
            from smd_tenant_access import stage_generation
            staged=stage_generation(live,task_id,current["version"]+1)
        updated = db.execute(
            """UPDATE smd_deployment_assignment SET tenant_id=%s,version=version+1
                             WHERE task_id=%s RETURNING tenant_id,version""",
            (tenant["id"], task_id),
        ).fetchone()
        if staged:
            db.execute("""UPDATE smd_install_identity SET public_key=%s,
                 owner_version=CASE WHEN %s=smd_admin_tenant_id() THEN %s ELSE NULL END WHERE task_id=%s""",
                 (staged,tenant["id"],updated["version"],task_id))
            db.execute("DELETE FROM smd_portal_launch WHERE task_id=%s",(task_id,))
            db.execute("""INSERT INTO smd_deployment_event(task_id,kind,label) VALUES
                  (%s,'configuration','Recreated tenant portal login and rebound assigned default secrets')""",(task_id,))
        db.execute(
            """INSERT INTO smd_deployment_event(task_id,kind,label,previous_tenant,next_tenant)
                      VALUES (%s,'assignment','Owner changed tenant assignment',%s,%s)""",
            (task_id, current["tenant_id"], tenant["id"]),
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
