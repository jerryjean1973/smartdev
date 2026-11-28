"""Unseal the isolated SmartDev Vault from its protected operator handoff."""

import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler


ADDRESS = os.environ.get("OPENBAO_ADDR", "http://openbao-replacement:8200").rstrip("/")
SHARES = Path(os.environ.get("OPENBAO_UNSEAL_SHARES", "/handoff/unseal-shares.json"))


def request(path, method="GET", payload=None):
    body = json.dumps(payload).encode() if payload is not None else None
    req = Request(
        f"{ADDRESS}/v1/{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with build_opener(ProxyHandler({})).open(req, timeout=10) as response:
            raw = response.read()
    except HTTPError as error:
        raw = error.read()
        if error.code not in (429, 472, 473, 501, 503):
            raise
    return json.loads(raw) if raw else {}


def main():
    status = request("sys/health")
    if not status.get("initialized"):
        raise RuntimeError("OpenBao is not initialized")
    if not status.get("sealed"):
        print("OpenBao already unsealed")
        return
    handoff = json.loads(SHARES.read_text())
    threshold = int(handoff["threshold"])
    for share in handoff["keys_base64"][:threshold]:
        status = request("sys/unseal", "PUT", {"key": share})
    if status.get("sealed"):
        raise RuntimeError("OpenBao remained sealed after the configured threshold")
    print("OpenBao unsealed from the protected operator handoff")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("OpenBao unseal failed. Check the protected operator handoff.", file=os.sys.stderr)
        raise SystemExit(1) from None
