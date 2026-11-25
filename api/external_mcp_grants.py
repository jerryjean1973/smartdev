"""Owner-managed credentials for external SmartDev callers."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from secrets import token_urlsafe
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from store import database

router = APIRouter()


class GrantRequest(BaseModel):
    project_ids: list[UUID] = Field(min_length=1, max_length=32)
    scopes: list[Literal["read", "write"]] = Field(min_length=1, max_length=2)


def token_hash(token):
    return sha256(token.encode()).digest()


@router.post("/external-mcp/grants", status_code=201)
def create_grant(body: GrantRequest):
    project_ids = sorted(set(body.project_ids))
    scopes = sorted(set(body.scopes))
    token = "sdmcp_" + token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=8)
    grant_id = uuid4()
    with database() as db:
        projects = db.execute("""
            SELECT id FROM projects p WHERE id=ANY(%s) AND NOT archived
              AND NOT EXISTS (SELECT 1 FROM acceptance_fixture f WHERE f.project_id=p.id)
            FOR SHARE
        """, (project_ids,)).fetchall()
        if len(projects) != len(project_ids):
            raise HTTPException(404, "Project not available.")
        db.execute("""
            INSERT INTO external_mcp_grant(id,owner_id,token_hash,project_ids,scopes,expires_at)
            SELECT %s,id,%s,%s,%s,%s FROM smartdev_owner WHERE singleton
        """, (grant_id, token_hash(token), project_ids, scopes, expires))
    return {"grant_id": grant_id, "token": token, "expires_at": expires,
            "project_ids": project_ids, "scopes": scopes}


@router.delete("/external-mcp/grants/{grant_id}")
def revoke_grant(grant_id: UUID):
    with database() as db:
        db.execute("""
            UPDATE external_mcp_grant SET revoked_at=now()
            WHERE id=%s AND revoked_at IS NULL
              AND owner_id=(SELECT id FROM smartdev_owner WHERE singleton)
        """, (grant_id,))
    return {"ok": True}


def require_grant(authorization, scope, resource=None):
    prefix, _, token = authorization.partition(" ")
    if prefix.lower() != "bearer" or not token.startswith("sdmcp_") or len(token) != 49:
        raise HTTPException(401, "External authorization required.")
    with database() as db:
        row = db.execute("""
            SELECT g.id,g.owner_id,g.scopes,g.project_ids FROM external_mcp_grant g
            JOIN smartdev_owner o ON o.id=g.owner_id AND o.singleton
            WHERE g.token_hash=%s AND g.revoked_at IS NULL AND g.expires_at>now()
              AND (%s::text IS NULL OR g.resource=%s)
        """, (token_hash(token), resource, resource)).fetchone()
    if not row:
        raise HTTPException(401, "External authorization required.")
    if scope not in row["scopes"]:
        raise HTTPException(403, "External permission required.")
    return row
