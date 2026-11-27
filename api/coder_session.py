"""Exchange an authenticated SmartDev session for a host-only Coder browser cookie."""
import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from redis.exceptions import RedisError
from starlette.responses import JSONResponse, Response

from store import queue

router = APIRouter()
BRIDGE = "/__smartdev/session"
BROWSER_TTL = 8 * 60 * 60
REVOKED = b"revoked"


def configuration():
    return os.getenv("PORTAL_ORIGIN", "").rstrip("/"), os.getenv("CODER_PUBLIC_URL", "").rstrip("/")


def session_ref(cookie):
    return "smartdev:coder-session:" + hashlib.sha256(cookie.encode()).hexdigest()


def upstream():
    return httpx.Client(base_url=os.environ["CODER_API_URL"], timeout=7, trust_env=False)


@router.post("/coder-session")
def issue(request: Request, target: Literal["coding", "management"] = "coding"):
    portal, coder = configuration()
    if target == "management" and (request.headers.get("origin") != portal
                                    or request.headers.get("host") != urlsplit(portal).netloc):
        raise HTTPException(403, "Invalid management session origin.")
    destination = portal if target == "management" else coder
    if urlsplit(destination).scheme != "https" or not Path("/run/command-coder/owner.json").is_file():
        raise HTTPException(503, "Coder session handoff is unavailable.")
    grant = secrets.token_urlsafe(32)
    try:
        cookie = request.cookies["__Host-command"]
        expiry = int(cookie.split(".", 1)[0])
        queue().set("smartdev:coder-grant:" + grant, json.dumps([session_ref(cookie), expiry, urlsplit(destination).netloc]), ex=30, nx=True)
    except RedisError:
        raise HTTPException(503, "Coder session handoff is unavailable.") from None
    return {"endpoint": destination + ("/api" if target == "management" else "") + BRIDGE, "grant": grant}


class Redemption(BaseModel):
    grant: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")


def allowed(request):
    portal, coder = configuration()
    trusted_origins = {portal}
    if os.getenv("SMARTDEV_ENABLE_CODING_HOST") == "1":
        trusted_origins.add(coder)
    caller = request.headers.get("origin")
    host = request.headers.get("host")
    return bool(portal and coder and (
        (caller == portal and host == urlsplit(portal).netloc)
        or (caller in trusted_origins and host == urlsplit(coder).netloc)
    ))


def cors():
    portal, _ = configuration()
    return {"Access-Control-Allow-Origin": portal, "Access-Control-Allow-Credentials": "true",
            "Vary": "Origin", "Cache-Control": "no-store"}


@router.options(BRIDGE)
def preflight(request: Request):
    if not allowed(request):
        return Response(status_code=403)
    return Response(status_code=204, headers={**cors(), "Access-Control-Allow-Methods": "POST",
                                              "Access-Control-Allow-Headers": "Content-Type"})


@router.post(BRIDGE)
def redeem(request: Request, body: Redemption):
    if not allowed(request):
        return Response(status_code=403)
    try:
        redis = queue()
        payload = redis.getdel("smartdev:coder-grant:" + body.grant)
        if not payload:
            return JSONResponse({"detail": "Connection request expired. Reconnect to try again."}, 401, headers=cors())
        values = json.loads(payload)
        key, expiry = values[:2]
        destination_host = values[2] if len(values) == 3 else urlsplit(configuration()[1]).netloc
        if request.headers.get("host") != destination_host:
            return JSONResponse({"detail": "Connection request is for another host."}, 403, headers=cors())
        ttl = min(BROWSER_TTL, expiry - int(time.time()))
        if ttl <= 0:
            return JSONResponse({"detail": "The SmartDev session ended."}, 401, headers=cors())
        with redis.lock(key + ":lock", timeout=40, blocking_timeout=4):
            previous = redis.get(key)
            if previous == REVOKED:
                return JSONResponse({"detail": "The SmartDev session ended."}, 401, headers=cors())
            with upstream() as client:
                token = previous.decode() if previous else None
                if token:
                    check = client.get("/api/v2/users/me", headers={"Coder-Session-Token": token})
                    if check.status_code in (401, 403):
                        token = None
                    else:
                        check.raise_for_status()
                if not token:
                    credential = json.loads(Path("/run/command-coder/owner.json").read_text())
                    result = client.post("/api/v2/users/login", json={
                        "email": credential["email"], "password": credential["password"],
                    })
                    result.raise_for_status()
                    token = result.json()["session_token"]
                redis.set(key, token, ex=ttl)
    except (httpx.HTTPError, OSError, ValueError, KeyError, RedisError):
        return JSONResponse({"detail": "Coder could not establish a browser session."}, 503, headers=cors())
    response = JSONResponse({"connected": True}, headers=cors())
    response.set_cookie("coder_session_token", token, max_age=ttl, path="/",
                        secure=True, httponly=True, samesite="lax")
    return response


def revoke(portal_cookie):
    if not portal_cookie:
        return
    try:
        redis = queue()
        key = session_ref(portal_cookie)
        with redis.lock(key + ":lock", timeout=30, blocking_timeout=5):
            token = redis.getset(key, REVOKED)
            redis.expire(key, BROWSER_TTL)
            if token and token != REVOKED:
                with upstream() as client:
                    response = client.post("/api/v2/users/logout", headers={"Coder-Session-Token": token.decode()})
                    if response.status_code not in (401, 403):
                        response.raise_for_status()
    except (RedisError, httpx.HTTPError, KeyError):
        return
