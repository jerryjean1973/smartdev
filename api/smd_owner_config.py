"""Authorize owner-backed operations against the current tenant on every use."""
import base64
import json
import time
from uuid import UUID

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from integration_registry import PROVIDERS, probe
from smd_owner_client import signed_message
from smd_receipts import decode
from store import database
from vault import call

router = APIRouter()
MAX_BODY = 32 * 1024 * 1024


class Identity(BaseModel, extra="forbid"):
    instance_id: UUID
    public_key: str = Field(min_length=44,max_length=44)
    version: int = Field(ge=0)


def bind(db, task, instance, key, version=None):
    row = db.execute("""SELECT a.version,a.tenant_id=smd_admin_tenant_id() AS owner
                        FROM smd_deployment_assignment a WHERE task_id=%s FOR UPDATE""",(task,)).fetchone()
    if not row or version is not None and row["version"]!=version:
        raise HTTPException(409, "Assignment changed during enrollment.")
    saved = db.execute("""INSERT INTO smd_install_identity(task_id,instance_id,public_key,owner_version)
        VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING task_id""",
        (task,instance,key,row["version"] if row["owner"] else None)).fetchone()
    if saved:
        db.execute("""INSERT INTO smd_deployment_event(task_id,kind,label) VALUES
                   (%s,'configuration','Enrolled installation configuration identity')""",(task,))
    else:
        matching = db.execute("""SELECT 1 FROM smd_install_identity WHERE task_id=%s AND instance_id=%s
                              AND public_key=%s""",(task,instance,key)).fetchone()
        if not matching:
            raise HTTPException(409,"A different installation identity is registered.")


@router.put("/admin/compute/deployments/{task}/configuration-identity")
def register_existing(task:UUID, body:Identity):
    with database() as db:
        current=db.execute("""SELECT 1 FROM smd_deploy_task t JOIN smd_deploy_receipt r
          ON r.task_id=t.id AND r.instance_id=t.instance_id AND r.verified_at IS NOT NULL
          WHERE t.id=%s AND t.instance_id=%s AND t.status='deployed' AND t.reconciled_at IS NULL FOR SHARE OF t""",
          (task,body.instance_id)).fetchone()
        if not current:
            raise HTTPException(409,"A verified active installation is required.")
        bind(db,task,body.instance_id,decode(body.public_key,32),body.version)
    return {"enrolled":True}


def identity(db, task, project, instance, operation, timestamp, nonce, signature, body):
    allowed={"portal-generation","portal-auth","domain-challenge","domains","domain-report","catalog",
             "codex-models","codex-responses","claude-models","claude-messages","claude-count-tokens",
             "model","tenant-bindings","tenant-secret","deployment-access"}
    allowed.update("verify-"+k for k,v in PROVIDERS.items() if v.endpoint)
    if operation not in allowed:
        raise HTTPException(422,"Unknown configuration operation.")
    if abs(time.time()-timestamp)>90:
        raise HTTPException(403,"Installation request expired.")
    row=db.execute("""SELECT i.public_key,a.version,a.tenant_id,a.tenant_id=smd_admin_tenant_id() AS admin_default,
          (a.tenant_id=smd_admin_tenant_id() AND i.owner_version=a.version) IS TRUE AS owner
        FROM smd_deployment_assignment a JOIN smd_deploy_task t ON t.id=a.task_id
        JOIN smd_install_identity i ON i.task_id=t.id AND i.instance_id=t.instance_id
        JOIN smd_deploy_receipt r ON r.task_id=t.id AND r.instance_id=t.instance_id AND r.verified_at IS NOT NULL
        JOIN projects p ON p.id=%s AND NOT p.archived
        WHERE t.id=%s AND t.instance_id=%s AND t.status='deployed' AND t.reconciled_at IS NULL
        AND EXISTS (SELECT 1 FROM jsonb_array_elements(t.project_manifest) m WHERE m->>'id'=p.id::text)
        FOR SHARE OF a,t""",(project,task,instance)).fetchone()
    if not row:
        raise HTTPException(403,"Installation unavailable.")
    try:
        Ed25519PublicKey.from_public_bytes(row["public_key"]).verify(signature,
            signed_message(task,instance,project,operation,timestamp,nonce,body))
    except (InvalidSignature,ValueError):
        raise HTTPException(403,"Invalid installation signature.",headers={"X-SMD-Identity":"superseded"}) from None
    if operation not in {"portal-generation","portal-auth","domain-challenge","domains","domain-report"} and row["admin_default"] and not row["owner"]:
        raise HTTPException(403,"Owner configuration access has ended.")
    # Commit replay protection separately, including on provider errors and disconnected streams.
    with database() as replay:
        replay.execute("DELETE FROM smd_config_nonce WHERE used_at<now()-interval '5 minutes'")
        if not replay.execute("""INSERT INTO smd_config_nonce(task_id,nonce) VALUES(%s,%s)
                          ON CONFLICT DO NOTHING RETURNING nonce""",(task,nonce)).fetchone():
            raise HTTPException(409,"Installation request already processed.")
        if operation not in {"portal-generation","portal-auth","domains","domain-challenge","domain-report"}:
            replay.execute("INSERT INTO audit(project_id,action) VALUES (%s,%s)",
                           (project,"smd:"+("owner" if row["owner"] else "tenant")+":"+str(task)+":"+operation))
    return row


