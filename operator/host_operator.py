"""Local, allowlisted SmartDev DEV operations over a systemd-owned socket."""
import fcntl
import json
import os
import re
import socket
import subprocess
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile
from datetime import datetime, timezone
from uuid import UUID, uuid4

PROJECT = "smart-dev-ide-browser"
SERVICES = {"api", "portal", "worker", "dev-gateway"}
OPERATIONS = {"container.inspect", "service.restart"}
CONTAINER_ID = re.compile(r"[0-9a-f]{64}")


def docker(*args):
    result = subprocess.run(
        ["/usr/bin/docker", "--host", "unix:///var/run/docker.sock", *args],
        check=True, capture_output=True, text=True, timeout=40,
        env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"},
    )
    return result.stdout


def inspect(target, run):
    rows = json.loads(run("container", "inspect", target))
    if len(rows) != 1:
        raise ValueError("Unexpected target count")
    row = rows[0]
    labels = row["Config"]["Labels"]
    service = labels.get("com.docker.compose.service")
    if (
        service not in SERVICES
        or labels.get("com.docker.compose.project") != PROJECT
        or row["Name"] != f"/{PROJECT}-{service}-1"
        or not CONTAINER_ID.fullmatch(row["Id"])
    ):
        raise ValueError("Target outside SmartDev DEV scope")
    return row, service


def execute(request, enrolled, run=docker):
    if not isinstance(request, dict) or set(request) != {"operation", "service"}:
        raise ValueError("Invalid request")
    operation, service = request["operation"], request["service"]
    if not isinstance(operation, str) or operation not in OPERATIONS:
        raise ValueError("Operation denied")
    if not isinstance(service, str) or service not in enrolled:
        raise ValueError("Service denied")
    row, actual_service = inspect(f"{PROJECT}-{service}-1", run)
    if actual_service != service:
        raise ValueError("Service identity mismatch")
    if operation == "service.restart":
        container_id = row["Id"]
        run("container", "restart", "--timeout", "15", container_id)
        row, actual_service = inspect(container_id, run)
    state = row["State"]
    return {
        "service": actual_service,
        "container_id": row["Id"],
        "image_id": row["Image"],
        "state": state["Status"],
        "health": state.get("Health", {}).get("Status"),
    }


def task_probe(request, receipt):
    fields = {"approval_id", "claim_id", "task_id", "execution_id", "project_id",
              "environment", "operation", "expires_at"}
    if set(request) != fields or request["environment"] != "dev":
        raise ValueError("Task scope denied")
    for key in ("approval_id", "claim_id", "task_id", "execution_id", "project_id"):
        receipt[key] = str(UUID(request[key]))
    with open("/etc/smartdev-operator/task-project", encoding="utf-8") as config:
        project = str(UUID(config.read().strip()))
    if receipt["project_id"] != project:
        raise ValueError("Project denied")
    receipt.update(operation="codex.version", environment="dev")
    deadline = datetime.fromisoformat(request["expires_at"])
    remaining = (deadline - datetime.now(timezone.utc)).total_seconds()
    if remaining <= 0:
        receipt.update(status="expired")
        return
    result = subprocess.run(
        ["/usr/local/lib/smartdev-operator/codex", "--version"], cwd="/",
        env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "CODEX_HOME": "/nonexistent"},
        stdin=subprocess.DEVNULL, capture_output=True, text=True, check=True,
        timeout=min(remaining, 3),
    )
    version = result.stdout.strip()
    if not re.fullmatch(r"codex-cli [0-9A-Za-z.+-]{1,80}", version):
        raise ValueError("Unexpected Codex version")
    receipt.update(status="observed", result={"version": version})


RELEASE_OPERATION = "smartdev.dev.release"
RELEASE_CONFIG = Path("/etc/smartdev-operator/release.json")
RELEASE_STATE = Path("/var/lib/smartdev-operator")
RELEASE_WORKSPACE_ID = "7c8b5d6a-b6aa-4ba0-adbd-04e93635cdef"
RELEASE_ENV = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"}


