"""Managed deployment entry point. Only the registered issuer can initiate owner sign-in."""
import os
import time
from secrets import token_urlsafe
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
import httpx
from starlette.concurrency import run_in_threadpool

from core import issue_session
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
    response.set_cookie("__Host-command", session, secure=True, httponly=True, samesite="strict", max_age=ttl)
    return response


async def app(scope, receive, send):
    # Uvicorn still delegates the native API's full lifespan and all normal routes to its original app.
    if scope["type"] == "http" and scope["path"].startswith(prefix + "/"):
        async def no_cache(message):
            if message["type"] == "http.response.start":
                message["headers"] = [(k, v) for k, v in message["headers"] if k.lower() != b"cache-control"]
                message["headers"].append((b"cache-control", b"no-store"))
            await send(message)
        return await receiver(scope, receive, no_cache)
    return await native(scope, receive, send)
