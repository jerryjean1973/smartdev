"""Issue single-use, deployment-bound portal access to authenticated Command Center owners."""
from hashlib import sha256
import json
import os
from secrets import token_urlsafe
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
import httpx
from pydantic import BaseModel, Field

from portal_handoff import ISSUER, PROTOCOL, RECEIVER, TOKEN, challenge, fields, post_page, require_origin
from store import database

router = APIRouter()
origin = os.environ["PORTAL_ORIGIN"].rstrip("/")
PUBLIC_POST = {"/deployment-sign-in/return", "/deployment-sign-in/redeem"}


def binding(db, task, *, updating=False):
    row = db.execute("""
        SELECT t.instance_id,a.version,a.portal_account,r.connections->>'portal_url' AS portal,
               t.dns_zone FROM smd_deploy_task t JOIN smd_deployment_assignment a ON a.task_id=t.id
          JOIN smd_deploy_receipt r ON r.task_id=t.id AND r.instance_id=t.instance_id
        WHERE t.id=%s AND t.status='deployed' AND t.reconciled_at IS NULL AND r.verified_at IS NOT NULL
        FOR SHARE OF t,r
    """ + (" FOR UPDATE OF a" if updating else " FOR SHARE OF a"), (task,)).fetchone()
    if not row or not row["portal"] or row["portal"].rstrip("/") != f"https://smd-{task.hex}.{row['dns_zone']}":
        raise HTTPException(409, "This deployment has no active, verified portal.")
    row["portal"] = row["portal"].rstrip("/")
    return row


def authenticate_receiver(task, target):
    expected = {"issuer": origin, "portal": target["portal"], "task_id": str(task),
                "instance_id": str(target["instance_id"]), "account": target["portal_account"], "protocol": PROTOCOL}
    try:
        with httpx.Client(trust_env=False, timeout=5) as client:
            with client.stream("GET", target["portal"] + RECEIVER + "/metadata") as res:
                if res.status_code == 200 and res.headers.get("content-type", "").startswith("application/json"):
                    body = bytearray()
                    for chunk in res.iter_bytes(1024):
                        body.extend(chunk)
                        if len(body) > 2048:
                            break
                    if len(body) <= 2048 and json.loads(body) == expected:
                        return
                if res.status_code >= 500:
                    raise HTTPException(503, "The deployment portal is unreachable.")
    except httpx.HTTPError:
        raise HTTPException(503, "The deployment portal is unreachable.") from None
    except ValueError:
        pass
    raise HTTPException(409, "One-click sign-in is awaiting enrollment on this deployment.")


@router.get("/admin/compute/deployments/{task}/portal-login")
def readiness(task: UUID):
    with database() as db:
        target = binding(db, task)
    authenticate_receiver(task, target)
    return {"ready": True, "account": target["portal_account"]}


@router.post("/admin/compute/deployments/{task}/portal-login")
def launch(task: UUID, request: Request):
    require_origin(request, origin)
    with database() as db:
        target = binding(db, task)
    authenticate_receiver(task, target)
    session = request.cookies["__Host-command"]
    flow = uuid4()
    with database() as db:
        current = binding(db, task, updating=True)
        if target != current:
            raise HTTPException(409, "The deployment changed. Open it again.")
        db.execute("UPDATE smd_deployment_assignment SET portal_ready_instance=%s WHERE task_id=%s AND portal_ready_instance IS DISTINCT FROM %s",
                   (target["instance_id"], task, target["instance_id"]))
        db.execute("DELETE FROM smd_portal_launch WHERE expires_at <= now()")
        db.execute("""INSERT INTO smd_portal_launch(id,task_id,instance_id,assignment_version,session_digest,session_expires)
                      VALUES (%s,%s,%s,%s,%s,%s)""",
                   (flow, task, target["instance_id"], target["version"], sha256(session.encode()).digest(), int(session.split(".")[0])))
    return post_page(target["portal"] + RECEIVER + "/start", flow=flow)


def flow_id(value):
    try:
        return UUID(value)
    except ValueError:
        raise HTTPException(400, "Invalid portal sign-in request.") from None


def current_flow(db, flow):
    row = db.execute("""SELECT * FROM smd_portal_launch WHERE id=%s AND expires_at>clock_timestamp()
                        AND session_expires>extract(epoch FROM clock_timestamp()) FOR UPDATE""", (flow,)).fetchone()
    if row:
        current = binding(db, row["task_id"])
        if row["instance_id"] == current["instance_id"] and row["assignment_version"] == current["version"]:
            return row, current
    raise HTTPException(403, "This portal sign-in expired or the deployment changed. Open it again.")


@router.post("/deployment-sign-in/return")
async def return_to_issuer(request: Request):
    form = await fields(request, "flow", "challenge")
    flow = flow_id(form["flow"])
    if not TOKEN.fullmatch(form["challenge"]):
        raise HTTPException(400, "Invalid browser challenge.")
    with database() as db:
        _, target = current_flow(db, flow)
    require_origin(request, target["portal"])
    # Commit an issuer-origin document before posting, so Strict owner cookies work across sites.
    return post_page(origin + ISSUER + "/authorize", **form)


@router.post("/deployment-sign-in/authorize")
async def authorize(request: Request):
    require_origin(request, origin)
    form = await fields(request, "flow", "challenge")
    flow = flow_id(form["flow"])
    if not TOKEN.fullmatch(form["challenge"]):
        raise HTTPException(400, "Invalid browser challenge.")
    code = token_urlsafe(32)
    with database() as db:
        row, target = current_flow(db, flow)
        if row["session_digest"] != sha256(request.cookies["__Host-command"].encode()).digest() or row["code_digest"]:
            raise HTTPException(403, "Open a new sign-in from the deployment portal.")
        db.execute("UPDATE smd_portal_launch SET challenge=%s,code_digest=%s WHERE id=%s",
                   (form["challenge"], sha256(code.encode()).digest(), flow))
    return post_page(target["portal"] + RECEIVER + "/return", flow=flow, code=code)


class Redemption(BaseModel, extra="forbid"):
    task_id: UUID
    instance_id: UUID
    flow: UUID
    code: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    verifier: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")


@router.post("/deployment-sign-in/redeem")
def redeem(body: Redemption):
    with database() as db:
        row, target = current_flow(db, body.flow)
        if (row["task_id"] != body.task_id or row["instance_id"] != body.instance_id
                or row["code_digest"] != sha256(body.code.encode()).digest()
                or row["challenge"] != challenge(body.verifier)):
            raise HTTPException(403, "Portal sign-in could not be verified.")
        db.execute("DELETE FROM smd_portal_launch WHERE id=%s", (body.flow,))
        db.execute("INSERT INTO smd_deployment_event(task_id,kind,label) VALUES (%s,'portal_login','Command Center owner redeemed portal sign-in')", (row["task_id"],))
    return {"account": target["portal_account"], "portal": target["portal"], "task_id": str(body.task_id),
            "instance_id": str(body.instance_id), "session_expires": row["session_expires"]}
