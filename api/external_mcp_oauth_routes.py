"""Browser consent and token endpoints for a predefined OAuth client."""
import html
import os
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from core import valid_session
from external_mcp_oauth import PKCE_CHALLENGE, create_code, exchange_code, exchange_refresh
from store import database
from uuid import UUID

router = APIRouter()
SCOPES = {"read", "write"}


def configuration():
    if os.getenv("SMARTDEV_EXTERNAL_MCP_ENABLED") != "1":
        raise HTTPException(404, "Not found.")
    issuer = os.environ["PORTAL_ORIGIN"].rstrip("/")
    client = os.getenv("SMARTDEV_OAUTH_CLIENT_ID")
    redirect = os.getenv("SMARTDEV_OAUTH_REDIRECT_URI")
    if not client or not redirect or urlsplit(redirect).scheme != "https":
        raise HTTPException(503, "OAuth client is not configured.")
    return issuer, issuer + "/api/mcp", client, redirect


@router.get("/.well-known/oauth-protected-resource")
def protected_resource():
    issuer, resource, _, _ = configuration()
    return {"resource": resource, "authorization_servers": [issuer],
            "scopes_supported": sorted(SCOPES), "bearer_methods_supported": ["header"]}


@router.get("/.well-known/oauth-authorization-server")
def provider_metadata():
    issuer, _, _, _ = configuration()
    return {"issuer": issuer, "authorization_endpoint": issuer + "/api/oauth/authorize",
            "token_endpoint": issuer + "/api/oauth/token",
            "response_types_supported": ["code"], "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"], "token_endpoint_auth_methods_supported": ["none"],
            "scopes_supported": sorted(SCOPES), "authorization_response_iss_parameter_supported": True}


AUTH_FIELDS = ("client_id", "redirect_uri", "resource", "response_type", "code_challenge_method",
               "code_challenge", "scope", "state")


def validate_request(values):
    issuer, resource, client, redirect = configuration()
    values.setdefault("resource", resource)
    values.setdefault("scope", "read")
    expected = {"client_id": client, "redirect_uri": redirect, "resource": resource,
                "response_type": "code", "code_challenge_method": "S256"}
    for field, expected_value in expected.items():
        if values.get(field) != expected_value:
            raise HTTPException(400, f"Invalid OAuth authorization field: {field}.")
    if not PKCE_CHALLENGE.fullmatch(values.get("code_challenge", "")):
        raise HTTPException(400, "Invalid OAuth authorization field: code_challenge.")
    if not values.get("state"):
        raise HTTPException(400, "Missing OAuth authorization field: state.")
    scopes = set(values["scope"].split())
    if not scopes or not scopes <= SCOPES:
        raise HTTPException(400, "Invalid OAuth scope.")
    return issuer, resource, scopes


def signed_in(request):
    return valid_session(request.cookies.get("__Host-command", ""), bytes.fromhex(os.environ["SESSION_KEY"]))


def page(content):
    redirect = urlsplit(configuration()[3])
    redirect_origin = f"{redirect.scheme}://{redirect.netloc}"
    return HTMLResponse("<!doctype html><html lang=en><meta charset=utf-8><title>SmartDev OAuth</title>" + content,
                        headers={"Referrer-Policy": "same-origin", "Cache-Control": "no-store",
                                 "Content-Security-Policy": f"default-src 'none'; form-action 'self' {redirect_origin}; frame-ancestors 'none'"})


