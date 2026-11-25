"""Persistent project integration configuration and lease-scoped MCP transport."""
import hashlib
import json
import os
import re
import secrets
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from codex_access import CAPABILITY, RunAccess, active_run
from store import database, queue
from vault import NAME, call

router = APIRouter()
BINDING_NAME = re.compile(r"[a-z][a-z0-9_]{0,47}")
MAX_REQUEST = 8 * 1024 * 1024
TRANSPORT_HEADERS = ("accept", "content-type", "mcp-session-id", "mcp-protocol-version", "last-event-id")


class Configuration(BaseModel):
    endpoint: str = Field(max_length=1000)
    secret_name: str = Field(max_length=96)
    credential_header: str = Field(pattern=r"^(authorization|x-api-key|x-mig-api-key)$")
    tenant: str = Field(min_length=1, max_length=128)
    organization: str = Field(min_length=1, max_length=128)
    principal: str = Field(min_length=1, max_length=180)
    domains: list[str] = Field(max_length=30)
    resources: list[str] = Field(max_length=30)
    enabled: bool = True


def allowed_endpoint(endpoint):
    try:
        parsed = urlsplit(endpoint)
        return (parsed.scheme == "https" and parsed.hostname and not parsed.username
                and not parsed.password and not parsed.query and not parsed.fragment
                and endpoint in os.getenv("SMARTDEV_MCP_ALLOWED_ENDPOINTS", "").split(","))
    except ValueError:
        return False


@router.get("/projects/{project_id}/mcp-bindings")
def bindings(project_id: UUID):
    with database() as db:
        return db.execute("""SELECT name,configuration,enabled,revision,status,checked_at
            FROM project_mcp_binding WHERE project_id=%s ORDER BY name""", (project_id,)).fetchall()


@router.put("/projects/{project_id}/mcp-bindings/{name}")
def save_binding(project_id: UUID, name: str, body: Configuration):
    if not BINDING_NAME.fullmatch(name) or not NAME.fullmatch(body.secret_name) or not allowed_endpoint(body.endpoint):
        raise HTTPException(422, "Use an approved HTTPS MCP endpoint and a project DEV secret reference.")
    with database() as db:
        if not db.execute("SELECT 1 FROM projects WHERE id=%s AND NOT archived FOR UPDATE", (project_id,)).fetchone():
            raise HTTPException(404, "Project not available.")
        row = db.execute("""INSERT INTO project_mcp_binding (project_id,name,configuration,enabled,status)
            VALUES (%s,%s,%s,%s,%s)
            ON CONFLICT (project_id,name) DO UPDATE SET configuration=excluded.configuration,
            enabled=excluded.enabled,revision=project_mcp_binding.revision+1,status=excluded.status,checked_at=NULL
            RETURNING revision""", (project_id,name,Jsonb(body.model_dump(exclude={"enabled"})),
                                    body.enabled,"CONFIGURED" if body.enabled else "UNAVAILABLE")).fetchone()
        return row


@router.post("/workspace-memory/{project_id}/mcp-bindings")
def prepare_bindings(project_id: UUID, run: RunAccess):
    active_run(project_id, run)
    rows = bindings(project_id)
    if not rows:
        return {"bindings": []}
    ready = {row["name"]: row["revision"] for row in rows
             if row["enabled"] and allowed_endpoint(row["configuration"]["endpoint"])}
    bearer = secrets.token_urlsafe(32)
    if ready:
        queue().set("smartdev:mcp:" + bearer, json.dumps(
            [str(project_id), run.model_dump(mode="json"), ready]), ex=86400)
    return {"bearer": bearer if ready else None, "bindings": [
        {"name": row["name"], "ready": row["name"] in ready,
         "status": row["status"], "tenant": row["configuration"]["tenant"],
         "organization": row["configuration"]["organization"]} for row in rows]}


