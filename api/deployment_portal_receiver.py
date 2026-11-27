"""Managed deployment entry point. Only the registered issuer can initiate owner sign-in."""
import os
import json
import re
import time
from secrets import token_urlsafe
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
import httpx
from starlette.concurrency import run_in_threadpool

from core import issue_session, valid_session
from store import database
from main import app as native
from portal_handoff import ISSUER, PROTOCOL, RECEIVER, TOKEN, TTL, challenge, fields, post_page, require_origin

receiver = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
portal = os.environ["PORTAL_ORIGIN"].rstrip("/")
issuer = os.environ["SMD_PORTAL_ISSUER"]
task = str(UUID(os.environ["SMD_PORTAL_TASK"]))
instance = str(UUID(os.environ["SMD_PORTAL_INSTANCE"]))
if (issuer == portal or any(urlsplit(url).scheme != "https" or url != "https://" + urlsplit(url).netloc
                            or not urlsplit(url).hostname or urlsplit(url).username is not None for url in (issuer, portal))):
    raise ValueError("Portal and issuer require separate HTTPS origins.")
identity = {"portal": portal, "task_id": task, "instance_id": instance, "account": "owner"}
prefix = RECEIVER.removeprefix("/api")


def browser_cookie(value):
    try:
        return "__Host-smd-login-" + UUID(value).hex
    except ValueError:
        raise HTTPException(400, "Invalid portal sign-in request.") from None


@receiver.get(prefix + "/metadata")
def metadata():
    return {**identity, "issuer": issuer, "protocol": PROTOCOL}


@receiver.post(prefix + "/start")
async def start(request: Request):
    require_origin(request, issuer)
    form = await fields(request, "flow")
    cookie = browser_cookie(form["flow"])
    verifier = token_urlsafe(32)
    response = post_page(issuer + ISSUER + "/return", **form, challenge=challenge(verifier))
    response.set_cookie(cookie, verifier, secure=True, httponly=True, samesite="strict", max_age=TTL)
    return response


@receiver.post(prefix + "/return")
async def return_to_portal(request: Request):
    require_origin(request, issuer)
    form = await fields(request, "flow", "code")
    browser_cookie(form["flow"])
    if not TOKEN.fullmatch(form["code"]):
        raise HTTPException(400, "Invalid portal sign-in request.")
    return post_page(portal + RECEIVER + "/finish", **form)


def exchange(form, verifier):
    try:
        with httpx.Client(trust_env=False, timeout=8) as client:
            result = client.post(issuer + ISSUER + "/redeem",
                                 json={**form, "task_id": task, "instance_id": instance, "verifier": verifier})
        if result.status_code == 200:
            data = result.json()
            if isinstance(data, dict) and all(data.get(k) == v for k, v in identity.items()):
                ttl = min(28800, int(data["session_expires"]) - int(time.time()))
                if ttl > 0:
                    return ttl
    except (httpx.HTTPError, ValueError, TypeError, KeyError):
        pass
    raise HTTPException(403, "Portal sign-in could not be completed. Open it again from Command Center.")


@receiver.post(prefix + "/finish")
async def finish(request: Request):
    require_origin(request, portal)
    form = await fields(request, "flow", "code")
    cookie = browser_cookie(form["flow"])
    verifier = request.cookies.get(cookie, "")
    if not TOKEN.fullmatch(verifier) or not TOKEN.fullmatch(form["code"]):
        raise HTTPException(403, "This browser has no matching portal sign-in.")
    ttl = await run_in_threadpool(exchange, form, verifier)
    response = RedirectResponse(portal + "/", status_code=303)
    response.delete_cookie(cookie, secure=True, httponly=True, samesite="strict")
    session = issue_session(bytes.fromhex(os.environ["SESSION_KEY"]), now=int(time.time()) + ttl - 28800)
    from smd_portal_generation import set_account
    set_account(response,session,"owner",ttl)
    response.set_cookie("__Host-command", session, secure=True, httponly=True, samesite="strict", max_age=ttl)
    return response


