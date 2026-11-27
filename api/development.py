"""Expose the selected project's Coder workspace lifecycle to the owner portal."""
import os
import json
import threading
import time
from pathlib import Path
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


_session_lock = threading.Lock()
_local_session = None
_session_until = 0


def local_coder_token():
    global _local_session, _session_until
    configured = os.getenv("CODER_TOKEN")
    if configured or not os.getenv("SMD_PORTAL_TASK"):
        return configured
    with _session_lock:
        if _local_session and time.monotonic() < _session_until:
            return _local_session
        try:
            credentials = json.loads(Path("/run/command-coder/owner.json").read_text())
            with httpx.Client(base_url=os.environ["CODER_API_URL"],trust_env=False,timeout=4) as client:
                result=client.post("/api/v2/users/login",json={k:credentials[k] for k in ("email","password")})
                result.raise_for_status()
                _local_session=result.json()["session_token"]
                _session_until=time.monotonic()+900
                return _local_session
        except (OSError,KeyError,ValueError,httpx.HTTPError):
            return None


_health_paths = {"editor":"healthz","codex-chat":"","dev-preview":".__smartdev/health"}
_probe_cache = {}


def probe_registered_apps(client, workspace_path, apps):
    # Older enrolled templates registered the apps without a Coder healthcheck.
    from concurrent.futures import ThreadPoolExecutor
    def probe(slug):
        key = (workspace_path,slug)
        cached = _probe_cache.get(key)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        try:
            response=client.get(workspace_path+"/apps/"+slug+"/"+_health_paths[slug],
                headers={"Host":urlsplit(os.environ["CODER_PUBLIC_URL"]).netloc,"Sec-Fetch-Site":"same-origin"},timeout=3)
            ready=response.status_code==200
        except httpx.HTTPError:
            ready=False
        _probe_cache[key]=(time.monotonic()+(15 if ready else 3),ready)
        return ready
    with ThreadPoolExecutor(max_workers=3) as pool:
        return {slug for slug,ready in zip(apps,pool.map(probe,apps)) if ready}


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
    token = local_coder_token()
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
            if os.getenv("SMD_PORTAL_TASK") and status=="running":
                unchecked = [app["slug"] for resource in build.get("resources") or []
                             for agent in resource.get("agents") or [] if agent["status"]=="connected"
                             for app in agent.get("apps") or [] if app.get("health")=="disabled" and app["slug"] in _health_paths]
                if unchecked:
                    healthy_apps.update(probe_registered_apps(client,"/@"+owner+"/"+name,unchecked))
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

