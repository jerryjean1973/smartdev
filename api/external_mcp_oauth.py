"""OAuth authorization codes and rotating refresh tokens for external grants."""

import base64
import re
from hashlib import sha256
from secrets import token_urlsafe
from uuid import uuid4

from external_mcp_grants import token_hash
from operator_dispatch import configured_project

PKCE_VERIFIER = re.compile(r"[A-Za-z0-9._~-]{43,128}")
PKCE_CHALLENGE = re.compile(r"[A-Za-z0-9_-]{43}")


def s256(verifier):
    if not PKCE_VERIFIER.fullmatch(verifier):
        raise ValueError("Invalid PKCE verifier")
    return base64.urlsafe_b64encode(sha256(verifier.encode("ascii")).digest()).decode().rstrip("=")


def authorize_projects(db, projects):
    projects = sorted(set(projects))
    permitted = db.execute("""
        SELECT p.id FROM projects p WHERE p.id=ANY(%s) AND NOT p.archived
          AND NOT EXISTS (SELECT 1 FROM acceptance_fixture f WHERE f.project_id=p.id)
        FOR SHARE
    """, (projects,)).fetchall()
    if not projects or len(permitted) != len(projects):
        raise ValueError("Project not available for authorization")
    return projects


def create_code(db, *, owner_id, client_id, redirect_uri, challenge, project_ids, scopes, resource=None):
    """Persist an authorization already approved by the authenticated owner."""
    if not PKCE_CHALLENGE.fullmatch(challenge):
        raise ValueError("Invalid PKCE challenge")
    project_ids = authorize_projects(db, project_ids)
    code = token_urlsafe(32)
    db.execute("""
        INSERT INTO external_mcp_oauth_code
          (code_hash,owner_id,client_id,redirect_uri,challenge,project_ids,scopes,resource,expires_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,now()+interval '5 minutes')
    """, (token_hash(code), owner_id, client_id, redirect_uri, challenge, project_ids, scopes, resource))
    return code


def token_response(access_token, scopes, refresh_token=None):
    result = {"access_token": access_token, "token_type": "Bearer", "expires_in": 28800,
              "scope": " ".join(scopes)}
    if refresh_token:
        result["refresh_token"] = refresh_token
    return result


def exchange_code(db, *, code, client_id, redirect_uri, verifier, resource=None):
    """Consume the valid code and issue a scoped bearer in the same transaction."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", code):
        raise ValueError("Invalid authorization code")
    row = db.execute("""
        DELETE FROM external_mcp_oauth_code
        WHERE code_hash=%s AND client_id=%s AND redirect_uri=%s AND challenge=%s
          AND resource IS NOT DISTINCT FROM %s AND expires_at>now()
        RETURNING owner_id,project_ids,scopes
    """, (token_hash(code), client_id, redirect_uri, s256(verifier), resource)).fetchone()
    if not row:
        raise ValueError("Invalid authorization code")
    project_ids = authorize_projects(db, row["project_ids"])
    token = "sdmcp_" + token_urlsafe(32)
    refresh = "sdmcp_refresh_" + token_urlsafe(32) if (
        resource and row["scopes"] == ["read"] and project_ids == [configured_project()]
    ) else None
    db.execute("""
        INSERT INTO external_mcp_grant
            (id,owner_id,token_hash,project_ids,scopes,resource,expires_at,
             refresh_token_hash,refresh_expires_at,refresh_client_id,refresh_redirect_uri)
        VALUES (%s,%s,%s,%s,%s,%s,now()+interval '8 hours',
                %s,CASE WHEN %s THEN now()+interval '90 days' END,%s,%s)
    """, (uuid4(), row["owner_id"], token_hash(token), project_ids, row["scopes"], resource,
          token_hash(refresh) if refresh else None, bool(refresh),
          client_id if refresh else None, redirect_uri if refresh else None))
    return token_response(token, row["scopes"], refresh)


def exchange_refresh(db, *, refresh_token, client_id, redirect_uri, resource, scope=None):
    if scope not in (None, "read") or not re.fullmatch(r"sdmcp_refresh_[A-Za-z0-9_-]{43}", refresh_token):
        raise ValueError("Invalid refresh grant")
    access = "sdmcp_" + token_urlsafe(32)
    replacement = "sdmcp_refresh_" + token_urlsafe(32)
    row = db.execute("""
        UPDATE external_mcp_grant g
        SET token_hash=%s, expires_at=now()+interval '8 hours',
            refresh_token_hash=%s, refresh_expires_at=now()+interval '90 days'
        FROM smartdev_owner o
        WHERE g.refresh_token_hash=%s AND g.refresh_expires_at>now()
          AND g.revoked_at IS NULL AND g.owner_id=o.id AND o.singleton
          AND g.refresh_client_id=%s AND g.refresh_redirect_uri=%s AND g.resource=%s
          AND g.scopes=ARRAY['read'] AND g.project_ids=ARRAY[%s]::uuid[]
        RETURNING g.project_ids
    """, (token_hash(access), token_hash(replacement), token_hash(refresh_token),
          client_id, redirect_uri, resource, configured_project())).fetchone()
    if not row:
        raise ValueError("Invalid refresh grant")
    authorize_projects(db, row["project_ids"])
    return token_response(access, ["read"], replacement)
