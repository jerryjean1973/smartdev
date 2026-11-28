"""Renew or reissue the scoped SmartDev OpenBao service token."""

import json
import os
from pathlib import Path
import tempfile
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import build_opener, HTTPRedirectHandler, ProxyHandler, Request


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def maintain(address, token_file, role_file, secret_file, minimum_ttl=600):
    url = urlsplit(address)
    if url.username or url.password or url.path not in ("", "/") or url.query or url.fragment:
        raise ValueError("Invalid operator Vault origin")
    if url.scheme != "https" and not (
        url.scheme == "http" and url.hostname in ("vault", "openbao-replacement", "127.0.0.1", "localhost")
    ):
        raise ValueError("Use the dedicated internal Vault network or a verified TLS endpoint")
    client = build_opener(ProxyHandler({}), NoRedirect())

    def call(route, method="GET", token=None, payload=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Vault-Token"] = token
        req = Request(
            address.rstrip("/") + "/v1/" + route,
            method=method,
            headers=headers,
            data=json.dumps(payload).encode() if payload is not None else None,
        )
        with client.open(req, timeout=7) as response:
            return json.load(response)

    def lookup(token):
        data = call("auth/token/lookup-self", token=token)["data"]
        if set(data.get("policies", [])) != {"smartdev-portal"} or not data.get("renewable"):
            raise RuntimeError("Vault identity is outside the renewable portal service policy")
        return data

    token = token_file.read_text().strip() if token_file.is_file() else ""
    try:
        data = lookup(token) if token else {}
        if data.get("ttl", 0) > minimum_ttl:
            return "valid"
        if token:
            call("auth/token/renew-self", "POST", token, {})
            if lookup(token).get("ttl", 0) > 300:
                return "renewed"
    except HTTPError as error:
        if error.code not in (400, 401, 403):
            raise

    if not role_file.is_file() or not secret_file.is_file():
        raise RuntimeError("SmartDev AppRole enrollment is unavailable")
    auth = call(
        "auth/approle/login",
        "POST",
        payload={"role_id": role_file.read_text().strip(), "secret_id": secret_file.read_text().strip()},
    )["auth"]
    new_token = auth["client_token"]
    lookup(new_token)
    token_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=token_file.parent, delete=False) as output:
        output.write(new_token + "\n")
        output.flush()
        os.fsync(output.fileno())
        os.fchmod(output.fileno(), 0o400)
    os.replace(output.name, token_file)
    return "reissued"


if __name__ == "__main__":
    try:
        base = Path("/run/command-vault")
        enrollment = Path("/run/vault-enrollment")
        status = maintain(
            os.environ["OPENBAO_ADDR"],
            base / "token",
            enrollment / "role-id",
            enrollment / "secret-id",
            int(os.getenv("SMARTDEV_VAULT_MIN_TTL", "600")),
        )
        print("Portal Vault lease " + status)
    except Exception:
        print("Portal Vault lease unavailable. Check enrollment and Vault health.", file=os.sys.stderr)
        raise SystemExit(1) from None
