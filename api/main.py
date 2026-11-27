import logging
import os
import re
import secrets
from uuid import UUID, uuid4
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Literal

from starlette.concurrency import run_in_threadpool
from acceptance import fixture_binding
from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator

from core import REPOSITORY, issue_session, password_digest, project_token, valid_session
from store import database, notify, queue
from mcp_request_history import router as mcp_request_history_router
from codex_access import router as codex_access_router
from coder_session import BRIDGE, revoke, router as coder_session_router
from development import router as development_router
from cc_codex import router as cc_codex_router
from coder_inventory import router as coder_inventory_router
from smd_deployment import router as smd_deployment_router
from deployment_portal_access import PUBLIC_POST as PORTAL_PUBLIC_POST, router as deployment_portal_router
from smd_receipts import router as smd_receipts_router
from smd_git_tokens import router as smd_git_tokens_router
from smd_release_assets import ASSET_ROUTE, router as smd_release_assets_router
from smd_stack_export import router as smd_stack_export_router
from conversations import router as conversations_router
from runtime import router as runtime_router
from project_mcp import router as project_mcp_router
from preview import router as preview_router
from publishing import AUTH_PATH as DEV_GATEWAY_AUTH, router as publishing_router
from domains import router as domains_router
from github import router as github_router
from governance import router as governance_router
from memory import router as memory_router
from vault import router as vault_router

@asynccontextmanager
async def lifespan(app):
    from acceptance import acceptance_projects
    acceptance_projects()
    with database() as db:
        for migration in sorted(Path(__file__).parent.glob("[0-9]*.sql")):
            db.execute(migration.read_text())
        from task_titles import backfill_titles
        backfill_titles(db)
    yield

app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
origin = os.environ["PORTAL_ORIGIN"].rstrip("/")
coding_origin = (os.getenv("CODER_PUBLIC_URL", "").rstrip("/")
                 if os.getenv("SMARTDEV_ENABLE_CODING_HOST") == "1" else "")
session_key = bytes.fromhex(os.environ["SESSION_KEY"])
memory_key = bytes.fromhex(os.environ["MEMORY_ACCESS_KEY"])
salt, stored_digest = os.environ["OWNER_PASSWORD_HASH"].split(":")
if len(memory_key) < 32 or len(session_key) < 32 or not origin.startswith("https://"):
    raise ValueError("Configure HTTPS and a 32-byte session key")

def authorize_acceptance_route(project_id, request):
    with database() as db:
        row = db.execute("""SELECT p.status,p.workspace_url,f.conversation_id,f.token_digest,f.closed_at
            FROM projects p JOIN acceptance_fixture f ON f.project_id=p.id
            WHERE p.id=%s AND NOT p.archived""", (project_id,)).fetchone()
        return fixture_binding(project_id, row, request.headers.get("x-smartdev-acceptance"),
                               request.headers.get("x-smartdev-fixture-conversation"),
                               request.headers.get("x-smartdev-fixture-workspace"))


