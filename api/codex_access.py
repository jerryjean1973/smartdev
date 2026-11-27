"""Project-scoped OpenAI broker for live managed Codex assignments."""
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
CAPABILITY = re.compile(r"Bearer ([A-Za-z0-9_-]{43})")
BC_PROJECT = UUID("3484ef03-4450-57d7-a96e-bb09c6d056ac")
HOST_BOUND_TASKS = {
    UUID("70ef5ff7-fcb2-40d4-9aff-ff8139c6c333"),
    UUID("d339468d-5910-48ac-ba35-a74470dacfe4"),
}



class RunAccess(BaseModel):
    execution_id: UUID
    objective_id: UUID | None = None
    conversation_id: UUID | None = None


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


@router.post("/workspace-memory/{project_id}/codex-access")
def preflight(project_id: UUID, run: RunAccess):
    active_run(project_id, run)
    key=deployment_credential(project_id)
    if key is None:
        from smd_owner_client import owner_json
        owner_json(project_id,"codex-models")
    else:
        verify_provider_key(key)
    try:
        bearer = secrets.token_urlsafe(32)
        queue().set("smartdev:codex-bearer:" + bearer,
                    json.dumps([str(project_id), run.model_dump(mode="json")]), ex=24 * 60 * 60)
        return {"bearer": bearer}
    except RedisError:
        raise HTTPException(503, "Codex authentication check is temporarily unavailable.") from None


def authorize_call(project_id, authorization):
    match = CAPABILITY.fullmatch(authorization)
    if not match:
        raise HTTPException(401, "Managed Codex authentication required.")
    try:
        stored = queue().get("smartdev:codex-bearer:" + match[1])
    except RedisError:
        raise HTTPException(503, "Codex authorization is temporarily unavailable.") from None
    if not stored:
        raise HTTPException(401, "Managed Codex authentication expired.")
    project, run = json.loads(stored)
    if project != str(project_id):
        raise HTTPException(403, "Codex session belongs to another project.")
    active_run(project_id, RunAccess.model_validate(run))
    return deployment_credential(project_id)


@router.post("/workspace-memory/{project_id}/codex/v1/responses")
async def model_response(project_id: UUID, request: Request):
    key = await run_in_threadpool(authorize_call, project_id, request.headers.get("authorization", ""))
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
