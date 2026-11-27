"""Read-only Coder fleet inventory for the Command Center owner."""

import os
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.get("/admin/coder-inventory")
def inventory():
    api = os.getenv("CODER_API_URL")
    token = os.getenv("CODER_TOKEN")
    public = os.getenv("CODER_PUBLIC_URL", "").rstrip("/")
    if not api or not token or not public:
        raise HTTPException(503, "Coder admin is not configured.")
    try:
        with httpx.Client(base_url=api, timeout=8, trust_env=False,
                          headers={"Coder-Session-Token": token}) as client:
            workspace_response = client.get("/api/v2/workspaces", params={"limit": 200})
            template_response = client.get("/api/v2/templates")
            workspace_response.raise_for_status()
            template_response.raise_for_status()
            workspaces = workspace_response.json()
            templates = template_response.json()
        rows = []
        agents = []
        for workspace in workspaces["workspaces"] or []:
            build = workspace["latest_build"]
            path = "/@" + quote(workspace["owner_name"], safe="") + "/" + quote(workspace["name"], safe="")
            rows.append({"id": workspace["id"], "name": workspace["name"], "owner": workspace["owner_name"],
                         "template": workspace["template_name"], "status": build["status"],
                         "outdated": workspace["outdated"], "url": public + path})
            for resource in build.get("resources") or []:
                for agent in resource.get("agents") or []:
                    agents.append({"id": agent["id"], "name": agent["name"], "workspace": workspace["name"],
                                   "status": agent["status"], "workspace_url": public + path})
        return {
            "workspaces": rows, "total_workspaces": workspaces["count"], "agents": agents,
            "templates": [{"id": item["id"], "name": item["display_name"] or item["name"],
                           "organization": item["organization_name"],
                           "active_version": item["active_version_id"]} for item in templates],
            "native_url": public,
        }
    except (httpx.HTTPError, KeyError, TypeError, ValueError):
        raise HTTPException(502, "Could not load the Coder inventory.") from None
