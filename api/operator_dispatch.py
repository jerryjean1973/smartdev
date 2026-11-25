"""Project-scoped, outbound-only dispatch of read-only portal inspections."""
import hmac
from hashlib import sha256
import os
import time
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from psycopg.types.json import Jsonb

from store import database

router = APIRouter()


def configured_project():
    try:
        return UUID(os.getenv("SMARTDEV_OPERATOR_PROJECT_ID", ""))
    except ValueError:
        return None


def machine_project(authorization):
    project = configured_project()
    expected = os.getenv("SMARTDEV_OPERATOR_TOKEN_SHA256", "")
    scheme, _, token = authorization.partition(" ")
    if (not project or scheme != "Bearer" or not 43 <= len(token) <= 256
            or not hmac.compare_digest(sha256(token.encode()).hexdigest(), expected)):
        raise HTTPException(401, "Operator authorization required.")
    return project


def inspect_portal(project, grant):
    if project != configured_project() or not os.getenv("SMARTDEV_OPERATOR_TOKEN_SHA256"):
        raise LookupError("Project not available")
    request_id = uuid4()
    with database() as db:
        db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 40261))", (str(project),))
        capacity = db.execute("""
            SELECT count(*) AS pending FROM operator_inspection
            WHERE project_id=%s AND expires_at>now() AND receipt IS NULL
        """, (project,)).fetchone()
        if capacity["pending"] >= 4:
            return {"status": "busy"}
        inserted = db.execute("""
            INSERT INTO operator_inspection (id, project_id, grant_id)
            SELECT %s, p.id, g.id FROM projects p
            JOIN external_mcp_grant g ON g.id=%s
            JOIN smartdev_owner o ON o.id=g.owner_id AND o.singleton
            WHERE p.id=%s AND NOT p.archived AND p.id=ANY(g.project_ids)
              AND 'read'=ANY(g.scopes) AND g.revoked_at IS NULL AND g.expires_at>now()
              AND NOT EXISTS (SELECT 1 FROM acceptance_fixture f WHERE f.project_id=p.id)
            RETURNING id
        """, (request_id, grant, project)).fetchone()
    if not inserted:
        raise LookupError("Project not available")
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        with database() as db:
            row = db.execute("SELECT receipt FROM operator_inspection WHERE id=%s",
                             (request_id,)).fetchone()
        if row and row["receipt"]:
            return {"request_id": str(request_id), "receipt": row["receipt"]}
        time.sleep(0.5)
    return {"request_id": str(request_id), "status": "unconfirmed"}


@router.post("/operator-machine/poll")
def poll(authorization: str = Header(default="")):
    project = machine_project(authorization)
    claim = uuid4()
    with database() as db:
        row = db.execute("""
            WITH pending AS (
                SELECT r.id FROM operator_inspection r
                JOIN projects p ON p.id=r.project_id
                JOIN external_mcp_grant g ON g.id=r.grant_id
                WHERE r.project_id=%s AND NOT p.archived AND r.expires_at>now()
                 AND r.receipt IS NULL AND (r.claimed_until IS NULL OR r.claimed_until<now())
                 AND g.revoked_at IS NULL AND g.expires_at>now()
                 AND r.project_id=ANY(g.project_ids) AND 'read'=ANY(g.scopes)
                ORDER BY r.requested_at LIMIT 1 FOR UPDATE OF r SKIP LOCKED
            )
            UPDATE operator_inspection r SET claim_id=%s, claimed_until=now()+interval '10 seconds'
            FROM pending WHERE r.id=pending.id RETURNING r.id
        """, (project, claim)).fetchone()
    return {"request_id": row["id"], "claim_id": claim} if row else {}


class ContainerObservation(BaseModel, extra="forbid"):
    service: Literal["portal"]
    container_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    image_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    state: Literal["created", "running", "paused", "restarting", "removing", "exited", "dead"]
    health: Literal["starting", "healthy", "unhealthy"] | None = None


class InspectionResult(BaseModel, extra="forbid"):
    request_id: UUID
    claim_id: UUID
    receipt_id: UUID
    status: Literal["observed", "denied", "unconfirmed"]
    result: ContainerObservation | None = None


@router.post("/operator-machine/result")
def complete(body: InspectionResult, authorization: str = Header(default="")):
    project = machine_project(authorization)
    if (body.status == "observed") != (body.result is not None):
        raise HTTPException(422, "Invalid observation.")
    receipt = body.model_dump(mode="json", exclude={"request_id", "claim_id"})
    with database() as db:
        row = db.execute("""
            UPDATE operator_inspection SET receipt=%s WHERE id=%s AND project_id=%s
            AND claim_id=%s AND claimed_until>now() AND expires_at>now() AND receipt IS NULL
            RETURNING id
        """, (Jsonb(receipt), body.request_id, project, body.claim_id)).fetchone()
    if not row:
        raise HTTPException(409, "Inspection claim unavailable.")
    return {"ok": True}
