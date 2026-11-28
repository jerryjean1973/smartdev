"""Tenant-scoped MCP requests delivered to an enrolled deployment workspace."""
import hashlib
import json
import re
from uuid import UUID, uuid4, uuid5

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from psycopg.types.json import Jsonb

from store import database, notify
from task_titles import valid_title
from work_messages import visible_text

router = APIRouter()


class EnvironmentBindingUnavailable(LookupError):
    """A caller-visible target binding or permission is missing."""


def registered_environment(row):
    hostname = (row.get("deployment_hostname") or "").lower()
    zone = (row.get("dns_zone") or "").lower()
    if ".qa." in hostname or zone.startswith("qa."):
        return "QA"
    if ".dev." in hostname or zone.startswith("dev."):
        return "DEV"
    return "DEPLOYED"


def tenant_scope(db, grant):
    rows = db.execute("""
        SELECT DISTINCT CASE WHEN binding.project_id IS NULL THEN smd_admin_tenant_id()
                             ELSE c.id END AS tenant_id
        FROM unnest(%s::uuid[]) selected(project_id)
        JOIN projects p ON p.id=selected.project_id AND NOT p.archived
        LEFT JOIN command_center_project_tenant binding ON binding.project_id=p.id
        LEFT JOIN command_center_tenant c ON c.name=binding.tenant_name
    """, (list(grant["project_ids"]),)).fetchall()
    if len(rows) != 1 or not rows[0]["tenant_id"]:
        raise LookupError("Choose projects from one current tenant before requesting deployment work.")
    return rows[0]["tenant_id"]


ENVIRONMENTS = """
 SELECT t.id AS deployment_id,t.project_id AS deployment_project_id,t.deployment_hostname,
        t.dns_zone,t.instance_id,t.project_manifest,t.status,t.reconciled_at,
        a.tenant_id,a.version AS assignment_version,r.connections,r.verified_at
 FROM smd_deploy_task t
 JOIN smd_deployment_assignment a ON a.task_id=t.id
 JOIN smd_deploy_receipt r ON r.task_id=t.id AND r.instance_id=t.instance_id
 WHERE a.tenant_id=%s AND t.status='deployed' AND t.reconciled_at IS NULL
   AND r.verified_at IS NOT NULL
 ORDER BY t.queued_at,t.id
"""

TARGET_ENVIRONMENT = """
 SELECT t.id AS deployment_id,t.project_id AS deployment_project_id,t.deployment_hostname,
        t.dns_zone,t.instance_id,t.project_manifest,t.status,t.reconciled_at,
        a.tenant_id,a.version AS assignment_version,r.connections,r.verified_at
 FROM smd_deploy_task t
 LEFT JOIN smd_deployment_assignment a ON a.task_id=t.id
 LEFT JOIN smd_deploy_receipt r ON r.task_id=t.id AND r.instance_id=t.instance_id
 WHERE t.id=%s
"""


def included_project(row, project):
    return project == row["deployment_project_id"] or any(
        item.get("id") == str(project) for item in row["project_manifest"] or []
    )


def list_environments(grant):
    allowed = {UUID(str(value)) for value in grant["project_ids"]}
    with database() as db:
        tenant = tenant_scope(db, grant)
        rows = db.execute(ENVIRONMENTS, (tenant,)).fetchall()
    result = []
    for row in rows:
        projects = sorted(str(project) for project in allowed if included_project(row, project))
        if not projects:
            continue
        links = row["connections"] or {}
        result.append({
            "deployment_id": str(row["deployment_id"]),
            "environment": registered_environment(row),
            "hostname": row["deployment_hostname"],
            "project_ids": projects,
            "portal_url": links.get("portal_url"),
            "coder_url": links.get("coder_url"),
            "verified_at": row["verified_at"].isoformat(),
        })
    return {"tenant_id": str(tenant), "environments": result}


