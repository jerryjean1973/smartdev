"""Durable owner-approved SMD jobs picked up by the Command Center executor."""

import json
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from hmac import digest as hmac_digest
from pathlib import Path
from secrets import compare_digest, token_urlsafe
from typing import Literal
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from core import REPOSITORY
from github import app_token
from smd_product_context import bind_contexts
from smd_git_ssh import key_for as ssh_key_for, pin as pin_ssh_revision
from store import database

router = APIRouter()
REGION = "atl"
PLAN = "vc2-4c-8gb"
OS_ID = 2284
PUBLIC = ("id,project_id,status,instance_id,observed_revision,note,queued_at,updated_at,"
          "region,plan,os_id,monthly_cost_usd,release_revision,reconciled_at,"
          "project_manifest,target_tenant,dns_zone,deployment_hostname,coder_hostname")
MEMBER = "(project_id=%s OR project_manifest @> %s)"
AVAILABLE_PROJECTS = """
    EXISTS (SELECT 1 FROM projects p WHERE p.id=t.project_id AND NOT p.archived)
    AND NOT EXISTS (
        SELECT 1 FROM jsonb_array_elements(t.project_manifest) AS member
        LEFT JOIN projects p ON p.id=(member->>'id')::uuid
        WHERE p.id IS NULL OR p.archived
    )
"""
OFFER = {"region": REGION, "plan": PLAN, "os_id": OS_ID, "monthly_cost_usd": 40}


def configured_offer(*, prepared=False):
    if os.getenv("SMARTDEV_SMD_ROLLOUT_BLOCKER"):
        return None
    prefix = "SMARTDEV_SMD_PRODUCT_" if prepared else "SMARTDEV_SMD_"
    url = os.environ.get(prefix + "INSTALLER_URL", "")
    digest = os.environ.get(prefix + "INSTALLER_SHA256", "")
    revision = os.environ.get(prefix + "RELEASE_REVISION", "")
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.fragment or any(char.isspace() for char in url)
            or not re.fullmatch(r"[a-f0-9]{64}", digest)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", revision)):
        return None
    return {**OFFER, "release_revision": revision, "installer_url": url,
            "installer_sha256": digest}


def offer_token(project_id, offer):
    encoded = json.dumps([str(project_id), offer], sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode()).hexdigest()


class DeploymentRequest(BaseModel, extra="forbid"):
    offer_token: str = Field(pattern=r"^[a-f0-9]{64}$")


def project(db, project_id, lock=False):
    query = """
        SELECT id FROM projects WHERE id=%s AND NOT archived AND NOT EXISTS (
            SELECT 1 FROM acceptance_fixture WHERE project_id=projects.id
        )
    """
    if lock:
        query += " FOR NO KEY UPDATE"
    if not db.execute(query, (project_id,)).fetchone():
        raise HTTPException(404, "Project not available.")


def membership(project_id):
    return project_id, Jsonb([{"id": str(project_id)}])


@router.get("/projects/{project_id}/development/smd")
def deployment(project_id: UUID):
    with database() as db:
        project(db, project_id)
        job = db.execute(f"SELECT {PUBLIC} FROM smd_deploy_task WHERE {MEMBER} AND reconciled_at IS NULL",
                         membership(project_id)).fetchone()
        retired = db.execute("""
            SELECT id,reconciled_at FROM smd_deploy_task WHERE project_id=%s AND reconciled_at IS NOT NULL
            ORDER BY reconciled_at DESC LIMIT 1
        """, (project_id,)).fetchone()
    offer = configured_offer()
    if os.getenv("SMARTDEV_SMD_DNS_ZONE"):
        return {"deployment": job, "reconciled_deployment": retired, "configured": False, **OFFER,
                "release_revision": offer["release_revision"] if offer else None, "offer_token": None,
                "configuration_note": "Select and pin project revisions in the Deployment console."}
    return {"deployment": job, "reconciled_deployment": retired, "configured": offer is not None, **OFFER,
            "release_revision": offer["release_revision"] if offer else None,
            "offer_token": offer_token(project_id, offer) if offer else None,
            "configuration_note": None if offer else (os.getenv("SMARTDEV_SMD_ROLLOUT_BLOCKER") or "SMD release and installer are not configured.")}


