"""Owner access to versioned OpenBao values. Workspace credentials have no vault route."""
import os
import re
from pathlib import Path
from typing import Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, SecretStr

from store import database

router = APIRouter()
Scope = Literal["DEV", "QA", "PREPROD", "PROD"]
NAME = re.compile(r"[A-Z_][A-Z0-9_]{0,95}")


class SecretWrite(BaseModel):
    value: SecretStr
    version: int = Field(ge=0)


class SecretVersion(BaseModel):
    version: int = Field(ge=1)


def owner_project(project_id):
    with database() as db:
        if not db.execute("SELECT 1 FROM projects WHERE id=%s AND NOT archived", (project_id,)).fetchone():
            raise HTTPException(404, "Active project not found.")


def audit(project_id, action, scope, name):
    with database() as db:
        db.execute("INSERT INTO audit (project_id,action) VALUES (%s,%s)",
                   (project_id, f"vault:{action}:{scope}:{name}"))


def call(method, route, body=None, missing=False):
    address = os.getenv("OPENBAO_ADDR", "")
    if not address:
        raise HTTPException(503, "OpenBao is awaiting configuration.")
    try:
        token = Path("/run/command-vault/token").read_text().strip()
        with httpx.Client(timeout=7, follow_redirects=False, trust_env=False) as client:
            response = client.request(method, address.rstrip("/") + "/v1/smartdev/" + route,
                                      headers={"X-Vault-Token": token}, json=body)
        if response.status_code == 404 and missing:
            return None
        if response.status_code == 400 and method == "POST" and route.startswith("data/"):
            raise HTTPException(409, "Secret version changed or the vault rejected the write. Refresh and retry.")
        response.raise_for_status()
        return response.json() if response.content else {}
    except (OSError, ValueError, httpx.HTTPError):
        raise HTTPException(502, "OpenBao is unavailable or rejected vault access.") from None


def prefix(project_id, scope, name=None):
    owner_project(project_id)
    if name is not None and not NAME.fullmatch(name):
        raise HTTPException(422, "Use an environment variable name with uppercase letters, numbers and underscores.")
    return f"projects/{project_id}/{scope}" + (f"/{name}" if name else "")


@router.get("/projects/{project_id}/secrets/{scope}")
def list_secrets(project_id: UUID, scope: Scope):
    key = prefix(project_id, scope)
    result = call("LIST", "metadata/" + key, missing=True)
    entries = []
    for name in (result or {}).get("data", {}).get("keys", []):
        if not NAME.fullmatch(name):
            continue
        row = call("GET", f"metadata/{key}/{name}", missing=True)
        if not row:
            continue
        metadata = row["data"]
        version = metadata["current_version"]
        latest = metadata["versions"][str(version)]
        if not latest["deletion_time"] and not latest["destroyed"]:
            entries.append({"name": name, "version": version, "updated": latest["created_time"]})
    return entries


@router.put("/projects/{project_id}/secrets/{scope}/{name}")
def store_secret(project_id: UUID, scope: Scope, name: str, body: SecretWrite):
    key = prefix(project_id, scope, name)
    value = body.value.get_secret_value()
    if not value or len(value.encode()) > 32_768:
        raise HTTPException(422, "Enter a nonempty secret of at most 32 KB.")
    result = call("POST", "data/" + key, {"options": {"cas": body.version}, "data": {"value": value}})
    audit(project_id, "write", scope, name)
    return {"version": result["data"]["version"]}


@router.post("/projects/{project_id}/secrets/{scope}/{name}/reveal")
def reveal_secret(project_id: UUID, scope: Scope, name: str, body: SecretVersion):
    if name == "OPENAI_API_KEY":
        raise HTTPException(403, "Managed Codex credentials can be rotated here.")
    key = prefix(project_id, scope, name)
    result = call("GET", f"data/{key}?version={body.version}", missing=True)
    if not result:
        raise HTTPException(404, "Secret version is unavailable.")
    audit(project_id, "reveal", scope, name)
    return {"value": result["data"]["data"]["value"]}


@router.post("/projects/{project_id}/secrets/{scope}/{name}/delete")
def delete_secret(project_id: UUID, scope: Scope, name: str, body: SecretVersion):
    key = prefix(project_id, scope, name)
    call("POST", "delete/" + key, {"versions": [body.version]})
    audit(project_id, "delete", scope, name)
    return {"ok": True}