def verify_release(request, config):
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    claim_fields = {"approval_id", "claim_id", "project_id", "task_id", "execution_id",
                    "environment", "operation", "expires_at", "grant", "lease_until"}
    grant_fields = {"id", "project_id", "task_id", "execution_id", "environment",
                    "operation", "expires_at", "mig_receipt", "token_sha256",
                    "mig_signature", "workspace", "revision"}
    if set(request) - {"recover_only"} != claim_fields:
        raise ValueError("Invalid release claim")
    grant = request["grant"]
    if not isinstance(grant, dict) or set(grant) != grant_fields:
        raise ValueError("Invalid release grant")
    if (grant["id"] != request["approval_id"]
            or any(grant[key] != request[key] for key in
                   ("project_id", "task_id", "execution_id", "environment", "operation"))
            or grant["operation"] != RELEASE_OPERATION or grant["environment"] != "dev"
            or any(grant[key] != config[key] for key in ("project_id", "workspace", "revision"))
            or not re.fullmatch(r"p-[a-z2-7]{26}", grant["workspace"])
            or not re.fullmatch(r"[a-f0-9]{40}", grant["revision"])
            or not re.fullmatch(r"[a-f0-9]{64}", grant["token_sha256"])):
        raise ValueError("Release scope denied")
    for value in (grant["id"], grant["project_id"], grant["task_id"], grant["execution_id"],
                  grant["mig_receipt"], request["claim_id"]):
        if str(UUID(value)) != value:
            raise ValueError("Invalid release identifier")
    expiry = datetime.fromisoformat(grant["expires_at"])
    claim_expiry = datetime.fromisoformat(request["expires_at"])
    lease_until = datetime.fromisoformat(request["lease_until"])
    if any(value.tzinfo is None for value in (expiry, claim_expiry, lease_until)) or claim_expiry > expiry:
        raise ValueError("Invalid release lease")
    payload = json.dumps([
        "smartdev-operator-task-v2", grant["id"], grant["project_id"], grant["task_id"],
        grant["execution_id"], grant["environment"], grant["operation"], grant["mig_receipt"],
        grant["token_sha256"], expiry.astimezone(timezone.utc).isoformat(timespec="microseconds"),
        grant["workspace"], grant["revision"],
    ], separators=(",", ":")).encode()
    try:
        key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(config["mig_public_key"]))
        key.verify(bytes.fromhex(grant["mig_signature"]), payload)
    except InvalidSignature as error:
        raise ValueError("Invalid MIG signature") from error
    return grant, min(expiry, claim_expiry, lease_until)


def persist_release(path, record):
    with NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as pending:
        json.dump(record, pending)
        pending.flush()
        os.fsync(pending.fileno())
    os.replace(pending.name, path)
    directory = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def release_command(*args, timeout):
    return subprocess.run(
        ["/usr/bin/docker", "--host", "unix:///var/run/docker.sock", *args],
        check=True, capture_output=True, text=True, timeout=timeout, env=RELEASE_ENV,
        stdin=subprocess.DEVNULL,
    ).stdout


def deploy_release(grant):
    workspace = grant["workspace"]
    revision = grant["revision"]
    container = "command-dev-" + RELEASE_WORKSPACE_ID
    rows = json.loads(docker("container", "inspect", container))
    if (len(rows) != 1 or rows[0]["Name"] != "/" + container
            or not rows[0]["State"]["Running"] or rows[0]["Config"]["User"] != "1000:1000"
            or "PROJECT_NAME=" + workspace not in rows[0]["Config"]["Env"]):
        raise ValueError("Assigned workspace is unavailable")
    container_id = rows[0]["Id"]
    if not CONTAINER_ID.fullmatch(container_id):
        raise ValueError("Invalid workspace container identity")
    # This is the existing private preview build. Arguments are fixed or
    # verified above, and package code runs only as the existing workspace user.
    release_command("exec", "--user", "1000:1000", container_id, "/bin/sh", "-ec", """
        cd "/projects/$1/repository"
        test "$(git branch --show-current)" = smartdev-dev-local
        test "$(git rev-parse HEAD)" = "$2"
        test -z "$(git status --porcelain)"
        repository="$PWD"
        stage=$(mktemp -d "../.release-XXXXXXXX")
        stage=$(realpath "$stage")
        trap 'rm -rf -- "$stage"' EXIT
        git archive "$2:portal" > "$stage/source.tar"
        mkdir "$stage/portal"
        tar -xf "$stage/source.tar" -C "$stage/portal"
        cd "$stage/portal"
        npm ci --ignore-scripts --prefer-offline --no-audit --no-fund
        NEXT_TELEMETRY_DISABLED=1 SMARTDEV_PREVIEW_BASE_PATH="/@validation/$1/apps/dev-preview" npm run build
        cd "$repository"
        test "$(git rev-parse HEAD)" = "$2"
        test -z "$(git status --porcelain)"
        test -f "$stage/portal/.next/BUILD_ID"
        if test -d portal/.next; then
            mv portal/.next "$stage/previous"
        fi
        if ! mv "$stage/portal/.next" portal/.next; then
            if test -d "$stage/previous"; then mv "$stage/previous" portal/.next; fi
            exit 1
        fi
    """, "smartdev-release", workspace, revision, timeout=130)
    # Continue through the same owner-side publication control used by
    # the prior manual release, and verify the new preview generation.
    release_command("exec", "smart-dev-ide-browser-api-1", "python", "-c", """
import os, time, sys
from uuid import UUID
import httpx
from publishing import ApplicationAction, publication_control, published
project, workspace = UUID(sys.argv[1]), sys.argv[2]
path = '/@validation/' + workspace + '/apps/dev-preview/'
with httpx.Client(base_url=os.environ['CODER_API_URL'],
                  headers={'Coder-Session-Token': os.environ['CODER_TOKEN']}, timeout=6) as client:
    def generation():
        response = client.get(path + '.__smartdev/revision')
        response.raise_for_status()
        return response.json()['generation']
    previous = generation()
    publication_control(project, ApplicationAction(action='restart'))
    for _ in range(24):
        if generation() > previous and published(project)['ready'] and client.get(path).status_code == 200:
            break
        time.sleep(2)
    else:
        raise RuntimeError('Private DEV preview did not verify.')
""", grant["project_id"], workspace, timeout=65)
    if release_command("exec", "--user", "1000:1000", container_id, "/usr/bin/git",
                       "-C", "/projects/" + workspace + "/repository",
                       "rev-parse", "HEAD", timeout=5).strip() != revision:
        raise ValueError("Deployed checkout no longer matches approved revision")


