"""Initialize project-local OpenBao and keep its scoped portal token current."""
import json
import os
from pathlib import Path
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

VAULT_URL = "http://openbao:8200/v1/"
RECOVERY_FILE = Path("/vault-operator/recovery.json")
CLIENT_TOKEN = Path("/vault-client/token")
POLICY = "smartdev-portal"


def call(path, payload=None, token=None, *, method=None, timeout=4):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Vault-Token"] = token
    data = json.dumps(payload).encode() if payload is not None else None
    request = Request(VAULT_URL + path, data=data, headers=headers, method=method)
    with urlopen(request, timeout=timeout) as response:
        body = response.read()
    return json.loads(body) if body else {}


def secure_write(path, contents, mode, group=0):
    staging = path.with_suffix(".tmp")
    fd = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as target:
        target.write(contents)
        target.flush()
        os.fsync(target.fileno())
    staging.chmod(mode)
    os.chown(staging, 0, group)
    staging.replace(path)


def ensure_ready():
    if not call("sys/init")["initialized"]:
        if RECOVERY_FILE.exists():
            raise RuntimeError("Vault storage is empty and a previous recovery file exists")
        recovery = call("sys/init", {"secret_shares": 1, "secret_threshold": 1}, timeout=90)
        secure_write(RECOVERY_FILE, json.dumps(recovery), 0o600)
    recovery = json.loads(RECOVERY_FILE.read_text())
    if call("sys/seal-status")["sealed"]:
        call("sys/unseal", {"key": recovery["keys_base64"][0]}, timeout=60)
    root = recovery["root_token"]
    if CLIENT_TOKEN.exists():
        try:
            call("auth/token/lookup-self", token=CLIENT_TOKEN.read_text().strip())
        except HTTPError as error:
            if error.code != 403:
                raise
        else:
            marker = Path("/tmp/smartdev-vault-ready")
            marker.touch()
            return
    if "smartdev/" not in call("sys/mounts", token=root)["data"]:
        call("sys/mounts/smartdev", {"type": "kv", "options": {"version": "2"}}, root)
    call(f"sys/policies/acl/{POLICY}",
         {"policy": Path("/vault-policy.hcl").read_text()}, root, method="PUT")
    token = call("auth/token/create-orphan",
                 {"policies": [POLICY], "no_default_policy": False, "ttl": "24h"},
                 root)["auth"]["client_token"]
    secure_write(CLIENT_TOKEN, token + "\n", 0o440, 65532)
    Path("/tmp/smartdev-vault-ready").touch()


if __name__ == "__main__":
    if sys.argv[1:] == ["--health"]:
        if not Path("/tmp/smartdev-vault-ready").exists():
            sys.exit(1)
        call("auth/token/lookup-self", token=CLIENT_TOKEN.read_text().strip())
    else:
        os.umask(0o077)
        while True:
            try:
                ensure_ready()
            except (KeyError, RuntimeError, OSError, ValueError) as failure:
                # HTTPError includes the route and HTTP status only.
                print(f"Vault initialization: {failure}", file=sys.stderr, flush=True)
            time.sleep(5)
