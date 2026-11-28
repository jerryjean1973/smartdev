"""Project-scoped provider broker for live managed coding-agent assignments."""
import base64
import hashlib
import json
import re
import secrets
import time
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from redis.exceptions import RedisError
from starlette.concurrency import run_in_threadpool
from starlette.responses import StreamingResponse

from store import database, queue
from vault import call

router = APIRouter()
OPENAI = "https://api.openai.com/v1"
ANTHROPIC = "https://api.anthropic.com/v1"
CAPABILITY = re.compile(r"Bearer ([A-Za-z0-9_-]{43})")
AGENTS = {"codex_cli", "claude_code"}
BC_PROJECT = UUID("3484ef03-4450-57d7-a96e-bb09c6d056ac")
HOST_BOUND_TASKS = {
    UUID("70ef5ff7-fcb2-40d4-9aff-ff8139c6c333"),
    UUID("d339468d-5910-48ac-ba35-a74470dacfe4"),
}



class RunAccess(BaseModel):
    execution_id: UUID
    objective_id: UUID | None = None
    conversation_id: UUID | None = None
    agent: str | None = None
    environment: str | None = None


def selected_agent(project_id):
    """Resolve the project override, then the global default, without exposing credentials."""
    if project_id is None:
        return "codex_cli"
    with database() as db:
        row = db.execute("""SELECT provider FROM integration_ai_role
            WHERE role='coder' AND scope IN (%s,'global')
            ORDER BY (scope=%s) DESC LIMIT 1""", (str(project_id), str(project_id))).fetchone()
    provider = (row or {}).get("provider")
    return provider if provider in AGENTS else "codex_cli"


def active_run(project_id, run):
    if project_id == BC_PROJECT and run.objective_id in HOST_BOUND_TASKS:
        raise HTTPException(
            409,
            "Host execution blocked. This task requires an approved DEV operation through "
            "the SmartDev operator. No task implementation ran in the workspace.",
        )
    with database() as db:
        if run.conversation_id and not run.objective_id:
            if not db.execute("""SELECT 1 FROM conversation_execution r JOIN project_conversation c
                ON c.id=r.conversation_id JOIN projects p ON p.id=c.project_id
                WHERE r.id=%s AND c.id=%s AND p.id=%s AND NOT p.archived
                  AND r.finished_at IS NULL AND r.expires_at>now()""",
                (run.execution_id,run.conversation_id,project_id)).fetchone():
                raise HTTPException(403, "Codex access requires an active conversation execution.")
            return
        if not run.objective_id or run.conversation_id or not db.execute("""
            SELECT 1 FROM objective_run r JOIN projects p ON p.id=r.project_id
            WHERE r.id=%s AND r.project_id=%s AND r.objective_id=%s AND NOT p.archived
              AND r.finished_at IS NULL AND r.lease_until>now()
        """, (run.execution_id, project_id, run.objective_id)).fetchone():
            raise HTTPException(403, "Codex access requires an active project assignment.")


def credential(project_id):
    path = f"projects/{project_id}/DEV/OPENAI_API_KEY"
    result = call("GET", f"data/{path}", missing=True)
    key = ((result or {}).get("data", {}).get("data") or {}).get("value")
    if result is None:
        metadata = call("GET", f"metadata/{path}", missing=True)
        versions = (metadata or {}).get("data", {})
        current = versions.get("versions", {}).get(str(versions.get("current_version")), {})
        if current.get("deletion_time") or current.get("destroyed"):
            raise HTTPException(401, "The current DEV OpenAI key was removed. Set a new project DEV OpenAI key.")
    if not isinstance(key, str) or not key.strip():
        raise HTTPException(401, "Codex authentication required. Set OPENAI_API_KEY in this project's DEV secrets.")
    return validate_key(key)


def anthropic_credential(project_id):
    path = f"projects/{project_id}/DEV/ANTHROPIC_API_KEY"
    result = call("GET", f"data/{path}", missing=True)
    key = ((result or {}).get("data", {}).get("data") or {}).get("value")
    if not key:
        shared = call("GET", "data/integrations/anthropic", missing=True)
        key = ((shared or {}).get("data", {}).get("data") or {}).get("credential")
    if not isinstance(key, str) or not re.fullmatch(r"[\x21-\x7e]{16,4096}", key.strip()):
        raise HTTPException(401, "Claude Code authentication required. Set ANTHROPIC_API_KEY in this project's DEV secrets.")
    return key.strip()


def agent_configured(project_id, agent):
    if agent not in AGENTS or project_id is None:
        return False
    try:
        (credential if agent == "codex_cli" else anthropic_credential)(project_id)
        return True
    except HTTPException:
        return False


def validate_key(key):
    if not re.fullmatch(r"[\x21-\x7e]{16,4096}", key.strip()):
        raise HTTPException(401, "Codex authentication required. Replace the DEV OpenAI key.")
    return key.strip()