@router.post("/projects/{project_id}/development/smd", status_code=202)
def deploy(project_id: UUID, body: DeploymentRequest):
    with database() as db:
        project(db, project_id, lock=True)
        job = db.execute(f"SELECT {PUBLIC},offer_token FROM smd_deploy_task WHERE {MEMBER} AND reconciled_at IS NULL",
                         membership(project_id)).fetchone()
        if job:
            if not compare_digest(job.pop("offer_token") or "", body.offer_token):
                raise HTTPException(409, "This project already has a deployment request.")
            return job
        if os.getenv("SMARTDEV_SMD_DNS_ZONE"):
            raise HTTPException(409, "Select and pin project revisions in the Deployment console.")
        offer = configured_offer()
        if not offer:
            raise HTTPException(503, "SMD release and installer are not configured.")
        if not compare_digest(offer_token(project_id, offer), body.offer_token):
            raise HTTPException(409, "The deployment offer changed. Review it and click Deploy SMD again.")
        return db.execute(f"""
            INSERT INTO smd_deploy_task (
                id,project_id,status,offer_token,region,plan,os_id,monthly_cost_usd,
                release_revision,installer_url,installer_sha256
            ) VALUES (
                %(id)s,%(project_id)s,'queued',%(offer_token)s,%(region)s,%(plan)s,%(os_id)s,
                %(monthly_cost_usd)s,%(release_revision)s,%(installer_url)s,%(installer_sha256)s
            ) RETURNING {PUBLIC}
        """, {**offer, "id": uuid4(), "project_id": project_id,
              "offer_token": body.offer_token}).fetchone()


GIT_SHA = r"[0-9a-f]{40}"
DNS_ZONE = r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"


class ProjectSelection(BaseModel, extra="forbid"):
    id: UUID
    revision: str | None = Field(default=None, pattern=f"^{GIT_SHA}$")


class Selection(BaseModel, extra="forbid"):
    projects: list[ProjectSelection] = Field(min_length=1, max_length=20)
    target_tenant: str | None = Field(default=None, max_length=80)


class GroupDeployment(Selection, DeploymentRequest):
    pass


def selected_projects(db, body, lock=False):
    revisions = {item.id: item.revision for item in body.projects}
    if len(revisions) != len(body.projects):
        raise HTTPException(422, "Each project may be selected only once.")
    for project_id in sorted(revisions):
        project(db, project_id, lock=lock)
    records = db.execute("""
        SELECT p.id,p.name,p.repository,p.runtime,t.tenant_name FROM projects p
        LEFT JOIN command_center_project_tenant t ON p.id=t.project_id
        WHERE p.id=ANY(%s) ORDER BY p.id
    """, (list(revisions),)).fetchall()
    if any(row["tenant_name"] != body.target_tenant for row in records):
        raise HTTPException(409, "All selected projects must share the current target tenant.")
    manifest = [{"id": str(row["id"]), "name": row["name"], "repository": row["repository"],
                 "runtime": row["runtime"], "revision": revisions[row["id"]]} for row in records]
    if any(not REPOSITORY.fullmatch(item["repository"] or "") for item in manifest):
        raise HTTPException(422, "Every selected project must have a valid GitHub repository.")
    return manifest


def active_member(db, owner):
    return db.execute(f"""
        SELECT {PUBLIC},offer_token FROM smd_deploy_task
        WHERE {MEMBER} AND reconciled_at IS NULL
    """, membership(owner)).fetchone()


def selected_offer(*, prepared=False):
    blocker = os.getenv("SMARTDEV_SMD_ROLLOUT_BLOCKER")
    if blocker:
        raise HTTPException(503, blocker)
    offer = configured_offer(prepared=prepared)
    zone = os.getenv("SMARTDEV_SMD_DNS_ZONE", "")
    if not offer or len(zone) > 213 or not re.fullmatch(DNS_ZONE, zone):
        raise HTTPException(503, "Configure the pinned SMD release and deployment DNS zone.")
    try:
        ssh_key = str(UUID(os.getenv("SMARTDEV_SMD_VULTR_SSH_KEY_ID", "")))
    except ValueError as error:
        raise HTTPException(503, "Configure the owner Vultr SSH key for deployment.") from error
    return {**offer, "dns_zone": zone, "ssh_key_id": ssh_key}