@router.get("/oauth/authorize")
def authorize(request: Request):
    values = dict(request.query_params)
    issuer, _, scopes = validate_request(values)
    esc = html.escape
    if not signed_in(request):
        retry = issuer + "/api/oauth/authorize?" + urlencode({key: values[key] for key in AUTH_FIELDS})
        return page('<h1>Sign in to SmartDev</h1><a href="' + esc(issuer) +
                    '" target="_blank" rel="noopener">Open SmartDev sign-in</a><p><a href="' +
                    esc(retry) + '">Continue after signing in</a></p>')
    with database() as db:
        projects = db.execute("""
            SELECT p.id,p.name FROM projects p WHERE NOT p.archived
            AND NOT EXISTS (SELECT 1 FROM acceptance_fixture f WHERE f.project_id=p.id)
            ORDER BY p.name,p.id
        """).fetchall()
    hidden = "".join(f'<input type=hidden name="{key}" value="{esc(values[key])}">' for key in AUTH_FIELDS)
    choices = "".join(f'<p><label><input type=checkbox name=project_id value="{row["id"]}">'
                      f'{esc(row["name"])}</label></p>' for row in projects)
    return page('<h1>Authorize SmartDev access</h1><p>Requested permissions: ' + esc(", ".join(sorted(scopes))) +
                '</p><form method=post>' + hidden + choices +
                '<button name=decision value=approve>Authorize selected projects</button> '
                '<button name=decision value=deny>Deny</button></form>')


async def form(request):
    if request.headers.get("content-type", "").split(";")[0] != "application/x-www-form-urlencoded":
        raise HTTPException(415, "Expected URL-encoded form.")
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > 16384:
            raise HTTPException(413, "OAuth request too large.")
        content.extend(chunk)
    try:
        return parse_qs(content.decode(), keep_blank_values=True, max_num_fields=80)
    except ValueError:
        raise HTTPException(400, "Invalid OAuth form.") from None


def consent(fields):
    values = {key: value[0] for key, value in fields.items() if len(value) == 1}
    issuer, resource, scopes = validate_request(values)
    response = {"state": values["state"], "iss": issuer}
    if values.get("decision") != "approve":
        response["error"] = "access_denied"
    else:
        try:
            projects = list({UUID(field) for field in fields.get("project_id", [])})
            if not 1 <= len(projects) <= 32:
                raise ValueError()
            with database() as db:
                owner = db.execute("SELECT id FROM smartdev_owner WHERE singleton").fetchone()
                code = create_code(db, owner_id=owner["id"], client_id=values["client_id"],
                                   redirect_uri=values["redirect_uri"], challenge=values["code_challenge"],
                                   project_ids=projects, scopes=sorted(scopes), resource=resource)
            response["code"] = code
        except ValueError:
            response["error"] = "access_denied"
    redirect = urlsplit(values["redirect_uri"])
    query = redirect.query + ("&" if redirect.query else "") + urlencode(response)
    return RedirectResponse(urlunsplit(redirect._replace(query=query)), status_code=303,
                            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@router.post("/oauth/authorize")
async def approve(request: Request):
    if not signed_in(request):
        raise HTTPException(401, "Sign in required.")
    return await run_in_threadpool(consent, await form(request))


def redeem(values):
    _, resource, client, redirect = configuration()
    headers = {"Cache-Control": "no-store", "Pragma": "no-cache"}
    grant_type = values.get("grant_type")
    if grant_type not in {"authorization_code", "refresh_token"}:
        return JSONResponse({"error": "unsupported_grant_type"}, 400, headers)
    if values.get("client_id") != client:
        return JSONResponse({"error": "invalid_client"}, 400, headers)
    supplied_redirect = values.get("redirect_uri", redirect if grant_type == "refresh_token" else None)
    if supplied_redirect != redirect or values.get("resource", resource) != resource:
        return JSONResponse({"error": "invalid_grant"}, 400, headers)
    try:
        with database() as db:
            if grant_type == "refresh_token":
                result = exchange_refresh(db, refresh_token=values.get("refresh_token", ""),
                                          client_id=client, redirect_uri=redirect,
                                          resource=resource, scope=values.get("scope"))
            else:
                result = exchange_code(db, code=values.get("code", ""), client_id=client,
                                       redirect_uri=redirect, verifier=values.get("code_verifier", ""), resource=resource)
        return JSONResponse(result, headers=headers)
    except ValueError:
        return JSONResponse({"error": "invalid_grant"}, 400, headers)


@router.post("/oauth/token")
async def token(request: Request):
    fields = await form(request)
    return await run_in_threadpool(redeem, {key: value[0] for key, value in fields.items() if len(value) == 1})
