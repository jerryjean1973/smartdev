"""Owner-approved read-only GitHub keys with durable operation IDs."""
import os
import subprocess
from contextlib import contextmanager
from uuid import UUID

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core import REPOSITORY
from github import app_token
from store import database
from vault import call

router = APIRouter(prefix="/integrations/github")


class Approval(BaseModel):
    repository: str
    approved: bool


def project_repository(db, project_id):
    row = db.execute("SELECT repository FROM projects WHERE id=%s AND NOT archived", (project_id,)).fetchone()
    if not row or not REPOSITORY.fullmatch(row["repository"]):
        raise HTTPException(409, "Select a project with a GitHub repository.")
    return row["repository"]


def require_approval(body, repository):
    if not body.approved or body.repository.casefold() != repository.casefold():
        raise HTTPException(403, "Approve the exact repository operation.")


def ssh_pair():
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH,
                                serialization.NoEncryption()).decode()
    public = key.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).decode()
    return {"private": private, "public": public}


@contextmanager
def github_client(repository):
    client_id = os.getenv("GITHUB_CLIENT_ID", "")
    installation = os.getenv("GITHUB_INSTALLATION_ID", "")
    if not client_id or not installation.isdecimal():
        raise HTTPException(409, "Configure the GitHub App installation.")
    try:
        with httpx.Client(base_url="https://api.github.com", timeout=12, trust_env=False, follow_redirects=False,
                          headers={"Accept":"application/vnd.github+json", "X-GitHub-Api-Version":"2022-11-28"}) as client:
            response = client.post(f"/app/installations/{installation}/access_tokens",
                headers={"Authorization":"Bearer "+app_token(client_id)},
                json={"repositories":[repository.split("/", 1)[1]], "permissions":{"administration":"write"}})
            response.raise_for_status()
            client.headers["Authorization"] = "Bearer " + response.json()["token"]
            selected = client.get(f"/repos/{repository}")
            selected.raise_for_status()
            if selected.json()["full_name"].casefold() != repository.casefold():
                raise HTTPException(409, "GitHub returned a different repository.")
            yield client
    except httpx.HTTPStatusError as error:
        if error.response.status_code in (401, 403, 404):
            raise HTTPException(409, "GitHub denied this operation. Check installation access and Administration write permission.") from None
        raise HTTPException(502, "GitHub rejected the operation. Retry using the same operation ID.") from None
    except (httpx.RequestError, OSError, ValueError, KeyError):
        raise HTTPException(502, "GitHub could not confirm the operation. Retry using the same operation ID.") from None


def find_key(client, repository, public):
    for page in range(1, 101):
        response = client.get(f"/repos/{repository}/keys", params={"per_page":100, "page":page})
        response.raise_for_status()
        match = next((row for row in response.json() if " ".join(row["key"].split()[:2]) == public), None)
        if match or "next" not in response.links:
            return match
    raise HTTPException(409, "GitHub deploy-key listing exceeded the inspection limit.")


