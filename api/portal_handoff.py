"""Browser messages shared by the deployment portal issuer and receiver."""
import base64
from hashlib import sha256
from html import escape
import re
from secrets import token_urlsafe
from urllib.parse import parse_qsl, urlsplit

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

PROTOCOL = "owner-pkce-v1"
RECEIVER = "/api/deployment-portal"
ISSUER = "/api/deployment-sign-in"
TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")
TTL = 120


def challenge(verifier):
    return base64.urlsafe_b64encode(sha256(verifier.encode("ascii")).digest()).decode().rstrip("=")


def require_origin(request, origin):
    if request.headers.get("origin") != origin:
        raise HTTPException(403, "Use the deployment link in Command Center.")


async def fields(request: Request, *names):
    if request.headers.get("content-type", "").split(";")[0] != "application/x-www-form-urlencoded":
        raise HTTPException(415, "Expected a portal sign-in form.")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > 1024:
            raise HTTPException(413, "Sign-in form too large.")
        body.extend(chunk)
    try:
        pairs = parse_qsl(body.decode("ascii"), strict_parsing=True, max_num_fields=len(names))
    except (UnicodeError, ValueError):
        pairs = []
    values = dict(pairs)
    if len(pairs) != len(names) or set(values) != set(names):
        raise HTTPException(400, "Invalid portal sign-in request.")
    return values


def post_page(action, **values):
    nonce = token_urlsafe(24)
    inputs = "".join(f'<input type="hidden" name="{escape(k)}" value="{escape(str(v), quote=True)}">' for k, v in values.items())
    target = escape(action, quote=True)
    allowed_origin = "https://" + urlsplit(action).netloc
    return HTMLResponse(
        '<!doctype html><html lang="en"><meta charset="utf-8"><title>Opening SmartDev Portal</title>'
        '<p>Opening SmartDev Portal…</p>'
        f'<form method="post" action="{target}">{inputs}<noscript><button>Continue</button></noscript></form>'
        f'<script nonce="{nonce}">document.forms[0].submit()</script></html>',
        headers={"Cache-Control": "no-store", "Referrer-Policy": "origin", "X-Content-Type-Options": "nosniff",
                 "Content-Security-Policy": f"default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action {allowed_origin}; script-src 'nonce-{nonce}'"},
    )