def target(project_id, name, authorization):
    match = CAPABILITY.fullmatch(authorization)
    raw = queue().get("smartdev:mcp:" + match[1]) if match else None
    if not raw:
        raise HTTPException(401, "Managed MCP authorization required.")
    project, run, revisions = json.loads(raw)
    if project != str(project_id) or name not in revisions:
        raise HTTPException(403, "MCP binding is outside this execution.")
    active_run(project_id, RunAccess.model_validate(run))
    with database() as db:
        row = db.execute("""SELECT configuration,revision FROM project_mcp_binding WHERE project_id=%s
            AND name=%s AND enabled AND revision=%s""", (project_id,name,revisions[name])).fetchone()
    if not row or not allowed_endpoint(row["configuration"]["endpoint"]):
        raise HTTPException(403, "MCP binding is unavailable or has changed.")
    return row


def status(project_id, name, revision, state):
    with database() as db:
        db.execute("""UPDATE project_mcp_binding SET status=%s,checked_at=now()
            WHERE project_id=%s AND name=%s AND revision=%s""", (state,project_id,name,revision))


def upstream_headers(project_id, config, supplied):
    result = call("GET", f"data/projects/{project_id}/DEV/{config['secret_name']}", missing=True)
    value = ((result or {}).get("data", {}).get("data") or {}).get("value")
    if not isinstance(value, str) or not re.fullmatch(r"[!-~]{16,4096}", value):
        raise HTTPException(403, "MCP credential is missing or invalid.")
    header = config["credential_header"]
    return {**supplied, header: ("Bearer " if header == "authorization" else "") + value}


@router.api_route("/workspace-memory/{project_id}/mcp/{name}", methods=["POST","GET","DELETE"])
async def transport(project_id: UUID, name: str, request: Request):
    authorization = request.headers.get("authorization", "")
    row = await run_in_threadpool(target, project_id, name, authorization)
    session_prefix = f"smartdev:mcp-session:{hashlib.sha256(authorization.encode()).hexdigest()}:{name}:"
    session = request.headers.get("mcp-session-id")
    if session and not await run_in_threadpool(queue().exists, session_prefix + hashlib.sha256(session.encode()).hexdigest()):
        raise HTTPException(403, "MCP transport session is outside this execution.")
    config = row["configuration"]
    supplied = {key: request.headers[key] for key in TRANSPORT_HEADERS if key in request.headers}
    try:
        headers = await run_in_threadpool(upstream_headers, project_id, config, supplied)
    except HTTPException as error:
        await run_in_threadpool(status, project_id, name, row["revision"],
                                "AUTHORIZATION_REQUIRED" if error.status_code == 403 else "UNAVAILABLE")
        raise
    payload = bytearray()
    async for part in request.stream():
        if len(payload) + len(part) > MAX_REQUEST:
            raise HTTPException(413, "MCP request exceeds the binding limit.")
        payload.extend(part)
    client = httpx.AsyncClient(follow_redirects=False, trust_env=False, timeout=httpx.Timeout(120,connect=5),
                               verify=os.getenv("SMARTDEV_MCP_CA_FILE") or True)
    try:
        response = await client.send(client.build_request(request.method,config["endpoint"],
                                     headers=headers,content=bytes(payload)),stream=True)
        code = response.status_code
        opened_session = response.headers.get("mcp-session-id")
        if opened_session and 200 <= code < 300:
            await run_in_threadpool(queue().set,
                session_prefix + hashlib.sha256(opened_session.encode()).hexdigest(), "1", ex=86400)
        result = "AUTHORIZATION_REQUIRED" if code in (401,403) else "CONNECTED" if 200 <= code < 300 else "UNAVAILABLE"
        await run_in_threadpool(status, project_id, name, row["revision"], result)
        if not 200 <= code < 300:
            await response.aclose()
            raise HTTPException(code if code in (401,403,404,405,429) else 502,
                                "The MCP service rejected this connection or request.")
    except httpx.RequestError:
        await client.aclose()
        await run_in_threadpool(status, project_id, name, row["revision"], "UNAVAILABLE")
        raise HTTPException(503, "MCP service is unreachable.") from None
    except BaseException:
        await client.aclose()
        raise

    async def stream():
        try:
            async for chunk in response.aiter_bytes():
                yield chunk
        finally:
            await response.aclose()
            await client.aclose()

    if code in (202, 204):
        await response.aclose()
        await client.aclose()
        return Response(status_code=code)
    return StreamingResponse(stream(),status_code=code,
        headers={key:response.headers[key] for key in TRANSPORT_HEADERS if key in response.headers})