def check_code(code):
    if code == 401:
        raise HTTPException(401, "OpenAI rejected the DEV key. Replace the expired, revoked, or invalid project key.")
    if code == 403:
        raise HTTPException(403, "OpenAI rejected access for this project key.")
    if code == 429:
        raise HTTPException(429, "OpenAI rate limit reached. Retry later.")
    if not 200 <= code < 300:
        raise HTTPException(502, "OpenAI is unavailable or rejected the Codex request.")


def deployment_credential(project_id):
    from smd_owner_client import enrolled
    return None if enrolled() else credential(project_id)


def verify_provider_key(key):
    key = validate_key(key)
    try:
        with httpx.Client(timeout=4, follow_redirects=False, trust_env=False) as client:
            for attempt in range(2):
                try:
                    response = client.get(OPENAI + "/models", headers={"Authorization": "Bearer " + key})
                except httpx.RequestError:
                    if attempt:
                        raise
                else:
                    if response.status_code not in (429, 500, 502, 503, 504) or attempt:
                        check_code(response.status_code)
                        return
                time.sleep(0.5)
    except httpx.RequestError:
        raise HTTPException(503, "Codex authentication check is temporarily unavailable.") from None


def check_anthropic_code(code):
    if code == 401:
        raise HTTPException(401, "Anthropic rejected the DEV key. Replace the expired, revoked, or invalid project key.")
    if code == 403:
        raise HTTPException(403, "Anthropic rejected access for this project key.")
    if code == 429:
        raise HTTPException(429, "Anthropic rate limit reached. Retry later.")
    if not 200 <= code < 300:
        raise HTTPException(502, "Anthropic is unavailable or rejected the Claude Code request.")


def verify_anthropic_key(key):
    try:
        with httpx.Client(timeout=4, follow_redirects=False, trust_env=False) as client:
            response = client.get(ANTHROPIC + "/models", headers={
                "x-api-key": key, "anthropic-version": "2023-06-01",
            })
        check_anthropic_code(response.status_code)
    except httpx.RequestError:
        raise HTTPException(503, "Claude Code authentication check is temporarily unavailable.") from None


def credential_marker(project_id, agent):
    from smd_owner_client import enrolled, keys
    if enrolled():
        digest = hashlib.sha256()
        for key_file in keys():
            digest.update(hashlib.sha256(key_file.read_bytes()).digest())
        return digest.hexdigest()
    key = credential(project_id) if agent == "codex_cli" else anthropic_credential(project_id)
    return hashlib.sha256(key.encode()).hexdigest()


def check_credentials(project_id, agent):
    from smd_owner_client import enrolled, owner_json
    if agent == "codex_cli":
        key = deployment_credential(project_id)
        if key is None:
            owner_json(project_id, "codex-models")
        else:
            verify_provider_key(key)
        return credential_marker(project_id, agent)
    if agent != "claude_code":
        raise HTTPException(409, "Unsupported coding agent selection.")
    if enrolled():
        owner_json(project_id, "claude-models")
    else:
        verify_anthropic_key(anthropic_credential(project_id))
    return credential_marker(project_id, agent)


@router.post("/workspace-memory/{project_id}/agent-selection")
def selection(project_id: UUID):
    return {"agent": selected_agent(project_id)}


@router.post("/workspace-memory/{project_id}/codex-access")
def preflight(project_id: UUID, run: RunAccess):
    agent = selected_agent(project_id)
    if run.agent and run.agent != agent:
        raise HTTPException(409, "The project's coding agent changed before this execution started. Retry the task.")
    for attempt in range(2):
        active_run(project_id, run)
        try:
            marker = check_credentials(project_id, agent)
            break
        except HTTPException as exc:
            if attempt or exc.detail not in {
                "OpenBao is unavailable or rejected vault access.",
                "Command Center cannot read the assigned tenant credentials from OpenBao.",
            }:
                raise
            time.sleep(.5)
    try:
        from access_inventory import resolve
        inventory = resolve(project_id, run.environment, agent, marker)
        bearer = secrets.token_urlsafe(32)
        queue().set("smartdev:codex-bearer:" + bearer,
                    json.dumps([str(project_id), run.model_dump(mode="json"), agent,
                                inventory["receipt"]["sha256"]]), ex=24 * 60 * 60)
        return {"bearer": bearer, "agent": agent, "access_inventory": inventory}
    except RedisError:
        raise HTTPException(503, "Codex authentication check is temporarily unavailable.") from None


def authorize_call(project_id, authorization, expected_agent="codex_cli"):
    match = CAPABILITY.fullmatch(authorization)
    if not match:
        raise HTTPException(401, "Managed coding-agent authentication required.")
    try:
        stored = queue().get("smartdev:codex-bearer:" + match[1])
    except RedisError:
        raise HTTPException(503, "Coding-agent authorization is temporarily unavailable.") from None
    if not stored:
        raise HTTPException(401, "Managed coding-agent authentication expired.")
    payload = json.loads(stored)
    project, run = payload[:2]
    agent = payload[2] if len(payload) > 2 else "codex_cli"
    if project != str(project_id):
        raise HTTPException(403, "Coding-agent session belongs to another project.")
    if agent != expected_agent:
        raise HTTPException(403, "Coding-agent capability cannot be used with another provider.")
    active_run(project_id, RunAccess.model_validate(run))
    if len(payload) < 4:
        raise HTTPException(401, "Managed coding-agent access inventory is missing; start a fresh session.")
    from access_inventory import resolve
    current = resolve(project_id, run.get("environment"), agent, credential_marker(project_id, agent))
    if not secrets.compare_digest(payload[3], current["receipt"]["sha256"]):
        raise HTTPException(401, "Managed access bindings or credentials changed; renew with a fresh session preflight.")
    if expected_agent == "codex_cli":
        return deployment_credential(project_id)
    from smd_owner_client import enrolled
    return None if enrolled() else anthropic_credential(project_id)