def catalog(db, project):
    roles=db.execute("""SELECT scope,role,provider,model FROM integration_ai_role
                     WHERE scope IN ('global',%s) ORDER BY scope,role""",(str(project),)).fetchall()
    providers=db.execute("""SELECT provider,status FROM integration_connections WHERE status IN ('CONNECTED','OPERATIONAL')
                         ORDER BY provider""").fetchall()
    bindings=db.execute("""SELECT environment,secret_bindings FROM project_runtime WHERE project_id=%s
                         ORDER BY environment""",(project,)).fetchall()
    return {"roles":roles,"providers":providers,"bindings":{r["environment"]:sorted(r["secret_bindings"]) for r in bindings}}


def project_openai(db,project,state):
    from smd_tenant_access import tenant_value
    assigned=tenant_value(db,state["tenant_id"],"DEV","OPENAI_API_KEY")
    if assigned or not state["owner"]:
        return assigned
    bindings=db.execute("SELECT secret_bindings FROM project_runtime WHERE project_id=%s AND environment='DEV'",(project,)).fetchone()
    name=((bindings or {}).get("secret_bindings") or {}).get("OPENAI_API_KEY","OPENAI_API_KEY")
    secret=call("GET",f"data/projects/{project}/DEV/{name}",missing=True)
    value=((secret or {}).get("data",{}).get("data") or {}).get("value")
    if not value:
        secret=call("GET","data/integrations/openai",missing=True)
        value=((secret or {}).get("data",{}).get("data") or {}).get("credential")
    from codex_access import validate_key
    return validate_key(value) if value else None


def project_anthropic(db,project,state):
    value=runtime_value(db,project,state,"DEV","ANTHROPIC_API_KEY")
    if not value and state["owner"]:
        secret=call("GET",f"data/projects/{project}/DEV/ANTHROPIC_API_KEY",missing=True)
        value=((secret or {}).get("data",{}).get("data") or {}).get("value")
    if not value and state["owner"]:
        secret=call("GET","data/integrations/anthropic",missing=True)
        value=((secret or {}).get("data",{}).get("data") or {}).get("credential")
    if not isinstance(value,str) or not 16<=len(value.strip())<=4096:
        return None
    return value.strip()


def runtime_defaults(db, project, state):
    from smd_tenant_access import tenant_defaults
    entries = tenant_defaults(db, state["tenant_id"])
    if not state["owner"]:
        return entries
    names = {(entry["environment"], entry["name"]) for entry in entries}
    for row in db.execute("SELECT environment,secret_bindings FROM project_runtime WHERE project_id=%s", (project,)):
        for target in row["secret_bindings"]:
            if (row["environment"], target) not in names:
                entries.append({"environment": row["environment"], "name": target})
    return entries


def runtime_value(db, project, state, env, name):
    from smd_tenant_access import tenant_value
    value = tenant_value(db, state["tenant_id"], env, name)
    if value is not None or not state["owner"]:
        return value
    row = db.execute("SELECT secret_bindings FROM project_runtime WHERE project_id=%s AND environment=%s",
                     (project, env)).fetchone()
    source = (row or {}).get("secret_bindings", {}).get(name)
    if source:
        stored = call("GET", f"data/projects/{project}/{env}/{source}", missing=True)
        return ((stored or {}).get("data", {}).get("data") or {}).get("value")
    return None