@app.middleware("http")
async def protect(request: Request, call_next):
    from starlette.responses import JSONResponse
    if request.method == "POST" and request.url.path in PORTAL_PUBLIC_POST:
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response
    if request.url.path == "/mcp":
        caller_origin = request.headers.get("origin")
        allowed = {origin, "https://chatgpt.com"}
        if caller_origin is not None and caller_origin not in allowed:
            return JSONResponse({"detail": "Invalid request origin."}, 403)
        if request.method == "OPTIONS":
            if os.getenv("SMARTDEV_EXTERNAL_MCP_ENABLED") != "1":
                return JSONResponse({"detail": "Not found."}, 404)
            response = Response(status_code=204)
        else:
            response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        if caller_origin in allowed:
            response.headers["Access-Control-Allow-Origin"] = caller_origin
            response.headers["Vary"] = "Origin"
            response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type, MCP-Protocol-Version, MCP-Session-Id"
            response.headers["Access-Control-Expose-Headers"] = "WWW-Authenticate"
        return response
    if request.url.path in {"/oauth/token", "/.well-known/oauth-protected-resource",
                             "/.well-known/oauth-authorization-server"} or (
                                 request.url.path == "/oauth/authorize" and request.method == "GET"):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response
    if request.method == "POST" and request.url.path in {
        "/operator-machine/poll", "/operator-machine/result",
        "/operator-machine/task-poll", "/operator-machine/task-result"
    }:
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response
    scoped = re.fullmatch(r"/workspace-memory/([a-f0-9-]{36})(?:/governance|/codex-access|/conversation|/preview-runtime|/mcp-bindings|/operator-task|/screenshots|/git-credential|/deployment-access)?", request.url.path)
    if "x-smartdev-acceptance" in request.headers and not (scoped and request.url.path.endswith("/conversation")):
        acceptance_scope = re.fullmatch(
            r"/projects/([a-f0-9-]{36})/(?:objectives|preview|development|dev-publishing)(?:/[^/]+){0,2}"
            r"|/workspace-memory/([a-f0-9-]{36})/governance", request.url.path)
        try:
            candidate = UUID(acceptance_scope[1] or acceptance_scope[2]) if acceptance_scope else None
        except ValueError:
            candidate = None
        if not candidate or not await run_in_threadpool(authorize_acceptance_route, candidate, request):
            return JSONResponse({"detail": "Acceptance fixture binding is invalid."}, 403)
    if scoped and request.method == "POST":
        try:
            project_id = UUID(scoped[1])
        except ValueError:
            return JSONResponse({"detail": "Invalid project"}, 404)
        supplied = request.headers.get("authorization", "")
        expected = "Bearer " + project_token(memory_key, project_id)
        if not secrets.compare_digest(supplied.encode(), expected.encode()):
            return JSONResponse({"detail": "Workspace authorization required"}, 401)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response
    if re.fullmatch(r"/workspace-memory/[a-f0-9-]{36}/mcp/[a-z][a-z0-9_]{0,47}", request.url.path):
        return await call_next(request)
    if request.method == "POST" and re.fullmatch(
        r"/workspace-memory/[a-f0-9-]{36}/codex/v1/responses", request.url.path
    ):
        return await call_next(request)
    if request.url.path == BRIDGE and request.method in ("OPTIONS", "POST"):
        return await call_next(request)
    if request.method == "GET" and re.fullmatch(ASSET_ROUTE, request.url.path):
        return await call_next(request)
    if request.method == "POST" and re.fullmatch(r"/smd-deploy-tasks/[a-f0-9-]{36}/(?:receipt|git-token)", request.url.path):
        return await call_next(request)
    expected_origin = coding_origin if coding_origin and request.headers.get("host") == coding_origin[8:] else origin
    if request.method not in ("GET", "HEAD") and request.headers.get("origin") != expected_origin:
        return JSONResponse({"detail": "Invalid request origin"}, 403)
    if request.url.path not in ("/health", "/login", DEV_GATEWAY_AUTH):
        if not valid_session(request.cookies.get("__Host-command", ""), session_key):
            return JSONResponse({"detail": "Sign in required"}, 401)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response

@app.middleware("http")
async def external_connection_diagnostics(request: Request, call_next):
    response = await call_next(request)
    if request.url.path in ("/mcp", "/oauth/authorize", "/oauth/token"):
        logging.getLogger("uvicorn.error").info(
            "SmartDev external connection %s %s status=%d",
            request.method, request.url.path, response.status_code,
        )
    return response


class Login(BaseModel):
    password: str = Field(max_length=512)

@app.post("/login")
def login(body: Login, response: Response):
    with queue().pipeline() as pipe:
        attempts, _ = pipe.incr("owner-login-attempts").expire("owner-login-attempts", 60, nx=True).execute()
    if attempts > 12:
        raise HTTPException(429, "Too many attempts. Try again in a minute.")
    if not secrets.compare_digest(password_digest(body.password, bytes.fromhex(salt)), stored_digest):
        raise HTTPException(401, "Invalid password")
    response.set_cookie("__Host-command", issue_session(session_key), secure=True,
                        httponly=True, samesite="strict", max_age=28800)
    return {"ok": True}

@app.post("/logout")
def logout(request: Request, response: Response):
    revoke(request.cookies.get("__Host-command"))
    response.delete_cookie("__Host-command", secure=True, httponly=True, samesite="strict")
    return {"ok": True}

@app.get("/health")
def health():
    with database() as db:
        db.execute("SELECT mode FROM conversation_execution LIMIT 0")
        db.execute("SELECT released, environment FROM conversation_submission LIMIT 0")
    return {"ok": True, "main_execution_protocol": "workspace-write-v1", "main_reply_protocol": "resume-execution-v1", "acceptance_protocol": "fixture-bound-v2"}

