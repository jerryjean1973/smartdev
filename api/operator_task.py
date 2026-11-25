"""One-shot host operations over the existing operator machine connection."""
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from secrets import compare_digest
from typing import Literal
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import Header, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import AwareDatetime, BaseModel, Field

from operator_dispatch import configured_project, machine_project, router
from store import database


class Binding(BaseModel, extra="forbid"):
    approval_id: UUID
    task_id: UUID
    execution_id: UUID
    environment: Literal["dev"]
    operation: Literal["codex.version"]


class SignedGrant(BaseModel, extra="forbid"):
    id: UUID
    project_id: UUID
    task_id: UUID
    execution_id: UUID
    environment: Literal["dev"]
    operation: Literal["codex.version"]
    token_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expires_at: AwareDatetime
    mig_receipt: UUID
    mig_signature: str = Field(pattern=r"^[0-9a-f]{128}$")


class Dispatch(Binding):
    ticket: str = Field(min_length=43, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    grant: SignedGrant | None = None


def mig_payload(row):
    return json.dumps([
        "smartdev-operator-task-v1", str(row["id"]), str(row["project_id"]),
        str(row["task_id"]), str(row["execution_id"]), row["environment"],
        row["operation"], str(row["mig_receipt"]), row["token_sha256"],
        row["expires_at"].astimezone(timezone.utc).isoformat(timespec="microseconds"),
    ], separators=(",", ":")).encode()


def authorized_by_mig(row):
    try:
        key = Ed25519PublicKey.from_public_bytes(
            bytes.fromhex(os.getenv("SMARTDEV_OPERATOR_TASK_MIG_PUBLIC_KEY", "")))
        key.verify(bytes.fromhex(row["mig_signature"] or ""), mig_payload(row))
    except (InvalidSignature, ValueError):
        return False
    return True


def event(db, row, phase, outcome):
    receipt = uuid4()
    db.execute("""
        INSERT INTO operator_task_event
            (id,approval_id,project_id,task_id,execution_id,phase,outcome)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
    """, (receipt, row["id"], row["project_id"], row["task_id"],
          row["execution_id"], phase, outcome))
    return receipt


def load(db, project, approval):
    return db.execute("""
        SELECT a.*, clock_timestamp() > a.updated_at + interval '8 seconds' AS claim_timed_out,
            LEAST(a.expires_at, r.lease_until) AS deadline,
            CASE WHEN a.revoked_at IS NOT NULL OR p.archived OR r.finished_at IS NOT NULL
                       OR r.version<>c.version THEN 'revoked'
                 WHEN LEAST(a.expires_at,r.lease_until)<=clock_timestamp() THEN 'expired'
            END AS invalid
        FROM operator_task a
        JOIN objective_run r ON r.id=a.execution_id AND r.objective_id=a.task_id
                           AND r.project_id=a.project_id
        JOIN objective_contract c ON c.id=a.task_id AND c.project_id=a.project_id
        JOIN projects p ON p.id=a.project_id
        WHERE a.project_id=%s AND a.id=%s
        FOR UPDATE OF a,r,c,p
    """, (project, approval)).fetchone()


def finish(db, row, outcome, host_receipt=None):
    receipt = event(db, row, "completion", outcome)
    db.execute("""
        UPDATE operator_task SET completion_id=%s,host_receipt_id=%s,
            outcome=%s,updated_at=clock_timestamp() WHERE id=%s
    """, (receipt, host_receipt, outcome, row["id"]))
    return {"completion_id": receipt, "outcome": outcome, "host_receipt_id": host_receipt}


def enroll(db, project, body):
    grant = body.grant
    row = grant.model_dump()
    if (grant.project_id != project or grant.id != body.approval_id
            or any(row[key] != getattr(body, key) for key in
                   ("task_id", "execution_id", "environment", "operation"))
            or not compare_digest(grant.token_sha256, sha256(body.ticket.encode()).hexdigest())
            or grant.expires_at <= datetime.now(timezone.utc)
            or not authorized_by_mig(row)):
        return
    db.execute("""
        INSERT INTO operator_task (id,project_id,task_id,execution_id,environment,
            operation,token_sha256,expires_at,mig_receipt,mig_signature)
        SELECT %(id)s,%(project_id)s,%(task_id)s,%(execution_id)s,%(environment)s,
            %(operation)s,%(token_sha256)s,%(expires_at)s,%(mig_receipt)s,%(mig_signature)s
        FROM objective_run r JOIN objective_contract c ON c.id=r.objective_id
        JOIN projects p ON p.id=r.project_id
        WHERE r.id=%(execution_id)s AND c.id=%(task_id)s AND p.id=%(project_id)s
          AND c.project_id=p.id AND NOT p.archived AND r.version=c.version
          AND r.finished_at IS NULL AND r.lease_until>clock_timestamp()
        ON CONFLICT DO NOTHING
    """, row)


@router.post("/workspace-memory/{project}/operator-task")
def dispatch(project: UUID, body: Dispatch):
    with database() as db:
        if project == configured_project() and body.grant is not None:
            enroll(db, project, body)
        row = load(db, project, body.approval_id) if project == configured_project() else None
        if (not row or any(row[key] != getattr(body, key) for key in
                           ("task_id", "execution_id", "operation", "environment"))
                or not compare_digest(row["token_sha256"], sha256(body.ticket.encode()).hexdigest())):
            receipt = event(db, {"id": body.approval_id, "project_id": project,
                                 "task_id": body.task_id, "execution_id": body.execution_id},
                            "dispatch", "denied")
            result = {"outcome": "denied", "receipt_id": receipt}
        elif not authorized_by_mig(row):
            result = {"outcome": "denied", "receipt_id": event(db, row, "dispatch", "denied")}
        elif row["completion_id"]:
            return {key: row[key] for key in
                    ("dispatch_id", "claim_id", "completion_id", "host_receipt_id", "outcome")}
        elif row["invalid"]:
            result = finish(db, row, row["invalid"])
        else:
            receipt = row["dispatch_id"]
            if receipt is None:
                receipt = event(db, row, "dispatch", "queued")
                db.execute("UPDATE operator_task SET dispatch_id=%s WHERE id=%s", (receipt, row["id"]))
            return {"dispatch_id": receipt, "outcome": "queued"}
    raise HTTPException(403, jsonable_encoder(result))


@router.post("/operator-machine/task-poll")
def poll_task(authorization: str = Header(default="")):
    project = machine_project(authorization)
    with database() as db:
        candidates = db.execute("""
            SELECT a.id FROM operator_task a
            WHERE a.project_id=%s AND a.dispatch_id IS NOT NULL AND a.completion_id IS NULL
              AND (a.claim_id IS NULL OR clock_timestamp() > a.updated_at + interval '8 seconds')
            ORDER BY a.updated_at LIMIT 16
        """, (project,)).fetchall()
        for candidate in candidates:
            row = load(db, project, candidate["id"])
            if not row or row["completion_id"]:
                continue
            if row["claim_id"]:
                if row["claim_timed_out"]:
                    finish(db, row, "unconfirmed")
                continue
            if not authorized_by_mig(row):
                finish(db, row, "denied")
                continue
            if row["invalid"]:
                finish(db, row, row["invalid"])
                continue
            claim = event(db, row, "execution", "claimed")
            db.execute("UPDATE operator_task SET claim_id=%s,updated_at=clock_timestamp() WHERE id=%s", (claim, row["id"]))
            return {"approval_id": row["id"], "claim_id": claim, "project_id": project,
                    **{key: row[key] for key in ("task_id", "execution_id", "environment", "operation")},
                    "expires_at": row["deadline"]}
    return {}


class Completion(BaseModel, extra="forbid"):
    approval_id: UUID
    claim_id: UUID
    host_receipt_id: UUID
    outcome: Literal["observed", "denied", "expired", "unconfirmed"]


@router.post("/operator-machine/task-result")
def complete_task(body: Completion, authorization: str = Header(default="")):
    project = machine_project(authorization)
    with database() as db:
        row = load(db, project, body.approval_id)
        if not row or row["claim_id"] != body.claim_id:
            raise HTTPException(409, "Execution claim unavailable.")
        if row["completion_id"]:
            if row["host_receipt_id"] == body.host_receipt_id and row["outcome"] == body.outcome:
                return {"completion_id": row["completion_id"]}
            raise HTTPException(409, "Execution already closed.")
        return finish(db, row, body.outcome, body.host_receipt_id)