@receiver.post(prefix+"/assignment")
async def prepare_assignment(request:Request):
    from smd_portal_generation import stage
    raw=bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw)>2048:
            raise HTTPException(413,"Assignment request too large.")
    try:
        data=json.loads(raw)
    except ValueError:
        raise HTTPException(403,"Invalid assignment authorization.") from None
    return await run_in_threadpool(stage,data)


def tenant_projects():
    return {str(UUID(p)) for p in os.getenv("SMD_PORTAL_PROJECTS","").split(",") if p}


def tenant_board(path, projects, requested=None):
    allowed = [UUID(p) for p in projects if not requested or p==requested]
    if not allowed:
        return []
    with database() as db:
        if path == "/taskboard/business-objectives":
            return db.execute("""SELECT b.* FROM business_objective b WHERE EXISTS (
                SELECT 1 FROM objective_contract c WHERE c.project_id=ANY(%s)
                AND substring(c.business_objective from '^BO-[0-9]+')=b.id) ORDER BY b.execution_order""",(allowed,)).fetchall()
        if not db.execute("SELECT to_regclass('mcp_task_request') AS t").fetchone()["t"]:
            return []
        import mcp_request_history as history
        return db.execute("SELECT "+history.COLUMNS+history.SOURCE+" AND r.project_id=ANY(%s) ORDER BY r.received_at DESC",(allowed,)).fetchall()


