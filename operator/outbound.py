"""Poll SmartDev over HTTPS and dispatch only the enrolled portal inspection."""
import json
import os
import socket
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def endpoint(origin):
    url = urlsplit(origin)
    if (url.scheme != "https" or not url.hostname or url.username is not None
            or url.password is not None or url.path not in ("", "/")
            or url.query or url.fragment):
        raise ValueError("Expected a SmartDev HTTPS origin")
    return origin.rstrip("/") + "/api/operator-machine"


def socket_request(body):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(215 if body.get("operation") == "smartdev.dev.release" else 5)
        client.connect("/run/smartdev-operator.sock")
        client.sendall(json.dumps(body).encode() + b"\n")
        with client.makefile("rb") as stream:
            return json.loads(stream.readline(4096))


def inspect():
    return socket_request({"operation": "container.inspect", "service": "portal"})


def task_cycle(post, execute=socket_request):
    claim = post("task-poll", {})
    if not claim:
        return
    if claim["operation"] not in ("codex.version", "smartdev.dev.release") or claim["environment"] != "dev":
        raise ValueError("Unsupported task operation")
    for key in ("approval_id", "claim_id", "project_id", "task_id", "execution_id"):
        str(UUID(claim[key]))
    receipt = execute(claim)
    result = {
        "approval_id": claim["approval_id"], "claim_id": claim["claim_id"],
        "host_receipt_id": receipt["id"], "outcome": receipt["status"],
    }
    if claim["operation"] == "smartdev.dev.release" and receipt["status"] == "observed":
        result["deployed_revision"] = receipt["deployed_revision"]
    post("task-result", result)


def cycle(post, observe=inspect):
    claim = post("poll", {})
    if not claim:
        return
    request_id = str(UUID(claim["request_id"]))
    claim_id = str(UUID(claim["claim_id"]))
    receipt = observe()
    post("result", {
        "request_id": request_id, "claim_id": claim_id, "receipt_id": receipt["id"],
        "status": receipt["status"], "result": receipt.get("result"),
    })


def main():
    base = endpoint(os.environ["SMARTDEV_OPERATOR_ORIGIN"])
    from pathlib import Path
    token = (Path(os.environ["CREDENTIALS_DIRECTORY"]) / "machine-token").read_text().strip()
    if len(token) < 43 or not token.isascii() or any(char.isspace() for char in token):
        raise SystemExit("Invalid machine credential")
    opener = build_opener(NoRedirect())

    def post(path, body):
        request = Request(f"{base}/{path}", data=json.dumps(body).encode(),
                          headers={"Authorization": "Bearer " + token,
                                   "Content-Type": "application/json"}, method="POST")
        with opener.open(request, timeout=5) as response:
            return json.loads(response.read(8192))

    pause = 1
    while True:
        try:
            cycle(post)
            if os.getenv("SMARTDEV_OPERATOR_TASKS") == "1":
                task_cycle(post)
            pause = 1
        except HTTPError as error:
            print(f"SmartDev operator request failed (HTTP {error.code}).", flush=True)
            pause = min(30, pause * 2)
        except (OSError, URLError, ValueError, KeyError, TypeError):
            print("SmartDev operator transport or observation unavailable.", flush=True)
            pause = min(30, pause * 2)
        time.sleep(pause)


if __name__ == "__main__":
    main()
