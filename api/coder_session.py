"""Exchange an authenticated SmartDev session for a host-only Coder browser cookie."""
import hashlib
import json
import os
import secrets
import time
from pathlib import Path
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
def issue(request: Request):
    _, coder = configuration()
    if urlsplit(coder).scheme != "https" or not Path("/run/command-coder/owner.json").is_file():
        raise HTTPException(503, "Coder session handoff is unavailable.")
    grant = secrets.token_urlsafe(32)
    try:
        cookie = request.cookies["__Host-command"]
        expiry = int(cookie.split(".", 1)[0])
        queue().set("smartdev:coder-grant:" + grant, json.dumps([session_ref(cookie), expiry]), ex=30, nx=True)
    except RedisError:
        raise HTTPException(503, "Coder session handoff is unavailable.") from None
    return {"endpoint": coder + BRIDGE, "grant": grant}


class Redemption(BaseModel):
    grant: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")


def allowed(request):
    portal, coder = configuration()
    return bool(portal and coder and request.headers.get("origin") == portal
                and request.headers.get("host") == urlsplit(coder).netloc)


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
        key, expiry = json.loads(payload)
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