def task_release(request, receipt, deploy=deploy_release, state=RELEASE_STATE, config_path=RELEASE_CONFIG):
    grant, deadline = verify_release(request, json.loads(config_path.read_text()))
    receipt.update(operation=RELEASE_OPERATION, project_id=grant["project_id"],
                   workspace=grant["workspace"], revision=grant["revision"],
                   approval_id=grant["id"], claim_id=request["claim_id"],
                   task_id=grant["task_id"], execution_id=grant["execution_id"],
                   mig_receipt=grant["mig_receipt"], environment="dev")
    path = state / (grant["id"] + ".json")
    with (state / (grant["id"] + ".lock")).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.exists():
            previous = json.loads(path.read_text())
            if (any(previous[key] != receipt[key] for key in
                    ("claim_id", "project_id", "revision", "workspace", "mig_receipt", "task_id", "execution_id"))):
                raise ValueError("Release grant already consumed")
            if previous["status"] == "started":
                previous["status"] = "unconfirmed"
                persist_release(path, previous)
            receipt.clear()
            receipt.update(previous)
            return
        if request.get("recover_only") or deadline <= datetime.now(timezone.utc):
            receipt.update(status="expired")
            return
        receipt.update(status="started")
        persist_release(path, receipt)
        try:
            deploy(grant)
        except (OSError, subprocess.SubprocessError, ValueError, KeyError):
            receipt.update(status="unconfirmed")
        else:
            receipt.update(status="observed", deployed_revision=grant["revision"])
        persist_release(path, receipt)


def handle(stream, enrolled):
    receipt = {"id": str(uuid4()), "time": datetime.now(timezone.utc).isoformat()}
    try:
        line = stream.readline(2049)
        if not line.endswith(b"\n") or len(line) > 2048:
            raise ValueError("Invalid request frame")
        request = json.loads(line)
        if isinstance(request, dict) and request.get("operation") == "codex.version":
            task_probe(request, receipt)
        elif isinstance(request, dict) and request.get("operation") == RELEASE_OPERATION:
            task_release(request, receipt)
        elif isinstance(request, dict):
            for key, allowed in (("operation", OPERATIONS), ("service", SERVICES)):
                if isinstance(request.get(key), str) and request[key] in allowed:
                    receipt[key] = request[key]
        if "status" not in receipt:
            result = execute(request, enrolled)
            receipt.update(status="observed", result=result)
    except (ValueError, TypeError, KeyError, AttributeError, FileNotFoundError):
        receipt.update(status="denied")
    except (OSError, subprocess.SubprocessError):
        receipt.update(status="unconfirmed")
    payload = json.dumps(receipt)
    print(payload, file=sys.stderr, flush=True)
    stream.write((payload + "\n").encode())
    stream.flush()


def main():
    if os.environ.get("LISTEN_PID") != str(os.getpid()) or os.environ.get("LISTEN_FDS") != "1":
        raise SystemExit("Use socket activation")
    with open("/etc/smartdev-operator/services", encoding="utf-8") as config:
        enrolled = set(config.read().split())
    if not enrolled or not enrolled <= SERVICES:
        raise SystemExit("Invalid enrollment")
    with socket.socket(fileno=3) as listener:
        while True:
            connection, _ = listener.accept()
            with connection:
                connection.settimeout(5)
                try:
                    with connection.makefile("rwb") as stream:
                        handle(stream, enrolled)
                except OSError:
                    continue


if __name__ == "__main__":
    main()
