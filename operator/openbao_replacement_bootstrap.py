"""Initialize an isolated replacement Vault without exposing bootstrap values."""

import json
import os
from pathlib import Path
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler


ADDRESS = os.environ.get("OPENBAO_ADDR", "http://openbao-replacement:8200").rstrip("/")
HANDOFF = Path("/handoff")
PORTAL = Path("/portal-enrollment")
PORTAL_POLICY = Path("/portal-policy.hcl")


def request(path, method="GET", token=None, payload=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Vault-Token"] = token
    body = json.dumps(payload).encode() if payload is not None else None
    req = Request(f"{ADDRESS}/v1/{path}", data=body, headers=headers, method=method)
    with build_opener(ProxyHandler({})).open(req, timeout=15) as response:
        raw = response.read()
    return json.loads(raw) if raw else {}


def atomic_secret(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as output:
        output.write(value + "\n")
        output.flush()
        os.fsync(output.fileno())
        os.fchmod(output.fileno(), 0o600)
    os.replace(output.name, path)


def policy(name, contents, root):
    request(f"sys/policies/acl/{name}", "PUT", root, {"policy": contents})


def role(name, policies, period, root):
    request(
        f"auth/approle/role/{name}",
        "POST",
        root,
        {
            "bind_secret_id": True,
            "secret_id_num_uses": 0,
            "secret_id_ttl": "0s",
            "token_no_default_policy": True,
            "token_period": period,
            "token_policies": policies,
            "token_type": "service",
        },
    )
    role_id = request(f"auth/approle/role/{name}/role-id", token=root)["data"]["role_id"]
    secret_id = request(f"auth/approle/role/{name}/secret-id", "POST", root, {})["data"]["secret_id"]
    return role_id, secret_id


def login(role_id, secret_id):
    return request("auth/approle/login", "POST", payload={"role_id": role_id, "secret_id": secret_id})["auth"]


def main():
    os.umask(0o077)
    for _ in range(30):
        try:
            status = request("sys/health")
            break
        except HTTPError as error:
            if error.code == 501:
                status = json.loads(error.read())
                break
            raise
        except OSError:
            time.sleep(1)
    else:
        raise RuntimeError("Replacement OpenBao did not become reachable")
    if status.get("initialized"):
        raise RuntimeError("Replacement bootstrap requires a fresh volume")

    initialized = request("sys/init", "PUT", payload={"secret_shares": 3, "secret_threshold": 2})
    shares = initialized["keys_base64"]
    root = initialized["root_token"]
    atomic_secret(HANDOFF / "unseal-shares.json", json.dumps({"keys_base64": shares, "threshold": 2}))
    for share in shares[:2]:
        request("sys/unseal", "PUT", payload={"key": share})

    request("sys/mounts/smartdev", "POST", root, {"type": "kv", "options": {"version": "2"}})
    policy("smartdev-portal", PORTAL_POLICY.read_text(), root)
    operator_policy = '''
path "sys/health" { capabilities = ["read"] }
path "sys/auth" { capabilities = ["read"] }
path "sys/auth/*" { capabilities = ["create", "read", "update", "delete", "list", "sudo"] }
path "auth/approle/*" { capabilities = ["create", "read", "update", "delete", "list"] }
path "sys/policies/acl/*" { capabilities = ["create", "read", "update", "delete", "list"] }
path "sys/mounts" { capabilities = ["read"] }
path "sys/mounts/*" { capabilities = ["create", "read", "update", "delete", "list", "sudo"] }
path "sys/audit" { capabilities = ["read"] }
path "sys/audit/*" { capabilities = ["create", "read", "update", "delete", "list", "sudo"] }
path "auth/token/lookup-self" { capabilities = ["read"] }
path "auth/token/renew-self" { capabilities = ["update"] }
path "auth/token/revoke-self" { capabilities = ["update"] }
'''
    policy("smartdev-operator", operator_policy, root)
    request("sys/auth/approle", "POST", root, {"type": "approle"})

    admin_role, admin_secret = role("smartdev-operator", ["smartdev-operator"], "15m", root)
    portal_role, portal_secret = role("smartdev-portal", ["smartdev-portal"], "15m", root)
    atomic_secret(HANDOFF / "admin-role-id", admin_role)
    atomic_secret(HANDOFF / "admin-secret-id", admin_secret)
    atomic_secret(PORTAL / "role-id", portal_role)
    atomic_secret(PORTAL / "secret-id", portal_secret)

    admin = login(admin_role, admin_secret)
    if set(admin.get("policies", [])) != {"smartdev-operator"} or not admin.get("renewable"):
        raise RuntimeError("Administrator AppRole handoff verification failed")
    request("sys/auth", token=admin["client_token"])
    portal = login(portal_role, portal_secret)
    if set(portal.get("policies", [])) != {"smartdev-portal"} or not portal.get("renewable"):
        raise RuntimeError("SmartDev AppRole enrollment verification failed")
    request("auth/token/revoke-self", "POST", portal["client_token"], {})
    request("auth/token/revoke-self", "POST", admin["client_token"], {})
    request("auth/token/revoke-self", "POST", root, {})
    print("Replacement OpenBao initialized; administrator and SmartDev handoffs verified; bootstrap root revoked.")


if __name__ == "__main__":
    try:
        main()
    except HTTPError as error:
        raise SystemExit(f"Replacement bootstrap failed at OpenBao HTTP {error.code}") from None
