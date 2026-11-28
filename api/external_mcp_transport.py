"""Stateless MCP transport for SmartDev's scoped task tools."""
import json
import os

from fastapi import APIRouter, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool

from external_mcp_grants import require_grant
from external_mcp_tasks import ScopedTasks, WorkspaceUnavailable
from governance import task_navigation

COMMAND_CENTER_IMAGE = os.getenv("SMARTDEV_IMAGE_PROFILE", "command-center") != "tenant-smd"
if COMMAND_CENTER_IMAGE:
    from smd_environment_fixes import EnvironmentBindingUnavailable
else:
    class EnvironmentBindingUnavailable(LookupError):
        pass

router = APIRouter()
VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")


def tool(name, description, properties, required=(), *, write=False):
    scope = "write" if write else "read"
    return {
        "name": name, "description": description,
        "inputSchema": {"type": "object", "properties": properties,
                        "required": list(required), "additionalProperties": False},
        "annotations": {"readOnlyHint": not write, "idempotentHint": True},
        "securitySchemes": [{"type": "oauth2", "scopes": [scope]}],
        "_meta": {"securitySchemes": [{"type": "oauth2", "scopes": [scope]}]},
    }


UUID_FIELD = {"type": "string", "format": "uuid"}
TEXT_FIELD = {"type": "string", "minLength": 1, "maxLength": 30000}
SCOPE_FIELD = {"type": "string", "description": "Work scope for this request: global for platform-wide work, an existing BO ID, or unassigned."}
MESSAGE_FIELDS = {"task_id": UUID_FIELD, "message": TEXT_FIELD, "client_request_id": UUID_FIELD}
RELEASE_TOOL = "smartdev_dev_release"
WRITE_TOOLS = {"smartdev_create_task", "smartdev_send_message", "smartdev_reply_to_waiting_task",
               "smartdev_request_environment_fix", RELEASE_TOOL}

TOOLS = [
    tool("smartdev_get_receipt", "Look up an MCP task write by its public execution receipt or Taskboard request ID.",
         {"receipt": {"type":"string"}}, ("receipt",)),
    tool("smartdev_inspect_dev_portal", "Inspect the enrolled SmartDev DEV portal via the Ubuntu operator",
         {"project_id": UUID_FIELD}, ("project_id",)),
    tool("smartdev_list_projects", "List authorized SmartDev projects", {}),
    tool("smartdev_list_tasks", "List tasks in an authorized SmartDev project",
         {"project_id": {"type": "string", "format": "uuid"}, "status": {"type": "string"}},
         ("project_id",)),
    tool("smartdev_get_task", "Read the identity and lifecycle status of a SmartDev task",
         {"task_id": {"type": "string", "format": "uuid"}}, ("task_id",)),
    tool("smartdev_get_latest_response", "Read the latest visible Codex response for a task",
         {"task_id": UUID_FIELD}, ("task_id",)),
    tool("smartdev_create_task", "Submit work and receive its codex_task_id. Open matching work is reused or updated. Reuse the client request UUID on retry and a stable work_key for refinements.",
         {"project_id": UUID_FIELD, "title": {"type": "string"}, "work_key": {"type":"string","description":"Stable issue or deliverable identifier within this project and scope."}, "instructions": TEXT_FIELD, "scope": SCOPE_FIELD,
          "client_request_id": UUID_FIELD}, ("project_id", "instructions", "client_request_id"), write=True),
    tool("smartdev_send_message", "Send a follow-up to an existing task. Reuse the same client request UUID on retry.",
         {**MESSAGE_FIELDS, "scope": SCOPE_FIELD}, tuple(MESSAGE_FIELDS), write=True),
    tool("smartdev_reply_to_waiting_task", "Resume a task stopped awaiting a user reply. Reuse the same client request UUID on retry.",
         {**MESSAGE_FIELDS, "scope": SCOPE_FIELD}, tuple(MESSAGE_FIELDS), write=True),
]
if COMMAND_CENTER_IMAGE:
    TOOLS.extend([
        tool("smartdev_list_environments", "List active verified deployment environments in the caller's current tenant and project scope.", {}),
        tool("smartdev_get_environment_fix", "Read delivery status and the remote executor receipt for a tenant-scoped environment fix request.",
             {"request_id": UUID_FIELD}, ("request_id",)),
        tool("smartdev_request_environment_fix", "Request a fix in one explicitly selected deployed environment. The target must be active, verified, assigned to the caller's current tenant, and contain the authorized project. Delivery uses the enrolled operator; SSH credentials are never returned.",
         {"deployment_id": UUID_FIELD, "project_id": UUID_FIELD,
          "environment": {"type":"string","enum":["DEV","QA","DEPLOYED"]},
          "title": {"type":"string","minLength":1,"maxLength":100},
          "instructions": {"type":"string","minLength":1,"maxLength":27000},
          "client_request_id": UUID_FIELD},
         ("deployment_id","project_id","environment","instructions","client_request_id"), write=True),
    ])