@router.post("/workspace-memory/{project_id}/codex/v1/responses")
async def model_response(project_id: UUID, request: Request):
    key = await run_in_threadpool(authorize_call, project_id, request.headers.get("authorization", ""), "codex_cli")
    payload = bytearray()
    async for chunk in request.stream():
        if len(payload) + len(chunk) > 32 * 1024 * 1024:
            raise HTTPException(413, "Codex request exceeds the project inference limit.")
        payload.extend(chunk)
    client = httpx.AsyncClient(timeout=httpx.Timeout(180, connect=8), follow_redirects=False, trust_env=False)
    try:
        if key is None:
            from smd_owner_client import keys,signed_request
            for key_file in keys():
                url,headers=signed_request(project_id,"codex-responses",bytes(payload),key_file)
                response=await client.send(client.build_request("POST",url,content=bytes(payload),headers=headers),stream=True)
                if response.status_code!=403 or response.headers.get("x-smd-identity")!="superseded":
                    break
                await response.aclose()
        else:
            response=await client.send(client.build_request("POST",OPENAI+"/responses",content=bytes(payload),
                headers={"Authorization":"Bearer "+key,"Content-Type":"application/json"}),stream=True)
        if not 200 <= response.status_code < 300:
            retry = response.headers.get("retry-after", "")
            status = response.status_code
            await response.aclose()
            if status == 429 and re.fullmatch(r"\d{1,3}", retry):
                raise HTTPException(429, "OpenAI rate limit reached.", headers={"Retry-After": retry})
            check_code(status)
    except httpx.RequestError:
        await client.aclose()
        raise HTTPException(503, "OpenAI connection unavailable.") from None
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

    return StreamingResponse(stream(), status_code=response.status_code,
                             media_type=response.headers.get("content-type", "application/json"),
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


async def claude_proxy(project_id, request, operation, upstream_path):
    authorization = request.headers.get("authorization", "")
    key = await run_in_threadpool(authorize_call, project_id, authorization, "claude_code")
    payload = bytearray()
    async for chunk in request.stream():
        if len(payload) + len(chunk) > 32 * 1024 * 1024:
            raise HTTPException(413, "Claude Code request exceeds the project inference limit.")
        payload.extend(chunk)
    version = request.headers.get("anthropic-version", "2023-06-01")
    beta = request.headers.get("anthropic-beta", "")
    client = httpx.AsyncClient(timeout=httpx.Timeout(300, connect=8), follow_redirects=False, trust_env=False)
    try:
        if key is None:
            from smd_owner_client import keys, signed_request
            envelope = json.dumps({
                "body": base64.b64encode(payload).decode(), "anthropic_version": version, "anthropic_beta": beta,
            }, separators=(",", ":")).encode()
            for key_file in keys():
                url, headers = signed_request(project_id, operation, envelope, key_file)
                response = await client.send(client.build_request("POST", url, content=envelope, headers=headers), stream=True)
                if response.status_code != 403 or response.headers.get("x-smd-identity") != "superseded":
                    break
                await response.aclose()
        else:
            headers = {"x-api-key": key, "anthropic-version": version, "content-type": "application/json"}
            if beta:
                headers["anthropic-beta"] = beta
            response = await client.send(client.build_request(
                "POST", ANTHROPIC + upstream_path, content=bytes(payload), headers=headers,
            ), stream=True)
        if not 200 <= response.status_code < 300:
            status = response.status_code
            retry = response.headers.get("retry-after", "")
            await response.aclose()
            if status == 429 and re.fullmatch(r"\d{1,3}", retry):
                raise HTTPException(429, "Anthropic rate limit reached.", headers={"Retry-After": retry})
            check_anthropic_code(status)
    except httpx.RequestError:
        await client.aclose()
        raise HTTPException(503, "Anthropic connection unavailable.") from None
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

    return StreamingResponse(stream(), status_code=response.status_code,
        media_type=response.headers.get("content-type", "application/json"),
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.post("/workspace-memory/{project_id}/claude/v1/messages")
async def claude_messages(project_id: UUID, request: Request):
    return await claude_proxy(project_id, request, "claude-messages", "/messages")


@router.post("/workspace-memory/{project_id}/claude/v1/messages/count_tokens")
async def claude_count_tokens(project_id: UUID, request: Request):
    return await claude_proxy(project_id, request, "claude-count-tokens", "/messages/count_tokens")