async def tenant_api(scope,receive,send):
    from fastapi.encoders import jsonable_encoder
    request = Request(scope, receive)
    path, method = scope["path"], scope["method"]
    projects = tenant_projects()
    from smd_portal_generation import MARK,tenant_login,valid_account
    if path=="/login" and method=="POST" and os.getenv("SMD_ASSIGNMENT_AUTHORITY"):
        try:
            raw=bytearray()
            async for chunk in request.stream():
                if len(raw)+len(chunk)>4096:
                    raise ValueError
                raw.extend(chunk)
            value=json.loads(raw)["password"]
            response=await run_in_threadpool(tenant_login,value)
        except (ValueError,TypeError,KeyError):
            response=JSONResponse({"detail":"Invalid password."},401)
        except HTTPException as exc:
            response=JSONResponse({"detail":exc.detail},exc.status_code)
        return await response(scope,receive,send)
    session=request.cookies.get("__Host-command","")
    if session and path not in {"/health","/logout","/login"} and os.getenv("SMD_ASSIGNMENT_AUTHORITY"):
        try:
            allowed=await run_in_threadpool(valid_account,session,request.cookies.get(MARK,""))
        except HTTPException:
            allowed=False
        if not allowed:
            return await JSONResponse({"detail":"Portal assignment changed. Sign in again."},401)(scope,receive,send)
    hidden = JSONResponse({"detail":"Not found."},404)
    if path in ("/health","/login","/logout","/coder-session","/__smartdev/session"):
        return await native(scope,receive,send)
    if path in ("/taskboard/business-objectives","/taskboard/mcp-requests") and method=="GET":
        if not valid_session(request.cookies.get("__Host-command",""),bytes.fromhex(os.environ["SESSION_KEY"])):
            return await JSONResponse({"detail":"Authentication required"},401)(scope,receive,send)
        rows=await run_in_threadpool(tenant_board,path,projects,request.query_params.get("project_id"))
        return await JSONResponse(jsonable_encoder(rows),headers={"Cache-Control":"no-store"})(scope,receive,send)
    if path in ("/projects","/task-navigation") and method=="GET":
        messages=[]
        async def collect(message):
            messages.append(message)
        await native(scope,receive,collect)
        status=messages[0]["status"]
        if status!=200:
            for message in messages:
                await send(message)
            return
        rows=json.loads(b"".join(m.get("body",b"") for m in messages[1:]))
        column="id" if path=="/projects" else "project_id"
        rows=[r for r in rows if isinstance(r,dict) and str(r.get(column)) in projects]
        return await JSONResponse(rows,headers={"Cache-Control":"no-store","X-SmartDev-Portal":"tenant"})(scope,receive,send)
    if path.startswith("/taskboard/mcp-requests/") and method=="GET":
        if request.query_params.get("project_id") in projects:
            return await native(scope,receive,send)
    scoped = re.fullmatch(r"/(workspace-memory|projects)/([a-f0-9-]{36})(?:/([^/]+)(?:/.*)?)?",path)
    if scoped and scoped[2] in projects:
        if scoped[1]=="workspace-memory" and path==f"/workspace-memory/{scoped[2]}/deployment-access" and method=="POST":
            from core import project_token
            from secrets import compare_digest
            from smd_owner_client import owner_json
            expected="Bearer "+project_token(bytes.fromhex(os.environ["MEMORY_ACCESS_KEY"]),UUID(scoped[2]))
            if not compare_digest(request.headers.get("authorization","").encode(),expected.encode()):
                return await JSONResponse({"detail":"Workspace authorization required."},401)(scope,receive,send)
            try:
                body=bytearray()
                async for chunk in request.stream():
                    if len(body)+len(chunk)>256:
                        raise ValueError
                    body.extend(chunk)
                if json.loads(body)!={"deployment_id":task}:
                    return await hidden(scope,receive,send)
                data=await run_in_threadpool(owner_json,scoped[2],"deployment-access")
            except ValueError:
                return await JSONResponse({"detail":"Invalid deployment identifier."},422)(scope,receive,send)
            except HTTPException as exc:
                return await JSONResponse({"detail":exc.detail},exc.status_code)(scope,receive,send)
            return await JSONResponse(data,headers={"Cache-Control":"no-store"})(scope,receive,send)
        verify=re.fullmatch(r"/projects/[a-f0-9-]{36}/integrations/inherited/([a-z][a-z0-9-]{0,32})/verify",path)
        if scoped[1]=="projects" and ((path.endswith("/integrations/inherited") and method=="GET") or (verify and method=="POST")):
            if not valid_session(request.cookies.get("__Host-command",""),bytes.fromhex(os.environ["SESSION_KEY"])):
                return await JSONResponse({"detail":"Authentication required"},401)(scope,receive,send)
            if method=="POST" and request.headers.get("origin")!=portal:
                return await JSONResponse({"detail":"Use this deployment portal."},403)(scope,receive,send)
            from smd_owner_client import owner_json
            try:
                data=await run_in_threadpool(owner_json,scoped[2],"verify-"+verify[1] if verify else "catalog")
            except HTTPException as exc:
                return await JSONResponse({"detail":exc.detail},exc.status_code)(scope,receive,send)
            return await JSONResponse(data,headers={"Cache-Control":"no-store"})(scope,receive,send)
        if scoped[1]=="workspace-memory" or scoped[3] in {
            "preview","domains","secrets","development","objectives","tasks","mcp-tasks","mcp-requests","conversations","runtime","integrations","memory","evidence","activity"}:
            return await native(scope,receive,send)
    return await hidden(scope,receive,send)


async def app(scope, receive, send):
    # Uvicorn still delegates the native API's full lifespan and all normal routes to its original app.
    if scope["type"] == "http" and scope["path"].startswith(prefix + "/"):
        async def no_cache(message):
            if message["type"] == "http.response.start":
                message["headers"] = [(k, v) for k, v in message["headers"] if k.lower() != b"cache-control"]
                message["headers"].append((b"cache-control", b"no-store"))
            await send(message)
        return await receiver(scope, receive, no_cache)
    if scope["type"]=="http":
        return await tenant_api(scope,receive,send)
    return await native(scope, receive, send)
