"""Mint repository-scoped Git credentials to a signed deployment identity."""

import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from time import time
from typing import Literal
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask
import httpx
from pydantic import BaseModel, Field

from github import app_token
from smd_receipts import decode
from smd_product_context import approved_context
from smd_git_ssh import build_bundle, key_for
from store import database

router = APIRouter()


class GitTokenRequest(BaseModel, extra="forbid"):
    artifact: Literal["git-token", "build-context", "qa-runtime"] = "git-token"
    project_id: UUID
    instance_id: UUID
    timestamp: int
    nonce: str = Field(pattern=r"^[a-f0-9]{64}$")
    signature: str = Field(min_length=88, max_length=88)


def make_token(repository):
    app = os.getenv("GITHUB_CLIENT_ID")
    installation = os.getenv("GITHUB_INSTALLATION_ID", "")
    if not app or not installation.isdecimal():
        raise HTTPException(503, "The GitHub App is not configured for deployment.")
    try:
        with httpx.Client(base_url="https://api.github.com", timeout=10, trust_env=False, headers={
            "Authorization": "Bearer " + app_token(app),
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }) as client:
            lookup = client.get(f"/repos/{repository}/installation")
            lookup.raise_for_status()
            if str(lookup.json()["id"]) != installation:
                raise ValueError("Repository belongs to a different GitHub App installation.")
            response = client.post(f"/app/installations/{installation}/access_tokens", json={
                "repositories": [repository.split("/")[1]], "permissions": {"contents": "write"},
            })
            response.raise_for_status()
            token = response.json()["token"]
            if not isinstance(token, str) or not token:
                raise ValueError("Missing GitHub installation token.")
            return token
    except (OSError, httpx.HTTPError, KeyError, ValueError, TypeError):
        raise HTTPException(502, "Could not issue a credential for the approved repository.") from None


@router.post("/smd-deploy-tasks/{task_id}/git-token")
def issue(task_id: UUID, body: GitTokenRequest):
    if abs(time() - body.timestamp) > 120:
        raise HTTPException(403, "Git credential request expired.")
    signature = decode(body.signature, 64)
    message = (f"{body.artifact}\n{task_id}\n{body.project_id}\n{body.instance_id}\n"
               f"{body.timestamp}\n{body.nonce}\n").encode("ascii")
    with database() as db:
        job = db.execute("""
            SELECT t.status,t.project_manifest,r.instance_id,r.public_key,r.verified_at,
                   r.enrolled_at > now() - interval '2 hours' AS enrollment_current
            FROM smd_deploy_task t JOIN smd_deploy_receipt r ON r.task_id=t.id
            WHERE t.id=%s FOR UPDATE OF t
        """, (task_id,)).fetchone()
        if not job:
            raise HTTPException(409, "Deployment credential enrollment is pending.")
        manifest = job["project_manifest"] or []
        project = next((entry for entry in manifest if entry["id"] == str(body.project_id)), None)
        if (not project or job["instance_id"] != body.instance_id or not job["enrollment_current"]
                or job["verified_at"] or job["status"] not in {"processing", "needs_attention"}):
            raise HTTPException(403, "Deployment identity is not authorized for this repository.")
        try:
            Ed25519PublicKey.from_public_bytes(job["public_key"]).verify(signature, message)
        except (InvalidSignature, ValueError):
            raise HTTPException(403, "Invalid deployment signature.") from None
        recorded = db.execute("""
            INSERT INTO smd_git_token_nonce (task_id,nonce) VALUES (%s,%s)
            ON CONFLICT DO NOTHING RETURNING nonce
        """, (task_id, body.nonce)).fetchone()
        if not recorded:
            raise HTTPException(409, "Git credential request was already used.")
    if body.artifact == "qa-runtime":
        raise HTTPException(410,"Owner credentials are available only through assignment-bound configuration.")
    if body.artifact == "build-context":
        return FileResponse(approved_context(project), media_type="application/gzip",
                            headers={"Cache-Control": "private, no-store"})
    key = key_for(project["id"])
    if key:
        storage = os.getenv("SMARTDEV_SMD_GIT_BUNDLE_DIRECTORY", "")
        if not storage or not Path(storage).is_dir():
            raise HTTPException(503, "The secure Git bundle storage directory is unavailable.")
        scratch = TemporaryDirectory(dir=storage)
        try:
            bundle = build_bundle(project, key, scratch.name)
        except (OSError, ValueError, subprocess.SubprocessError):
            scratch.cleanup()
            raise HTTPException(502, "Could not build the pinned Git bundle.") from None
        return FileResponse(bundle, media_type="application/x-git-bundle",
                            headers={"Cache-Control": "no-store"}, background=BackgroundTask(scratch.cleanup))
    return JSONResponse({"token": make_token(project["repository"])},
                        headers={"Cache-Control": "no-store"})


class WorkspaceGitRead(BaseModel, extra="forbid"):
    repository: str


@router.post("/workspace-memory/{project_id}/git-credential")
def workspace_git_credential(project_id: UUID, body: WorkspaceGitRead):
    from core import REPOSITORY
    with database() as db:
        row = db.execute("SELECT repository FROM projects WHERE id=%s AND NOT archived", (project_id,)).fetchone()
    if not row or not REPOSITORY.fullmatch(row["repository"]) or row["repository"].casefold()!=body.repository.casefold():
        raise HTTPException(403, "The repository is outside this workspace.")
    return JSONResponse({"token": make_token(row["repository"])}, headers={"Cache-Control": "no-store"})