def pin_git_revisions(manifest):
    remaining = []
    try:
        for row in manifest:
            key = ssh_key_for(row["id"])
            if key:
                row["revision"] = pin_ssh_revision(row, key)
            else:
                remaining.append(row)
    except (OSError, ValueError, subprocess.SubprocessError):
        raise HTTPException(502, "An existing SSH deploy key could not verify a selected Git revision.") from None
    if not remaining:
        return
    manifest = remaining
    client_id = os.getenv("GITHUB_CLIENT_ID")
    installation = os.getenv("GITHUB_INSTALLATION_ID", "")
    if not client_id or not installation.isdecimal():
        raise HTTPException(503, "Configure the GitHub App to verify project revisions.")
    try:
        with httpx.Client(base_url="https://api.github.com", timeout=5, headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }) as client:
            response = client.post(f"/app/installations/{installation}/access_tokens",
                                   headers={"Authorization": "Bearer " + app_token(client_id)},
                                   json={"permissions": {"contents": "read"},
                                         "repositories": sorted({row["repository"].split("/")[1] for row in manifest})})
            response.raise_for_status()
            auth = {"Authorization": "Bearer " + response.json()["token"]}

            def resolve(row):
                requested = row["revision"]
                response = client.get(f"/repos/{row['repository']}/commits/{requested or 'HEAD'}", headers=auth)
                response.raise_for_status()
                sha = response.json()["sha"]
                if not re.fullmatch(GIT_SHA, sha) or requested and sha != requested:
                    raise ValueError("Commit does not match")
                return sha

            with ThreadPoolExecutor(max_workers=8) as pool:
                for row, revision in zip(manifest, pool.map(resolve, manifest), strict=True):
                    row["revision"] = revision
    except (OSError, ValueError, TypeError, KeyError, httpx.HTTPError) as error:
        raise HTTPException(502, "GitHub could not verify all selected project revisions.") from error


def group_token(offer, manifest, tenant):
    message = json.dumps([offer, manifest, tenant], sort_keys=True, separators=(",", ":"))
    return hmac_digest(bytes.fromhex(os.environ["SESSION_KEY"]), message.encode(), "sha256").hex()


@router.post("/admin/smd-deploy-tasks/offer")
def quote_selection(body: Selection):
    with database() as db:
        manifest = selected_projects(db, body)
        if any(active_member(db, UUID(row["id"])) for row in manifest):
            raise HTTPException(409, "At least one selected project already has a deployment request.")
    pin_git_revisions(manifest)
    bind_contexts(manifest)
    offer = selected_offer(prepared=any("build_context_sha256" in row for row in manifest))
    return {**OFFER, "release_revision": offer["release_revision"], "dns_zone": offer["dns_zone"],
            "projects": manifest, "target_tenant": body.target_tenant,
            "offer_token": group_token(offer, manifest, body.target_tenant)}


@router.post("/admin/smd-deploy-tasks", status_code=202)
def deploy_selection(body: GroupDeployment):
    with database() as db:
        manifest = selected_projects(db, body, lock=True)
        for entry in manifest:
            active = active_member(db, UUID(entry["id"]))
            if active:
                previous_token = active.pop("offer_token") or ""
                if (compare_digest(previous_token, body.offer_token)
                        and active["project_manifest"]
                        and [{key: row[key] for key in ("id", "revision")} for row in active["project_manifest"]]
                        == [{"id": row["id"], "revision": row["revision"]} for row in manifest]):
                    return active
                raise HTTPException(409, "At least one selected project already has a deployment request.")
        if any(not row["revision"] for row in manifest):
            raise HTTPException(422, "Pin a Git commit for every selected project.")
        bind_contexts(manifest)
        offer = selected_offer(prepared=any("build_context_sha256" in row for row in manifest))
        expected = group_token(offer, manifest, body.target_tenant)
        if not compare_digest(expected, body.offer_token):
            raise HTTPException(409, "The deployment offer changed. Review a fresh offer before deploying.")
        task_id = uuid4()
        zone = offer["dns_zone"]
        for row in manifest:
            row["preview_hostname"] = f"dev-{UUID(row['id']).hex[:12]}-{task_id.hex[:12]}.{zone}"
            row["hostname"] = f"app-{UUID(row['id']).hex[:12]}-{task_id.hex[:12]}.{zone}"
        return db.execute(f"""
            INSERT INTO smd_deploy_task (
                id,project_id,status,offer_token,region,plan,os_id,monthly_cost_usd,
                release_revision,installer_url,installer_sha256,project_manifest,target_tenant,
                dns_zone,deployment_hostname,coder_hostname,ssh_key_id
            ) VALUES (
                %(id)s,%(project_id)s,'queued',%(offer_token)s,%(region)s,%(plan)s,%(os_id)s,
                %(monthly_cost_usd)s,%(release_revision)s,%(installer_url)s,%(installer_sha256)s,
                %(manifest)s,%(target_tenant)s,%(dns_zone)s,%(deployment_hostname)s,%(coder_hostname)s,%(ssh_key_id)s
            ) RETURNING {PUBLIC}
        """, {**offer, "id": task_id, "project_id": UUID(manifest[0]["id"]),
              "offer_token": body.offer_token, "manifest": Jsonb(manifest), "target_tenant": body.target_tenant,
              "deployment_hostname": f"smd-{task_id.hex}.{zone}", "coder_hostname": f"coder-{task_id.hex}.{zone}"}).fetchone()