def operation_result(db,task,project,operation,state,body):
    from smd_preview_domains import read_report, write_report
    from smd_tenant_access import login_check,tenant_defaults,tenant_value
    if operation=="portal-generation":
        return {"version":state["version"]}
    if operation=="deployment-access":
        row=db.execute("""SELECT c.name,r.connections FROM command_center_tenant c
            JOIN smd_deploy_task t ON t.id=%s JOIN smd_deploy_receipt r
              ON (r.task_id,r.instance_id)=(t.id,t.instance_id) AND r.verified_at IS NOT NULL
            WHERE c.id=%s""",(task,state["tenant_id"])).fetchone()
        links=(row or {}).get("connections") or {}
        if not links.get("portal_url") or not links.get("coder_url"):
            raise HTTPException(409,"Verified deployment connections are unavailable.")
        return {"tenant":row["name"],"portal_url":links["portal_url"],"coder_url":links["coder_url"],
                "apps":[item for item in links.get("apps",[]) if item.get("project_id")==str(project)]}
    if operation=="portal-auth":
        return login_check(task,state,body)
    if operation=="domain-challenge":
        from smd_preview_domains import reserve_challenge
        return reserve_challenge(db,task,project,state["version"],body)
    if operation=="tenant-secret":
        from smd_tenant_access import default_path
        try:
            spec=json.loads(body)
            name,env=spec["name"],spec["environment"]
            default_path(state["tenant_id"],env,name)
            if spec.get("version",state["version"])!=state["version"]:
                raise HTTPException(409,"Tenant generation changed.")
        except (ValueError,TypeError,KeyError):
            raise HTTPException(422,"Invalid tenant binding.") from None
        value=runtime_value(db,project,state,env,name)
        return {"present":True,"value":value} if value is not None else {"present":False}
    if operation=="tenant-bindings":
        return {"names":runtime_defaults(db,project,state)}
    if operation=="domains":
        return read_report(db,task,project,state["version"])
    if operation=="domain-report":
        return write_report(db,task,project,state["version"],body)
    if operation=="model":
        from inference import run_model,ProviderFailure
        try:
            req=json.loads(body)
            role=req["role"]
            if role not in ("challenger","prompter","reviewer"):
                raise ValueError
            instructions,prompt=req["instructions"],req["prompt"]
            if not all(isinstance(v,str) and 0<len(v)<=180000 for v in (instructions,prompt)):
                raise ValueError
        except (ValueError,KeyError,TypeError):
            raise HTTPException(422,"Invalid role request.") from None
        from challenge_engine import provider_config
        chosen=db.execute("""SELECT provider,model FROM integration_ai_role WHERE scope IN ('global',%s)
                            AND role=%s ORDER BY (scope=%s) DESC LIMIT 1""",(str(project),role,str(project))).fetchone()
        if not state["owner"]:
            chosen=None
        provider,model=(chosen["provider"],chosen["model"]) if chosen else (provider_config(role,project) if state["owner"] else (("openai","gpt-5.4") if role=="challenger" else ("anthropic","claude-sonnet-4-6")))
        if provider not in {"openai","anthropic"}:
            raise HTTPException(409,"Configure the role provider for this deployment.")
        if provider=="openai":
            key=project_openai(db,project,state)
        else:
            key=runtime_value(db,project,state,"DEV","ANTHROPIC_API_KEY")
            if not key and state["owner"]:
                secret=call("GET",f"data/projects/{project}/DEV/ANTHROPIC_API_KEY",missing=True)
                key=((secret or {}).get("data",{}).get("data") or {}).get("value")
            if not key and state["owner"]:
                secret=call("GET","data/integrations/anthropic",missing=True)
                key=((secret or {}).get("data",{}).get("data") or {}).get("credential")
        if not key:
            raise HTTPException(409,"Configure the role provider for the project.")
        try:
            output,usage=run_model(provider,model,key,instructions,prompt,limit=6000,structured=True)
        except ProviderFailure:
            raise HTTPException(502,"Role provider could not complete the request.") from None
        return {"output":output,"usage":usage}
    if operation=="catalog":
        if state["owner"]:
            return {**catalog(db,project),"defaults":runtime_defaults(db,project,state)}
        return {"roles":[],"providers":[],"defaults":tenant_defaults(db,state["tenant_id"])}
    if operation.startswith("verify-"):
        provider=operation.removeprefix("verify-")
        if provider in PROVIDERS and PROVIDERS[provider].endpoint:
            value=runtime_value(db,project,state,"DEV",provider.upper()+"_API_KEY")
            if not value and state["owner"]:
                secret=call("GET","data/integrations/"+provider,missing=True)
                value=((secret or {}).get("data",{}).get("data") or {}).get("credential")
            if not value:
                raise HTTPException(409,"Integration is unconfigured.")
            result=probe(provider,value)
            return {"status":result.status,"message":result.message}
    if operation=="codex-models":
        from codex_access import verify_provider_key
        key=project_openai(db,project,state)
        if not key:
            raise HTTPException(409,"Configure the project OpenAI integration.")
        verify_provider_key(key)
        return {"ready":True}
    if operation=="claude-models":
        from codex_access import verify_anthropic_key
        key=project_anthropic(db,project,state)
        if not key:
            raise HTTPException(409,"Configure the project Anthropic integration.")
        verify_anthropic_key(key)
        return {"ready":True}
    raise HTTPException(422,"Unknown configuration operation.")