class Project(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    repository: str = Field(default="", max_length=140)
    runtime: Literal["node", "python", "other"] = "node"

    @field_validator("name")
    @classmethod
    def validate_name(cls, value):
        if not value.strip():
            raise ValueError("Enter a project name")
        return value.strip()

    @field_validator("repository")
    @classmethod
    def validate_repository(cls, value):
        value = value.strip()
        if value and not REPOSITORY.fullmatch(value):
            raise ValueError("Enter a GitHub repository as owner/repo")
        return value

@app.get("/projects")
def projects():
    with database() as db:
        return db.execute("SELECT p.* FROM projects p WHERE NOT EXISTS (SELECT 1 FROM acceptance_fixture f WHERE f.project_id=p.id) ORDER BY p.created_at DESC").fetchall()

class TenantAssignment(BaseModel, extra="forbid"):
    tenant_name: str = Field(max_length=80)


@app.get("/admin/project-tenants")
def project_tenants():
    with database() as db:
        rows = db.execute("SELECT project_id, tenant_name FROM command_center_project_tenant").fetchall()
    return {str(row["project_id"]): row["tenant_name"] for row in rows}


@app.put("/admin/project-tenants/{project_id}")
def assign_project_tenant(project_id: UUID, body: TenantAssignment):
    name = body.tenant_name.strip()
    with database() as db:
        row = db.execute("""
            SELECT id FROM projects WHERE id=%s AND NOT EXISTS (
                SELECT 1 FROM acceptance_fixture WHERE project_id=projects.id
            ) FOR UPDATE
        """, (project_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Project not available.")
        if name:
            db.execute("""
                INSERT INTO command_center_project_tenant (project_id, tenant_name)
                VALUES (%s, %s) ON CONFLICT (project_id)
                DO UPDATE SET tenant_name=EXCLUDED.tenant_name
            """, (project_id, name))
        else:
            db.execute("DELETE FROM command_center_project_tenant WHERE project_id=%s", (project_id,))
        db.execute("INSERT INTO audit (project_id, action) VALUES (%s, %s)",
                   (project_id, "command-center:tenant-assignment"))
    return {"tenant_name": name}


@app.put("/projects/{project_id}", status_code=202)
def create(project_id: UUID, body: Project):
    with database() as db:
        created = db.execute(
            """INSERT INTO projects (id,name,repository,runtime,status)
               VALUES (%s,%s,%s,%s,'queued') ON CONFLICT DO NOTHING RETURNING id""",
            (project_id, body.name, body.repository, body.runtime),
        ).fetchone()
        if created:
            db.execute("INSERT INTO audit (project_id,action) VALUES (%s,'create')", (project_id,))
        else:
            existing = db.execute("SELECT name,repository,runtime FROM projects WHERE id=%s", (project_id,)).fetchone()
            if existing != body.model_dump():
                raise HTTPException(409, "Project ID already has different settings")
    notify()
    return {"id": project_id}

@app.post("/projects/{project_id}/retry")
def retry(project_id: UUID):
    with database() as db:
        updated = db.execute("""UPDATE projects SET status='queued',error=NULL
                               WHERE id=%s AND status='failed' AND NOT archived RETURNING id""", (project_id,)).fetchone()
        if not updated:
            raise HTTPException(409, "Only failed active projects can be retried")
        db.execute("INSERT INTO audit (project_id,action) VALUES (%s,'retry')", (project_id,))
    notify()
    return {"ok": True}

@app.post("/projects/{project_id}/archive")
def archive(project_id: UUID):
    with database() as db:
        updated = db.execute("""UPDATE projects SET archived=true
                               WHERE id=%s AND NOT archived AND status IN ('created','failed') RETURNING id""", (project_id,)).fetchone()
        if not updated:
            raise HTTPException(409, "Project cannot be archived in its current state")
        db.execute("INSERT INTO audit (project_id,action) VALUES (%s,'archive')", (project_id,))
    return {"ok": True}

from compute_console import router as compute_console_router
app.include_router(compute_console_router)
app.include_router(memory_router)
app.include_router(github_router)

app.include_router(governance_router)

app.include_router(development_router)
app.include_router(cc_codex_router)
app.include_router(coder_inventory_router)
app.include_router(smd_deployment_router)
app.include_router(smd_receipts_router)
app.include_router(deployment_portal_router)
app.include_router(smd_git_tokens_router)
app.include_router(smd_release_assets_router)
app.include_router(smd_stack_export_router)

app.include_router(vault_router)

app.include_router(coder_session_router)

app.include_router(codex_access_router)
app.include_router(project_mcp_router)
from external_mcp_grants import router as external_mcp_grants_router
app.include_router(external_mcp_grants_router)
from external_mcp_transport import router as external_mcp_transport_router
app.include_router(external_mcp_transport_router)
app.include_router(mcp_request_history_router)
import operator_task  # Register task routes on the existing operator router.
from operator_dispatch import router as operator_dispatch_router
app.include_router(operator_dispatch_router)
from external_mcp_oauth_routes import router as external_mcp_oauth_router
app.include_router(external_mcp_oauth_router)

app.include_router(conversations_router)

app.include_router(runtime_router)

app.include_router(preview_router)

app.include_router(publishing_router)
app.include_router(domains_router)

from integrations import router as integrations_router
app.include_router(integrations_router)

from deploy_keys import router as deploy_keys_router
app.include_router(deploy_keys_router)

from workspace_screenshots import router as workspace_screenshots_router
app.include_router(workspace_screenshots_router)