def verify_git(repository, private):
    # Hold the private key in an anonymous Linux file, visible only to this process and its children.
    fd = os.memfd_create("github-deploy-key")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(os.dup(fd), "wb") as output:
            output.write(private.encode())
        os.lseek(fd, 0, os.SEEK_SET)
        command = (
            f"ssh -F /dev/null -i /proc/{os.getpid()}/fd/{fd} -o IdentityAgent=none -o IdentitiesOnly=yes "
            "-o BatchMode=yes -o StrictHostKeyChecking=yes -o HostKeyAlgorithms=ssh-ed25519 "
            "-o GlobalKnownHostsFile=/dev/null -o UserKnownHostsFile=/app/github_known_hosts"
        )
        result = subprocess.run(["git", "ls-remote", "--", f"git@github.com:{repository}.git"],
            env={"PATH":os.defpath, "HOME":"/nonexistent", "GIT_CONFIG_NOSYSTEM":"1", "GIT_CONFIG_GLOBAL":"/dev/null",
                 "GIT_SSH_COMMAND":command, "GIT_TERMINAL_PROMPT":"0"},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
    finally:
        os.close(fd)


def audit(db, project_id, action, operation):
    db.execute("INSERT INTO audit (project_id,action) VALUES (%s,%s)", (project_id, f"github:{action}:{operation}"))


@router.get("/projects/{project_id}/deploy-keys")
def keys(project_id: UUID):
    with database() as db:
        project_repository(db, project_id)
        return db.execute("SELECT * FROM integration_deploy_key WHERE project_id=%s ORDER BY created_at DESC",
                          (project_id,)).fetchall()


@router.put("/projects/{project_id}/deploy-keys/{operation}")
def provision(project_id: UUID, operation: UUID, body: Approval):
    with database() as db:
        repository = project_repository(db, project_id)
        require_approval(body, repository)
        db.execute("""INSERT INTO integration_deploy_key (id,project_id,repository) VALUES (%s,%s,%s)
                   ON CONFLICT DO NOTHING""", (operation, project_id, repository))
    with database() as db:
        row = db.execute("SELECT * FROM integration_deploy_key WHERE id=%s AND project_id=%s FOR UPDATE",
                         (operation, project_id)).fetchone()
        if not row or row["repository"].casefold() != repository.casefold() or row["status"] in ("revoking", "revoked"):
            raise HTTPException(409, "Operation ID is unavailable for this repository.")
        if row["status"] == "verified":
            return row
        path = f"data/projects/{project_id}/github-deploy/{operation}"
        with github_client(repository) as client:
            stored = call("GET", path, missing=True)
            if not stored:
                if row["remote_id"]:
                    raise HTTPException(409, "The original private key is unavailable.")
                pair = ssh_pair()
                call("POST", path, {"options":{"cas":0}, "data":pair})
            else:
                pair = stored["data"]["data"]
            remote = find_key(client, repository, pair["public"])
            if not remote:
                response = client.post(f"/repos/{repository}/keys",
                    json={"title":f"SmartDev {operation}", "key":pair["public"], "read_only":True})
                response.raise_for_status()
                remote = response.json()
            if remote["read_only"] is not True:
                raise HTTPException(409, "GitHub returned a key outside the approved read-only scope.")
        verified = verify_git(repository, pair["private"])
        updated = db.execute("""UPDATE integration_deploy_key SET remote_id=%s,status=%s,checked_at=now()
            WHERE id=%s RETURNING *""", (remote["id"], "verified" if verified else "registered", operation)).fetchone()
        audit(db, project_id, updated["status"], operation)
        return updated


@router.post("/projects/{project_id}/deploy-keys/{operation}/revoke")
def revoke(project_id: UUID, operation: UUID, body: Approval):
    with database() as db:
        row = db.execute("SELECT * FROM integration_deploy_key WHERE id=%s AND project_id=%s FOR UPDATE",
                         (operation, project_id)).fetchone()
        if not row:
            raise HTTPException(404, "Deploy key not found.")
        repository = row["repository"]
        require_approval(body, repository)
        if row["status"] == "revoked":
            return row
        db.execute("UPDATE integration_deploy_key SET status='revoking' WHERE id=%s", (operation,))
    with database() as db:
        row = db.execute("SELECT * FROM integration_deploy_key WHERE id=%s AND project_id=%s FOR UPDATE",
                         (operation, project_id)).fetchone()
        if row["status"] == "revoked":
            return row
        reference = f"projects/{project_id}/github-deploy/{operation}"
        pair = call("GET", "data/" + reference, missing=True)
        with github_client(repository) as client:
            if not pair:
                if row["remote_id"]:
                    # A previous attempt may have removed the vault value after deleting remotely.
                    check = client.get(f"/repos/{repository}/keys/{row['remote_id']}")
                    if check.status_code != 404:
                        raise HTTPException(409, "Cannot safely match the registered public key.")
            else:
                remote = find_key(client, repository, pair["data"]["data"]["public"])
                if remote:
                    target = f"/repos/{repository}/keys/{remote['id']}"
                    response = client.delete(target)
                    if response.status_code != 404:
                        response.raise_for_status()
                    if client.get(target).status_code != 404:
                        raise HTTPException(502, "GitHub did not confirm key removal.")
        call("DELETE", "metadata/" + reference, missing=True)
        updated = db.execute("UPDATE integration_deploy_key SET status='revoked',checked_at=now() WHERE id=%s RETURNING *",
                             (operation,)).fetchone()
        audit(db, project_id, "revoke", operation)
        return updated