@router.post("/smd-deploy-tasks/{task}/configuration")
async def configure(task: UUID,request:Request):
    h=request.headers
    try:
        project,instance,nonce=(UUID(h["x-smd-"+k]) for k in ("project","instance","nonce"))
        operation=h["x-smd-operation"]
        timestamp=int(h["x-smd-time"])
        signature=decode(h["x-smd-signature"],64)
    except (KeyError,ValueError):
        raise HTTPException(403,"Installation authentication required.") from None
    data=bytearray()
    async for chunk in request.stream():
        if len(data)+len(chunk)>MAX_BODY:
            raise HTTPException(413,"Configuration request too large.")
        data.extend(chunk)
    body=bytes(data)
    db=await run_in_threadpool(database)
    client=upstream=None
    streaming=False
    try:
        state=await run_in_threadpool(identity,db,task,project,instance,operation,timestamp,nonce,signature,body)
        streaming_operations={"codex-responses","claude-messages","claude-count-tokens"}
        if operation not in streaming_operations:
            result=await run_in_threadpool(operation_result,db,task,project,operation,state,body)
            await run_in_threadpool(db.commit)
            from fastapi.responses import JSONResponse
            return JSONResponse(result,headers={"Cache-Control":"no-store"})
        if operation=="codex-responses":
            key=await run_in_threadpool(project_openai,db,project,state)
            if not key:
                raise HTTPException(409,"Configure the project OpenAI integration.")
            endpoint="https://api.openai.com/v1/responses"
            upstream_body=body
            headers={"Authorization":"Bearer "+key,"Content-Type":"application/json"}
            from codex_access import check_code as provider_check
        else:
            key=await run_in_threadpool(project_anthropic,db,project,state)
            if not key:
                raise HTTPException(409,"Configure the project Anthropic integration.")
            try:
                envelope=json.loads(body)
                upstream_body=base64.b64decode(envelope["body"],validate=True)
                version=envelope.get("anthropic_version") or "2023-06-01"
                beta=envelope.get("anthropic_beta") or ""
                if not isinstance(version,str) or not isinstance(beta,str) or len(version)>100 or len(beta)>2000:
                    raise ValueError
            except (ValueError,TypeError,KeyError):
                raise HTTPException(422,"Invalid Claude Code provider request.") from None
            endpoint="https://api.anthropic.com/v1/messages"+("/count_tokens" if operation=="claude-count-tokens" else "")
            headers={"x-api-key":key,"anthropic-version":version,"Content-Type":"application/json"}
            if beta:
                headers["anthropic-beta"]=beta
            from codex_access import check_anthropic_code as provider_check
        client=httpx.AsyncClient(trust_env=False,follow_redirects=False,timeout=httpx.Timeout(180,connect=8))
        upstream=await client.send(client.build_request("POST",endpoint,
            content=upstream_body,headers=headers),stream=True)
        provider_check(upstream.status_code)
        await run_in_threadpool(db.commit)
        async def stream():
            try:
                async for part in upstream.aiter_bytes():
                    yield part
            finally:
                db.close()
                await upstream.aclose()
                await client.aclose()
        streaming=True
        return StreamingResponse(stream(),media_type=upstream.headers.get("content-type","application/json"),
                                 headers={"Cache-Control":"no-store","X-Accel-Buffering":"no"})
    except httpx.RequestError:
        raise HTTPException(503,"Provider temporarily unavailable.") from None
    finally:
        if not streaming:
            db.close()
            if upstream: await upstream.aclose()
            if client: await client.aclose()