def release_tool():
    from operator_task import SignedGrant

    return tool(RELEASE_TOOL, "Queue or retrieve one signed SmartDev DEV release. Reuse the grant and ticket to retrieve its receipt.",
                {"project_id": UUID_FIELD, "grant": SignedGrant.model_json_schema(),
                 "ticket": {"type": "string", "minLength": 43, "maxLength": 128}},
                ("project_id", "grant", "ticket"), write=True)


def dispatch(message, reads):
    request_id = message.get("id")
    method = message.get("method")
    params = message.get("params", {})
    frame = {"jsonrpc": "2.0", "id": request_id}

    def error(code, description):
        return {**frame, "error": {"code": code, "message": description}}

    if message.get("jsonrpc") != "2.0" or not isinstance(method, str):
        return error(-32600, "Invalid Request")
    if "id" not in message:
        return None
    if not isinstance(params, dict):
        return error(-32602, "Invalid params")
    if method == "initialize":
        requested = params.get("protocolVersion")
        result = {"protocolVersion": requested if requested in VERSIONS else VERSIONS[0],
                  "capabilities": {"tools": {}}, "serverInfo": {"name": "smartdev", "version": "0.1.0"}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        from operator_task import release_project

        result = {"tools": [*TOOLS, release_tool()] if release_project() else TOOLS}
    elif method == "tools/call":
        handlers = {
            "smartdev_inspect_dev_portal": lambda **args: reads.inspect_dev_portal(**args),
            "smartdev_list_projects": reads.list_projects,
            "smartdev_list_tasks": reads.list_tasks,
            "smartdev_get_task": reads.get_task,
            "smartdev_get_receipt": lambda **args: reads.get_receipt(**args),
            "smartdev_get_latest_response": reads.get_latest_response,
            "smartdev_create_task": reads.create_task,
            "smartdev_send_message": reads.send_message,
            "smartdev_reply_to_waiting_task": reads.reply_to_waiting_task,
            RELEASE_TOOL: reads.release_dev,
        }
        if COMMAND_CENTER_IMAGE:
            handlers.update({
                "smartdev_list_environments": reads.list_environments,
                "smartdev_get_environment_fix": reads.get_environment_fix,
                "smartdev_request_environment_fix": reads.request_environment_fix,
            })
        name = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(name, str) or name not in handlers or not isinstance(arguments, dict):
            return error(-32602, "Invalid tool or arguments")
        try:
            data = handlers[name](**arguments)
            result = {"content": [{"type": "text", "text": json.dumps(data)}]}
            if isinstance(data, dict):
                result["structuredContent"] = data
        except (WorkspaceUnavailable, EnvironmentBindingUnavailable) as failure:
            result = {"content": [{"type": "text", "text": str(failure)}], "isError": True}
        except LookupError:
            result = {"content": [{"type": "text", "text": "Resource not available."}], "isError": True}
        except (TypeError, ValueError):
            return error(-32602, "Invalid tool arguments")
    else:
        return error(-32601, "Method not found")
    return {**frame, "result": result}


def handle(message, authorization):
    if message.get("method") in ("initialize", "ping", "tools/list", "notifications/initialized"):
        return dispatch(message, None)
    from main import projects
    issuer = os.environ["PORTAL_ORIGIN"].rstrip("/")
    try:
        params = message.get("params")
        name = params.get("name") if isinstance(params, dict) else None
        scope = "write" if isinstance(name, str) and name in WRITE_TOOLS else "read"
        grant = require_grant(authorization, scope, issuer + "/api/mcp")
    except HTTPException as error:
        if error.status_code in (401, 403):
            error.headers = {"WWW-Authenticate": 'Bearer resource_metadata="' +
                             issuer + '/api/.well-known/oauth-protected-resource", scope="' + scope + '"' +
                             (', error="insufficient_scope"' if error.status_code == 403 else '')}
        raise
    return dispatch(message, ScopedTasks(grant, projects=projects,
                                             task_navigation=task_navigation))


@router.post("/mcp")
async def mcp(request: Request):
    if os.getenv("SMARTDEV_EXTERNAL_MCP_ENABLED") != "1":
        raise HTTPException(404, "Not found.")
    if request.headers.get("origin") not in (None, os.environ["PORTAL_ORIGIN"].rstrip("/"), "https://chatgpt.com"):
        raise HTTPException(403, "Invalid request origin.")
    version = request.headers.get("mcp-protocol-version")
    if version and version not in VERSIONS:
        raise HTTPException(400, "Unsupported MCP protocol version.")
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > 200000:
            raise HTTPException(413, "MCP request too large.")
        data.extend(chunk)
    try:
        message = json.loads(data)
    except (ValueError, UnicodeError):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
    if not isinstance(message, dict):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}
    result = await run_in_threadpool(handle, message, request.headers.get("authorization", ""))
    return Response(status_code=202) if result is None else result