@router.get("/admin/smd-deploy-tasks")
def tasks():
    with database() as db:
        return db.execute("""
            SELECT t.id,t.project_id,t.status,t.instance_id,t.observed_revision,t.note,
                   t.queued_at,t.updated_at,t.region,t.plan,t.os_id,t.monthly_cost_usd,
                   t.release_revision,t.reconciled_at,t.reconciliation,t.project_manifest,t.target_tenant,
                   t.dns_zone,t.deployment_hostname,t.coder_hostname,p.name AS project_name
            FROM smd_deploy_task t JOIN projects p ON p.id=t.project_id
            ORDER BY CASE WHEN t.status='queued' THEN 0 ELSE 1 END,t.queued_at
        """).fetchall()


class ClaimRequest(BaseModel, extra="forbid"):
    claim_token: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{43,128}$")


@router.post("/admin/smd-deploy-tasks/{task_id}/claim")
def claim(task_id: UUID, body: ClaimRequest | None = None):
    supplied = body.claim_token if body else None
    token = supplied or token_urlsafe(32)
    digest = sha256(token.encode()).hexdigest()
    with database() as db:
        job = db.execute(f"""
            UPDATE smd_deploy_task t SET status='processing',claim_digest=%s,updated_at=now()
            WHERE t.id=%s AND t.status='queued'
              AND t.offer_token IS NOT NULL
              AND t.reconciled_at IS NULL
              AND {AVAILABLE_PROJECTS}
            RETURNING {PUBLIC},installer_url,installer_sha256,ssh_key_id
        """, (digest, task_id)).fetchone()
        if not job and supplied:
            job = db.execute(f"""
                SELECT {PUBLIC},installer_url,installer_sha256,ssh_key_id FROM smd_deploy_task t
                WHERE t.id=%s AND t.claim_digest=%s AND t.status='processing'
                  AND t.offer_token IS NOT NULL AND t.reconciled_at IS NULL
                  AND {AVAILABLE_PROJECTS}
            """, (task_id, digest)).fetchone()
        if not job:
            raise HTTPException(409, "This deployment is unavailable or already claimed.")
        manifest = job["project_manifest"]
        selected = manifest[0] if manifest else db.execute("SELECT name,repository FROM projects WHERE id=%s",
                                                           (job["project_id"],)).fetchone()
    return {**job, "name": selected["name"], "repository": selected["repository"], "projects": manifest or [],
            "claim_token": token, "provider_tag": f"smd-{task_id}"}


class LegacyReconciliation(BaseModel, extra="forbid"):
    prior_runner_stopped: Literal[True]
    untagged_inventory_reviewed: Literal[True]
    evidence_reference: str = Field(min_length=5, max_length=500, pattern=r"\S")


class RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def vultr_inventory_page(token, cursor):
    query = {"per_page": 100}
    if cursor:
        query["cursor"] = cursor
    request = Request("https://api.vultr.com/v2/instances?" + urlencode(query),
                      headers={"Authorization": "Bearer " + token})
    with build_opener(RejectRedirects()).open(request, timeout=5) as response:
        return json.load(response)