def request_fix(grant, deployment_id, project_id, environment, instructions,
                client_request_id, title=None):
    deployment = UUID(str(deployment_id))
    project = UUID(str(project_id))
    client_request = UUID(str(client_request_id))
    environment = str(environment).upper()
    if project not in {UUID(str(value)) for value in grant["project_ids"]}:
        raise EnvironmentBindingUnavailable(
            "Missing MCP project permission: project_id is not present in the current grant."
        )
    if not isinstance(instructions, str) or not instructions.strip() or len(instructions) > 27000:
        raise ValueError("Invalid instructions")
    label = (title or f"{environment} environment fix").strip()
    if len(label) > 100 or not valid_title(label):
        raise ValueError("Invalid title")
    instruction = visible_text(instructions)
    binding = json.dumps([str(deployment), str(project), environment, label, instruction],
                         ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(binding.encode()).hexdigest()
    request_id = uuid5(UUID(str(grant["owner_id"])), "smartdev-environment-fix:" + str(client_request))
    with database() as db:
        tenant = tenant_scope(db, grant)
        row = db.execute(TARGET_ENVIRONMENT, (deployment,)).fetchone()
        if not row:
            raise EnvironmentBindingUnavailable(
                "Missing deployment binding: deployment_id does not identify a deployed environment."
            )
        if row["status"] != "deployed" or row["reconciled_at"] is not None:
            raise EnvironmentBindingUnavailable(
                "Missing active deployment permission: the target is not an active deployment."
            )
        if row["tenant_id"] != tenant:
            raise EnvironmentBindingUnavailable(
                "Missing tenant binding: the deployment is not assigned to the current tenant."
            )
        if row["verified_at"] is None:
            raise EnvironmentBindingUnavailable(
                "Missing receipt binding: the deployment instance has no verified enrollment receipt."
            )
        if not included_project(row, project):
            raise EnvironmentBindingUnavailable(
                "Missing deployment project binding: project_id is not deployed in this environment."
            )
        registered = registered_environment(row)
        if registered != environment:
            raise EnvironmentBindingUnavailable(
                f"Environment selection mismatch: deployment_id is registered as {registered}, not {environment}."
            )
        saved = db.execute("""
            SELECT id,payload_digest,status,receipt,failure_reason FROM smd_environment_fix_request
            WHERE grant_id=%s AND client_request_id=%s FOR UPDATE
        """, (grant["id"], client_request)).fetchone()
        if saved and saved["payload_digest"] != digest:
            raise ValueError("Request ID belongs to a different environment fix.")
        if not saved:
            saved = db.execute("""
                INSERT INTO smd_environment_fix_request
                    (id,grant_id,client_request_id,tenant_id,deployment_id,project_id,
                     environment,title,instruction,payload_digest)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING id,payload_digest,status,receipt,failure_reason
            """, (request_id, grant["id"], client_request, tenant, deployment, project,
                  environment, label, instruction, digest)).fetchone()
    notify()
    return {"request_id": str(saved["id"]), "deployment_id": str(deployment),
            "project_id": str(project), "environment": environment,
            "status": saved["status"], "receipt": saved["receipt"],
            "failure_reason": saved["failure_reason"]}


def get_fix(grant, request_id):
    request = UUID(str(request_id))
    with database() as db:
        row = db.execute("""
            SELECT id,deployment_id,project_id,environment,status,receipt,failure_reason,
                   created_at,updated_at FROM smd_environment_fix_request
            WHERE id=%s AND grant_id=%s
        """, (request, grant["id"])).fetchone()
    if not row:
        raise LookupError("Environment fix request not available.")
    return {**row, "id": str(row["id"]), "deployment_id": str(row["deployment_id"]),
            "project_id": str(row["project_id"]),
            "created_at": row["created_at"].isoformat(), "updated_at": row["updated_at"].isoformat()}


class Completion(BaseModel, extra="forbid"):
    request_id: UUID
    claim_id: UUID
    status: str = Field(pattern=r"^(delivered|denied|unconfirmed)$")
    receipt: dict | None = None
    failure_reason: str | None = Field(default=None, max_length=1000)


@router.post("/admin/smd-environment-fixes/claim")
def claim():
    claim_id = uuid4()
    with database() as db:
        db.execute("""
            UPDATE smd_environment_fix_request q SET status='denied',updated_at=now(),
              failure_reason='The MCP authorization or tenant assignment ended before delivery.'
            FROM external_mcp_grant g,smd_deployment_assignment a
            WHERE q.status IN ('queued','claimed') AND q.grant_id=g.id AND a.task_id=q.deployment_id
              AND (g.revoked_at IS NOT NULL OR g.expires_at<=now()
                   OR q.project_id<>ALL(g.project_ids) OR a.tenant_id<>q.tenant_id)
        """)
        row = db.execute("""
            WITH candidate AS (
              SELECT q.id FROM smd_environment_fix_request q
              JOIN external_mcp_grant g ON g.id=q.grant_id AND g.revoked_at IS NULL AND g.expires_at>now()
              JOIN smd_deploy_task t ON t.id=q.deployment_id AND t.status='deployed' AND t.reconciled_at IS NULL
              JOIN smd_deployment_assignment a ON a.task_id=t.id AND a.tenant_id=q.tenant_id
              JOIN smd_deploy_receipt r ON r.task_id=t.id AND r.instance_id=t.instance_id AND r.verified_at IS NOT NULL
              WHERE (q.status='queued' OR (q.status='claimed' AND q.claimed_at<now()-interval '5 minutes'))
                AND q.project_id=ANY(g.project_ids) AND 'write'=ANY(g.scopes)
              ORDER BY q.created_at,q.id LIMIT 1 FOR UPDATE OF q SKIP LOCKED
            )
            UPDATE smd_environment_fix_request q SET status='claimed',claim_id=%s,
              claimed_at=now(),updated_at=now() FROM candidate WHERE q.id=candidate.id
            RETURNING q.id AS request_id,q.claim_id,q.deployment_id,q.project_id,q.environment,
              q.title,q.instruction,
              (SELECT t.project_id FROM smd_deploy_task t WHERE t.id=q.deployment_id) AS deployment_project_id,
              (SELECT t.instance_id FROM smd_deploy_task t WHERE t.id=q.deployment_id) AS instance_id,
              (SELECT host(r.expected_ipv4) FROM smd_deploy_receipt r WHERE r.task_id=q.deployment_id) AS ipv4
        """, (claim_id,)).fetchone()
    return row or {}


@router.post("/admin/smd-environment-fixes/result")
def complete(body: Completion):
    if body.status == "delivered" and body.receipt is None:
        raise HTTPException(422, "A delivered request requires its remote receipt.")
    if body.status != "delivered" and not body.failure_reason:
        raise HTTPException(422, "An unsuccessful request requires a reason.")
    with database() as db:
        row = db.execute("""
            UPDATE smd_environment_fix_request SET status=%s,receipt=%s,failure_reason=%s,
              updated_at=now() WHERE id=%s AND claim_id=%s AND status='claimed'
            RETURNING id
        """, (body.status, Jsonb(body.receipt) if body.receipt else None,
              body.failure_reason, body.request_id, body.claim_id)).fetchone()
    if not row:
        raise HTTPException(409, "Environment fix claim unavailable.")
    return {"ok": True}
