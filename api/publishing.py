"""Publish registered DEV routes through Coder without accepting runtime addresses."""
import hmac
import os
import re
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from development import workspace_request
from store import database

router = APIRouter()
AUTH_PATH = "/internal/dev-gateway/authorize"
DEV_PATH = re.compile(r"^(/@[\w.-]+/[A-Za-z0-9-]+)/apps/dev-preview(?:/|$)", re.ASCII)


def project(db, project_id):
    row = db.execute("""SELECT id,name,workspace_url FROM projects WHERE id=%s AND NOT archived""",
                     (project_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Project not available.")
    return row


@router.get(AUTH_PATH, status_code=204)
def authorize(request: Request):
    configured = os.getenv("DEV_GATEWAY_TOKEN", "")
    token = request.headers.get("x-smartdev-gateway", "")
    supplied = request.headers.get("x-smartdev-preview-uri", "")
    match = DEV_PATH.match(urlsplit(supplied).path) if supplied.startswith("/") else None
    if not configured or not hmac.compare_digest(configured.encode(), token.encode()) or not match:
        return Response(status_code=403)
    origin = os.environ["CODER_PUBLIC_URL"].rstrip("/")
    with database() as db:
        exists = db.execute("SELECT 1 FROM projects WHERE workspace_url=%s AND NOT archived",
                            (origin + match.group(1),)).fetchone()
    return Response(status_code=204 if exists else 403)


@router.get("/projects/{project_id}/dev-publishing")
def published(project_id: UUID):
    with database() as db:
        row = project(db, project_id)
        observed = db.execute("SELECT observed FROM project_runtime WHERE project_id=%s AND environment='DEV'",
                              (project_id,)).fetchone()
        control = db.execute("SELECT enabled,revision FROM project_dev_publication WHERE project_id=%s",
                             (project_id,)).fetchone()
    workspace = workspace_request(project_id)
    activity = (observed or {}).get("observed", {})
    try:
        gateway = httpx.get("http://dev-gateway:8080/__smartdev/gateway-health", timeout=2, trust_env=False).status_code == 204
    except httpx.HTTPError:
        gateway = False
    return {"url": row["workspace_url"].rstrip("/") + "/apps/dev-preview/" if row["workspace_url"] else None,
            "gateway": gateway, "workspace": workspace["status"], "registered": bool(row["workspace_url"]),
            "runtime": activity.get("status", "unreported") if workspace["status"]=="running" else "unavailable",
            "ready": bool(workspace.get("preview_ready") and activity.get("status")=="ready"),
            "enabled": (control or {}).get("enabled", True)}


class ApplicationAction(BaseModel):
    action: Literal["start", "stop", "restart"]


@router.post("/projects/{project_id}/dev-publishing")
def publication_control(project_id: UUID, body: ApplicationAction):
    with database() as db:
        project(db, project_id)
        policy = db.execute("SELECT mode,url FROM project_preview_url WHERE project_id=%s AND environment='DEV'",
                            (project_id,)).fetchone()
        if body.action!="stop" and policy and (policy["url"] or policy["mode"]=="url"):
            raise HTTPException(409,"This project uses a configured DEV URL. Enable Automatic Preview to start a managed application.")
        db.execute("""INSERT INTO project_dev_publication(project_id,enabled,revision) VALUES (%s,%s,1)
            ON CONFLICT(project_id) DO UPDATE SET enabled=EXCLUDED.enabled,revision=project_dev_publication.revision+1""",
                   (project_id, body.action!="stop"))
        db.execute("INSERT INTO audit(project_id,action) VALUES (%s,%s)", (project_id,"preview:application:"+body.action))
    if body.action!="stop" and workspace_request(project_id)["status"]=="stopped":
        workspace_request(project_id, "start")
    return {"ok":True}