def verify_legacy_inventory(task_id):
    token_file = os.getenv("SMARTDEV_SMD_RECONCILIATION_VULTR_TOKEN_FILE")
    if not token_file:
        raise HTTPException(503, "The Vultr reconciliation credential is not configured.")
    tag = f"smd-{task_id}"
    seen = set()
    cursor = None
    try:
        token = Path(token_file).read_text().strip()
        if not token:
            raise ValueError("Empty credential")
        for _ in range(10):
            inventory = vultr_inventory_page(token, cursor)
            instances = inventory["instances"]
            if not isinstance(instances, list):
                raise ValueError("Unexpected instance collection")
            for instance in instances:
                label, tags = instance["label"], instance["tags"]
                if (not isinstance(label, str) or not isinstance(tags, list)
                        or any(not isinstance(value, str) for value in tags)):
                    raise ValueError("Unexpected instance label or tags")
                if label == tag or tag in tags:
                    raise HTTPException(409, "Vultr inventory contains a resource with the legacy deployment tag.")
            cursor = inventory["meta"]["links"]["next"]
            if cursor in (None, ""):
                return tag
            if not isinstance(cursor, str) or cursor in seen:
                break
            seen.add(cursor)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    raise HTTPException(503, "Vultr inventory could not be fully verified. The legacy request remains locked.")


@router.post("/admin/smd-deploy-tasks/{task_id}/reconcile-legacy")
def reconcile_legacy(task_id: UUID, body: LegacyReconciliation):
    with database() as db:
        selected = db.execute("SELECT project_id FROM smd_deploy_task WHERE id=%s", (task_id,)).fetchone()
        if not selected:
            raise HTTPException(404, "Deployment request not found.")
        project(db, selected["project_id"], lock=True)
        job = db.execute("SELECT * FROM smd_deploy_task WHERE id=%s FOR UPDATE", (task_id,)).fetchone()
        if job["reconciled_at"]:
            return {name: job[name] for name in PUBLIC.split(",")}
        if (job["status"] != "needs_attention" or job["offer_token"]
                or job["instance_id"] or job["observed_revision"]):
            raise HTTPException(409, "Only an unpinned legacy request without an instance or running release can be reconciled.")
        tag = verify_legacy_inventory(task_id)
        evidence = {"provider": "vultr", "provider_tag": tag, "matching_instances": 0,
                    "authorized_by": "command_center_owner", **body.model_dump()}
        return db.execute(f"""
            UPDATE smd_deploy_task SET reconciled_at=now(),reconciliation=%s,updated_at=now()
            WHERE id=%s RETURNING {PUBLIC}
        """, (Jsonb(evidence), task_id)).fetchone()


class Progress(BaseModel, extra="forbid"):
    claim_token: str = Field(min_length=1, max_length=128)
    status: Literal["processing", "deployed", "failed", "needs_attention"]
    instance_id: UUID | None = None
    observed_revision: str | None = Field(default=None, min_length=1, max_length=128)
    note: str = Field(min_length=1, max_length=2000)


@router.post("/admin/smd-deploy-tasks/{task_id}/progress")
def progress(task_id: UUID, body: Progress):
    supplied = sha256(body.claim_token.encode()).hexdigest()
    with database() as db:
        job = db.execute("SELECT * FROM smd_deploy_task WHERE id=%s FOR UPDATE",
                         (task_id,)).fetchone()
        if not job or not compare_digest(job["claim_digest"] or "", supplied):
            raise HTTPException(403, "Invalid deployment claim.")
        if job["reconciled_at"]:
            raise HTTPException(409, "This legacy deployment request has been reconciled.")
        instance = body.instance_id or job["instance_id"]
        revision = body.observed_revision or job["observed_revision"]
        if body.instance_id and job["instance_id"] and body.instance_id != job["instance_id"]:
            raise HTTPException(409, "The deployment already identifies another instance.")
        if body.status == "deployed" and not (instance and revision):
            raise HTTPException(422, "A deployed task requires an instance ID and running revision.")
        if job["status"] in {"deployed", "failed"}:
            if (job["status"],job["instance_id"],job["observed_revision"],job["note"]) != (
                body.status,instance,revision,body.note
            ):
                raise HTTPException(409, "This deployment has a final result.")
            return {name: job[name] for name in PUBLIC.split(",")}
        if body.status == "deployed":
            from smd_receipts import accepted_receipt

            if revision != job["release_revision"] or not accepted_receipt(db, job, instance, revision):
                raise HTTPException(409, "A verified receipt for the pinned release is required.")
        return db.execute(f"""
            UPDATE smd_deploy_task SET status=%s,instance_id=%s,observed_revision=%s,
                   note=%s,updated_at=now() WHERE id=%s RETURNING {PUBLIC}
        """, (body.status, instance, revision, body.note, task_id)).fetchone()
