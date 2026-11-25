"""Expose the selected project's Coder workspace lifecycle to the owner portal."""
import os
from typing import Literal
from urllib.parse import quote, urlsplit
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from store import database

router = APIRouter()


class Transition(BaseModel):
    action: Literal["start", "stop"]


def workspace_request(project_id, action=None):
    with database() as db:
        project = db.execute("SELECT workspace_url FROM projects WHERE id=%s AND NOT archived",
                             (project_id,)).fetchone()
    if not project:
        raise HTTPException(404, "Project not found.")
    url = project["workspace_url"]
    if not url:
        return {"status": "unprovisioned", "editor_ready": False}
    coder_url = os.getenv("CODER_API_URL")
    token = os.getenv("CODER_TOKEN")
    if not coder_url or not token:
        return {"status": "unavailable", "editor_ready": False}
    segments = urlsplit(url).path.strip("/").split("/")
    if len(segments) != 2 or not segments[0].startswith("@"):
        raise HTTPException(409, "Project has an invalid workspace URL.")
    owner, name = (quote(part, safe="") for part in (segments[0][1:], segments[1]))
    try:
        with httpx.Client(base_url=coder_url, timeout=5, headers={"Coder-Session-Token": token}) as client:
            response = client.get(f"/api/v2/users/{owner}/workspace/{name}")
            response.raise_for_status()
            workspace = response.json()
            build = workspace["latest_build"]
            status = build["status"]
            if action:
                if status != {"start": "stopped", "stop": "running"}[action]:
                    raise HTTPException(409, f"Cannot {action} a workspace in the {status} state.")
                metadata = {"transition": action}
                if action == "start":
                    active_version = workspace.get("template_active_version_id")
                    metadata["template_version_id"] = active_version or build["template_version_id"]
                response = client.post(f"/api/v2/workspaces/{UUID(workspace['id'])}/builds", json=metadata)
                response.raise_for_status()
                return {"status": response.json()["status"], "editor_ready": False}
            healthy_apps = {app["slug"] for resource in build.get("resources") or []
                            for agent in resource.get("agents") or [] if agent["status"] == "connected"
                            for app in agent.get("apps") or [] if app.get("health") == "healthy"}
            return {"status": status, "dev_preview_url": url.rstrip("/") + "/apps/dev-preview/", "editor_ready": "editor" in healthy_apps,
                    "preview_ready": "dev-preview" in healthy_apps, "chat_ready": "codex-chat" in healthy_apps}
    except (httpx.HTTPError, ValueError, KeyError):
        raise HTTPException(502, "Coder could not report or update this workspace.") from None


@router.get("/projects/{project_id}/development")
def development(project_id: UUID):
    return workspace_request(project_id)


@router.post("/projects/{project_id}/development")
def lifecycle(project_id: UUID, body: Transition):
    return workspace_request(project_id, body.action)
