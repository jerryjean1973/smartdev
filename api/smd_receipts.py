"""Bind installation receipts to a queue claim, pinned release and Vultr instance."""

import binascii
from base64 import b64decode
from hashlib import sha256
from ipaddress import IPv4Address
import json
import os
from pathlib import Path
from typing import Literal
from secrets import compare_digest
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from psycopg.types.json import Jsonb

from store import database

router = APIRouter()


class Enrollment(BaseModel, extra="forbid"):
    claim_token: str = Field(min_length=1, max_length=128)
    instance_id: UUID
    public_key: str = Field(min_length=44, max_length=44)
    ipv4: IPv4Address | None = None


class AppConnection(BaseModel, extra="forbid"):
    project_id: UUID
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    url: str = Field(max_length=300)
    runtime_status: Literal["awaiting_tenant_configuration", "runtime_unverified"] | None = None


class Connections(BaseModel, extra="forbid"):
    ipv4: IPv4Address
    ssh_user: Literal["root", "ubuntu"]
    portal_url: str = Field(max_length=300)
    coder_url: str = Field(max_length=300)
    apps: list[AppConnection] = Field(max_length=50)


class Receipt(BaseModel, extra="forbid"):
    project_id: UUID
    instance_id: UUID
    revision: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    signature: str = Field(min_length=88, max_length=88)
    connections: Connections | None = None


def connections_match(job, enrolled, connections):
    zone = job.get("dns_zone")
    expected = job.get("project_manifest")
    if not zone:
        return connections is None and not expected
    if (not connections or connections.ipv4 != enrolled["expected_ipv4"]
            or not isinstance(expected, list)):
        return False
    task = job["id"].hex
    if connections.portal_url.removesuffix("/") != f"https://smd-{task}.{zone}":
        return False
    if connections.coder_url.removesuffix("/") != f"https://coder-{task}.{zone}":
        return False
    projects = {UUID(str(project["id"])): project for project in expected}
    if len(connections.apps) != len(projects):
        return False
    observed = set()
    for app in connections.apps:
        host = f"app-{app.project_id.hex[:12]}-{task[:12]}.{zone}"
        if app.url.removesuffix("/") != f"https://{host}":
            return False
        project = projects.get(app.project_id)
        if not project or app.revision != project["revision"]:
            return False
        if project.get("tenant_setup"):
            if app.runtime_status != "awaiting_tenant_configuration":
                return False
        elif app.runtime_status is not None and not (project.get("verify_installation") and app.runtime_status == "runtime_unverified"):
            return False
        observed.add(app.project_id)
    return observed == projects.keys()


def decode(value, length):
    try:
        decoded = b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        decoded = b""
    if len(decoded) != length:
        raise HTTPException(422, "Invalid receipt credential.")
    return decoded


def accepted_receipt(db, job, instance, revision):
    if instance is None or revision != job["release_revision"]:
        return False
    return bool(db.execute("""
        SELECT 1 FROM smd_deploy_receipt
        WHERE task_id=%s AND instance_id=%s AND verified_at IS NOT NULL
          AND (%s = false OR connections IS NOT NULL)
    """, (job["id"], instance, bool(job.get("dns_zone")))).fetchone())


@router.post("/admin/smd-deploy-tasks/{task_id}/receipt-enrollment")
def enroll(task_id: UUID, body: Enrollment):
    key = decode(body.public_key, 32)
    supplied = sha256(body.claim_token.encode()).hexdigest()
    with database() as db:
        job = db.execute("SELECT * FROM smd_deploy_task WHERE id=%s FOR UPDATE", (task_id,)).fetchone()
        if not job or not compare_digest(job["claim_digest"] or "", supplied):
            raise HTTPException(403, "Invalid deployment claim.")
        if not job["release_revision"] or job["status"] != "processing":
            raise HTTPException(409, "Deployment is unavailable for receipt enrollment.")
        if job["instance_id"] and job["instance_id"] != body.instance_id:
            raise HTTPException(409, "Deployment instance conflicts with the queue.")
        if job.get("dns_zone") and (body.ipv4 is None or not body.ipv4.is_global):
            raise HTTPException(422, "A public Vultr IPv4 address is required.")
        db.execute("""
            INSERT INTO smd_deploy_receipt (task_id,instance_id,public_key,expected_ipv4) VALUES (%s,%s,%s,%s)
            ON CONFLICT DO NOTHING
        """, (task_id, body.instance_id, key, body.ipv4))
        row = db.execute("SELECT instance_id,public_key,expected_ipv4 FROM smd_deploy_receipt WHERE task_id=%s",
                         (task_id,)).fetchone()
        if (not row or row["instance_id"] != body.instance_id or row["expected_ipv4"] != body.ipv4
                or not compare_digest(row["public_key"], key)):
            raise HTTPException(409, "Receipt enrollment already binds an instance or key.")
    return {"enrolled": True}


@router.get("/admin/smd-deploy-tasks/{task_id}/receipt")
def status(task_id: UUID):
    with database() as db:
        row = db.execute("SELECT verified_at,connections FROM smd_deploy_receipt WHERE task_id=%s", (task_id,)).fetchone()
    verified = bool(row and row["verified_at"])
    return {"verified": verified, "connections": row["connections"] if verified else None}


@router.get("/admin/smd-deploy-tasks/{task_id}/evidence/{view}.png")
def screenshot(task_id: UUID, view: Literal["portal", "coder", "application", "connections"]):
    folder = os.getenv("SMARTDEV_SMD_EVIDENCE_DIRECTORY")
    if folder:
        file = Path(folder) / str(task_id) / (view + ".png")
        if file.is_file() and file.resolve().parent == Path(folder).resolve() / str(task_id):
            return FileResponse(file, media_type="image/png", headers={"Cache-Control": "private, no-store"})
    raise HTTPException(404, "No browser evidence is available for this view.")


@router.post("/smd-deploy-tasks/{task_id}/receipt")
def receive(task_id: UUID, body: Receipt):
    signature = decode(body.signature, 64)
    connections = body.connections.model_dump(mode="json", exclude_none=True) if body.connections else None
    message = f"{task_id}\n{body.project_id}\n{body.instance_id}\n{body.revision}\n".encode("ascii")
    if connections is not None:
        message += (json.dumps(connections, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
    with database() as db:
        job = db.execute("SELECT * FROM smd_deploy_task WHERE id=%s FOR UPDATE", (task_id,)).fetchone()
        row = db.execute("SELECT * FROM smd_deploy_receipt WHERE task_id=%s", (task_id,)).fetchone()
        if not job or not row:
            raise HTTPException(409, "Receipt enrollment is pending.")
        if (job["status"] not in ("processing", "needs_attention", "deployed")
                or job["project_id"] != body.project_id or job["release_revision"] != body.revision
                or row["instance_id"] != body.instance_id or not connections_match(job, row, body.connections)):
            raise HTTPException(409, "Receipt does not match the enrolled deployment.")
        try:
            Ed25519PublicKey.from_public_bytes(row["public_key"]).verify(signature, message)
        except (InvalidSignature, ValueError):
            raise HTTPException(403, "Invalid deployment signature.") from None
        db.execute("""
            UPDATE smd_deploy_receipt SET signature=%s,verified_at=now(),connections=%s
            WHERE task_id=%s AND verified_at IS NULL
        """, (signature, Jsonb(connections) if connections is not None else None, task_id))
    return {"verified": True}
