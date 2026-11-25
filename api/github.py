"""List repositories available to the configured GitHub App installation."""
import base64
import json
import os
import time
from pathlib import Path

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from fastapi import APIRouter, HTTPException, Query

from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, Field

from core import REPOSITORY
from store import database, notify

router = APIRouter()


def encode(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=")


def app_token(client_id):
    now = int(time.time())
    claims = json.dumps({"iat": now - 60, "exp": now + 480, "iss": client_id}).encode()
    message = b"eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9." + encode(claims)
    key = serialization.load_pem_private_key(Path("/run/github-app/private-key.pem").read_bytes(), None)
    return (message + b"." + encode(key.sign(message, padding.PKCS1v15(), hashes.SHA256()))).decode()


@router.get("/github/repositories")
def repositories(page: int = Query(1, ge=1, le=1000)):
    client_id = os.getenv("GITHUB_CLIENT_ID")
    installation = os.getenv("GITHUB_INSTALLATION_ID", "")
    coder_url = os.getenv("CODER_PUBLIC_URL", "").rstrip("/")
    connect = coder_url + "/external-auth/github" if coder_url else None
    if not client_id or not installation.isdecimal():
        return {"repositories": [], "next": None, "connect": connect, "configured": False}
    try:
        with httpx.Client(base_url="https://api.github.com", timeout=10, headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }) as client:
            auth = {"Authorization": "Bearer " + app_token(client_id)}
            response = client.post(f"/app/installations/{installation}/access_tokens",
                                   headers=auth, json={"permissions": {"metadata": "read"}})
            response.raise_for_status()
            response = client.get("/installation/repositories",
                                  params={"per_page": 100, "page": page},
                                  headers={"Authorization": "Bearer " + response.json()["token"]})
            response.raise_for_status()
            return {"repositories": [item["full_name"] for item in response.json()["repositories"]],
                    "next": page + 1 if "next" in response.links else None, "connect": connect,
                    "configured": True}
    except (OSError, ValueError, TypeError, KeyError, httpx.HTTPError):
        raise HTTPException(502, "Could not list repositories for the configured GitHub App installation.")


class ImportRepositories(BaseModel):
    repositories: list[str] = Field(min_length=1, max_length=100)


@router.post("/github/import", status_code=202)
def import_repositories(body: ImportRepositories):
    requested = {name.lower(): name for name in body.repositories}
    if any(not REPOSITORY.fullmatch(name) for name in requested.values()):
        raise HTTPException(422, "Select valid GitHub repositories.")
    allowed = {}
    page = 1
    while page:
        listing = repositories(page)
        if not listing["configured"]:
            raise HTTPException(409, "Configure the GitHub App installation first.")
        allowed.update((name.lower(), name) for name in listing["repositories"] if name.lower() in requested)
        if allowed.keys() == requested.keys():
            break
        page = listing["next"]
    if allowed.keys() != requested.keys():
        raise HTTPException(403, "The GitHub App installation does not include all selected repositories.")
    queued, existing = [], []
    with database() as db:
        for normalized, repository in sorted(allowed.items()):
            identifier = uuid5(NAMESPACE_URL, "https://github.com/" + normalized)
            prior = db.execute("SELECT id FROM projects WHERE lower(repository)=%s LIMIT 1", (normalized,)).fetchone()
            if prior:
                existing.append(repository)
                continue
            created = db.execute("""
                INSERT INTO projects (id,name,repository,runtime,status)
                VALUES (%s,%s,%s,'other','queued') ON CONFLICT DO NOTHING RETURNING id
            """, (identifier, repository.split("/", 1)[1][:80], repository)).fetchone()
            if created:
                db.execute("INSERT INTO audit (project_id,action) VALUES (%s,'create')", (identifier,))
                queued.append(repository)
            else:
                existing.append(repository)
    if queued:
        notify()
    return {"queued": queued, "existing": existing}
