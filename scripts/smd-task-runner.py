"""Claim owner-approved SMD jobs and advance their durable deployment state."""

import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
from secrets import token_urlsafe
import shlex
import stat
import subprocess
import socket
import sys
from tempfile import NamedTemporaryFile
from time import time
from urllib.error import HTTPError
from urllib.request import Request, build_opener
from uuid import UUID

from smd_internal_dns import DNSBlocked, DeploymentDNS
from smd_qa_ingress import QA_ZONE, expected_hosts, issue_certificate, register
from smd_cloud_init import cloud_init, generate_receipt_key, https_url, release_parameters
from smd_vultr_create import (
    MONTHLY_COST_USD, OS_ID, PLAN, REGION, CreateUnconfirmed, NoRedirect, OfferChanged,
    create_or_reconcile, instance_ipv4, make_request,
)

PICKER = Path(__file__).with_name("smd-task-picker.py")
OFFER = (REGION, PLAN, OS_ID, MONTHLY_COST_USD)
ORIGIN = "https://smartdev.youkandoit.com"
RECOVERABLE = (OSError, subprocess.SubprocessError, ValueError, KeyError)
MAX_INSTALLER_BYTES = 1024 * 1024
RECEIPT_TIMEOUT = 3600
PROVIDER_IP_TIMEOUT = 600

REMOTE_INNER_SUBMIT = r'''import json,os,sys
from uuid import UUID
from external_mcp_tasks import ScopedTasks
from governance import task_navigation
from main import projects
from store import database

payload=json.load(sys.stdin)
deployment=str(UUID(payload["deployment_id"]))
project=UUID(payload["project_id"])
if os.getenv("SMD_PORTAL_TASK") != deployment:
    raise SystemExit("Deployment identity mismatch")
with database() as db:
    owner=db.execute("SELECT id FROM smartdev_owner WHERE singleton").fetchone()["id"]
client=ScopedTasks({"project_ids":[project],"owner_id":owner},
                   projects=projects,task_navigation=task_navigation)
prompt=("Tenant-scoped environment fix request. Target deployment: "+deployment+
        ". Explicit environment: "+payload["environment"]+
        ". Work only in this deployment and project. Do not route to another workspace. "
        "Return the actual executor and durable receipt.\n\n"+payload["instruction"])
result=client.create_task(project,prompt,payload["request_id"],title=payload["title"],
                          scope="unassigned",work_key="environment-fix:"+deployment+":"+payload["environment"])
print(json.dumps(result,separators=(",",":")))
'''

REMOTE_LAUNCHER = r'''import json,subprocess,sys,urllib.request
payload=json.load(sys.stdin)
observed=urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
    "http://169.254.169.254/v1/instance-v2-id",timeout=4).read().decode().strip()
if observed != payload["instance_id"]:
    raise SystemExit("Instance identity mismatch")
compose="smd-"+payload["deployment_project_id"]
ids=subprocess.check_output(["docker","ps","-q","--filter","label=com.docker.compose.project="+compose,
                             "--filter","label=com.docker.compose.service=api"],text=True).split()
if len(ids)!=1:
    raise SystemExit("Registered deployment API unavailable")
result=subprocess.run(["docker","exec","-i",ids[0],"python","-c",''' + repr(REMOTE_INNER_SUBMIT) + r'''],
                      input=json.dumps(payload),text=True,capture_output=True,timeout=45)
if result.returncode or not result.stdout.strip():
    raise SystemExit("Remote task intake unavailable")
reply=json.loads(result.stdout)
print(json.dumps(reply,separators=(",",":")))
'''


def picker(*arguments, token=None):
    env = os.environ.copy()
    if token:
        env["SMD_TASK_CLAIM_TOKEN"] = token
    command = [sys.executable, str(PICKER), "--password-file",
               os.environ["SMARTDEV_OWNER_PASSWORD_FILE"], *arguments]
    result = subprocess.run(command, env=env, capture_output=True, timeout=40, check=True)
    return json.loads(result.stdout)


def report(claim, status, note, instance=None, revision=None):
    command = ["progress", str(UUID(claim["id"])), status, note]
    if instance:
        command.extend(("--instance-id", str(UUID(instance))))
    if revision:
        command.extend(("--revision", revision))
    return picker(*command, token=claim["claim_token"])


def save(path, value):
    with NamedTemporaryFile("w", dir=path.parent, prefix=".smd-", delete=False) as temporary:
        pending = Path(temporary.name)
        try:
            json.dump(value, temporary)
            temporary.flush()
            os.fsync(temporary.fileno())
            pending.replace(path)
            sync_directory(path.parent)
        finally:
            pending.unlink(missing_ok=True)


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def check_installer(claim):
    url, digest, _ = release_parameters(claim)
    opener = build_opener(NoRedirect)
    with opener.open(Request(url), timeout=20) as reply:
        payload = reply.read(MAX_INSTALLER_BYTES + 1)
    if not payload.startswith(b"#!") or len(payload) > MAX_INSTALLER_BYTES:
        raise ValueError("The pinned installer is missing a supported script header or exceeds the size limit.")
    if sha256(payload).hexdigest() != digest:
        raise ValueError("The downloaded installer does not match the pinned SHA-256.")


def finish(path, record, status, note, instance=None, revision=None):
    record["phase"] = "finished"
    record["result"] = [status, note, instance, revision]
    record.pop("receipt_key", None)
    save(path, record)
    finish_reporting(path, record)


def finish_reporting(path, record):
    result = record["result"]
    if isinstance(result, dict):
        report(record["claim"], result["status"], result["note"],
               result.get("instance_id"), result.get("revision"))
    else:
        report(record["claim"], *result)
    path.unlink()
    sync_directory(path.parent)


def verify_claim(claim):
    if tuple(claim.get(x) for x in ("region", "plan", "os_id", "monthly_cost_usd")) != OFFER:
        raise ValueError("The queued deployment does not match the approved Vultr offer.")
    task = str(UUID(claim["id"]))
    if claim.get("provider_tag") != "smd-" + task:
        raise ValueError("The queued deployment does not carry the expected provider tag.")
    UUID(claim["project_id"])
    release_parameters(claim)
    return task


def vultr_request():
    path = Path(os.getenv("SMARTDEV_VULTR_TOKEN_FILE", "/root/.secrets/vultr-api-token"))
    try:
        return make_request(path.read_text())
    except (OSError, ValueError):
        raise ValueError("The Vultr token file is missing, unreadable or empty.") from None


def pinned_ssh_key(claim):
    try:
        configured = str(UUID(os.getenv("SMARTDEV_SMD_VULTR_SSH_KEY_ID", "")))
    except ValueError:
        raise DNSBlocked("The runner's owner Vultr SSH key ID is missing or invalid.") from None
    if claim.get("ssh_key_id") != configured:
        raise DNSBlocked("The approved owner SSH key differs from the runner's configured key.")
    return configured


def submit_environment_fix(root, claim, run=subprocess.run):
    deployment = str(UUID(claim["deployment_id"]))
    instance = str(UUID(claim["instance_id"]))
    UUID(claim["project_id"])
    UUID(claim["deployment_project_id"])
    UUID(claim["request_id"])
    UUID(claim["claim_id"])
    ipv4 = str(claim["ipv4"])
    if claim["environment"] not in {"DEV", "QA", "DEPLOYED"}:
        raise ValueError("Environment binding is invalid.")
    identity = root / "recovery" / deployment
    known_hosts = identity.with_name(identity.name + ".known_hosts")
    if (not identity.is_file() or not known_hosts.is_file()
            or stat.S_IMODE(identity.stat().st_mode) != 0o600):
        raise ValueError("The enrolled deployment SSH binding is unavailable.")
    payload = {key: claim[key] for key in (
        "request_id", "deployment_id", "deployment_project_id", "project_id",
        "instance_id", "environment", "title", "instruction"
    )}
    command = [
        "ssh", "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
        "-o", "ConnectTimeout=8", "-o", "StrictHostKeyChecking=yes",
        "-o", "UserKnownHostsFile=" + str(known_hosts), "-i", str(identity),
        "root@" + ipv4, "python3", "-c", shlex.quote(REMOTE_LAUNCHER),
    ]
    result = run(command, input=json.dumps(payload), text=True, capture_output=True,
                 timeout=60, check=True)
    remote = json.loads(result.stdout)
    if not isinstance(remote, dict) or not remote.get("request_id") or not remote.get("task_id"):
        raise ValueError("The remote deployment did not return a task receipt.")
    return {"executor": "enrolled-smd-runner", "deployment_id": deployment,
            "instance_id": instance, "environment": claim["environment"], "remote": remote}


def process_environment_fix(root):
    claim = picker("fix-claim")
    if not claim:
        return False
    status, receipt, reason = "delivered", None, None
    try:
        receipt = submit_environment_fix(root, claim)
    except ValueError as error:
        status, reason = "denied", str(error)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, KeyError):
        status, reason = "unconfirmed", "The enrolled operator could not confirm remote task delivery."
    arguments = ["fix-result", claim["request_id"], claim["claim_id"], status]
    if receipt:
        with NamedTemporaryFile("w", dir=root, prefix=".smd-fix-receipt-", delete=False) as pending:
            json.dump(receipt, pending)
            receipt_path = Path(pending.name)
        try:
            picker(*arguments, "--receipt-file", str(receipt_path))
        finally:
            receipt_path.unlink(missing_ok=True)
    else:
        picker(*arguments, "--failure-reason", reason)
    return True


def recovery_public_key(path, task):
    directory = path.parent / "recovery"
    directory.mkdir(mode=0o700, exist_ok=True)
    identity = directory / str(UUID(task))
    if not identity.exists():
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(identity)],
                       capture_output=True, timeout=10, check=True)
    identity.chmod(0o600)
    return subprocess.run(["ssh-keygen", "-y", "-f", str(identity)],
                          capture_output=True, text=True, timeout=10, check=True).stdout.strip()


def prepare(path, record):
    claim = record["claim"]
    verify_claim(claim)
    check_installer(claim)
    pinned_ssh_key(claim)
    ingress = os.getenv("SMARTDEV_SMD_QA_INGRESS_IP")
    if ingress:
        if claim.get("dns_zone") != QA_ZONE:
            raise DNSBlocked("The approved hostnames must use the QA ingress zone.")
        for host in expected_hosts(claim, QA_ZONE):
            observed = {row[4][0] for row in socket.getaddrinfo(host, 443, socket.AF_INET)}
            if observed != {ingress}:
                raise DNSBlocked("A QA hostname does not resolve exclusively to Command Center.")
        record["qa_ingress_ip"] = ingress
        record["upstream_tls"] = issue_certificate(claim)
    else:
        DeploymentDNS.configured().preflight(claim)
    record["receipt_key"] = generate_receipt_key()
    record["phase"] = "provisioning"
    save(path, record)


def provision(path, record):
    claim = record["claim"]
    task = verify_claim(claim)
    key = record["receipt_key"]
    state = record.setdefault("provider", {})
    origin = https_url(ORIGIN, origin=True)
    callback = f"{origin}/api/smd-deploy-tasks/{task}/receipt"
    ingress = record.get("qa_ingress_ip")
    recovery_key = recovery_public_key(path, task) if ingress and not state.get("create_started") else None
    bootstrap = cloud_init(claim, callback, key["private_key"], ingress=ingress,
                           upstream_tls=record.get("upstream_tls"), recovery_key=recovery_key)
    request = vultr_request()
    ssh_key = pinned_ssh_key(claim)
    if not state.get("create_started") and not state.get("instance_id"):
        report(claim, "processing", "Pinned installer verified. Checking the approved Vultr offer.")
    try:
        instance = create_or_reconcile(request, task, state, lambda _: save(path, record),
                                       bootstrap, ssh_key_id=ssh_key)
    except CreateUnconfirmed as error:
        # Preserve the original HTTP/response blocker while inventory catches up.
        if "provider_alert" not in record:
            record["provider_alert"] = str(error) + " The runner will reconcile by tag."
            save(path, record)
        report(claim, "needs_attention", record["provider_alert"])
        return
    except OfferChanged as error:
        finish(path, record, "needs_attention", str(error))
        return
    except HTTPError as error:
        report(claim, "needs_attention", f"Vultr inventory or offer check returned HTTP {error.code}.")
        return
    if record.get("upstream_tls"):
        record["upstream_tls"].pop("key", None)
    record["phase"] = "assigning_dns"
    record["provider_since"] = time()
    save(path, record)
    report(claim, "processing", "Vultr VM identified. Waiting for its public IPv4.", instance)


def assign_dns(path, record):
    claim = record["claim"]
    state = record["provider"]
    instance = state["instance_id"]
    try:
        ipv4 = instance_ipv4(vultr_request(), claim["id"], instance)
        if ipv4 and state.get("ipv4") not in (None, ipv4):
            raise DNSBlocked("The Vultr IPv4 changed after it was confirmed. Review the existing DNS assignment.")
        if ipv4 is not None:
            state["ipv4"] = ipv4
            save(path, record)
            if record.get("qa_ingress_ip"):
                try:
                    register(claim, ipv4, record["upstream_tls"]["certificate"])
                except (OSError, ValueError, subprocess.SubprocessError):
                    raise DNSBlocked("QA ingress could not validate or activate the deployment routes.") from None
            else:
                dns_state = record.setdefault("dns", {})
                DeploymentDNS.configured().assign(claim, ipv4, dns_state, lambda: save(path, record))
    except (DNSBlocked, CreateUnconfirmed) as error:
        report(claim, "needs_attention", str(error), instance)
        return
    except HTTPError as error:
        report(claim, "needs_attention", f"Vultr could not verify the deployed instance (HTTP {error.code}).",
               instance)
        return
    if ipv4 is None:
        if time() - record["provider_since"] >= PROVIDER_IP_TIMEOUT:
            report(claim, "needs_attention", "Vultr has not reported a verified public IPv4 for the VM.", instance)
        return
    message = ("Verified the Vultr IPv4 and registered the QA ingress routes."
               if record.get("qa_ingress_ip") else "Verified the Vultr IPv4 and assigned the deployment DNS names.")
    report(claim, "processing", message, instance)
    key = record["receipt_key"]["public_key"]
    picker("receipt-enrollment", claim["id"], instance, key, "--ipv4", ipv4,
           token=claim["claim_token"])
    record["phase"] = "awaiting_receipt"
    record["receipt_since"] = time()
    record.pop("receipt_key")
    save(path, record)
    report(claim, "processing",
           "Waiting for verified HTTPS access to the portal, Coder, and deployed apps.", instance)


def poll_receipt(path, record):
    claim = record["claim"]
    instance = record["provider"]["instance_id"]
    if picker("receipt", str(UUID(claim["id"])))["verified"] is True:
        finish(path, record, "deployed", "Signed installation and service health receipt verified.",
               instance, claim["release_revision"])
    elif time() - record["receipt_since"] >= RECEIPT_TIMEOUT and not record.get("timeout_reported"):
        report(claim, "needs_attention",
               "The VM exists. Its installer has not returned a signed health receipt within one hour.",
               instance)
        record["timeout_reported"] = True
        save(path, record)


def process(path, record):
    phase = record["phase"]
    if phase == "finished" and record.get("result"):
        finish_reporting(path, record)
        return
    if phase == "executing":
        # The previous runner could send a create independently of this journal.
        finish(path, record, "needs_attention",
               "The previous executor was interrupted. Inspect the exact Vultr tag before proceeding.")
        return
    if phase == "claimed":
        try:
            prepare(path, record)
        except DNSBlocked as error:
            finish(path, record, "needs_attention", str(error))
            return
        except HTTPError as error:
            finish(path, record, "needs_attention", f"Pinned installer download returned HTTP {error.code}.")
            return
        except (OSError, ValueError, KeyError, subprocess.SubprocessError):
            finish(path, record, "needs_attention",
                   "Release preflight failed. Check the pinned installer URL, SHA-256, revision and runner configuration.")
            return
    if record["phase"] == "provisioning":
        try:
            provision(path, record)
        except ValueError as error:
            # DNS/SSH preconditions are checked again before the provider request.
            if isinstance(error, DNSBlocked):
                report(record["claim"], "needs_attention", str(error))
            else:
                report(record["claim"], "needs_attention", "Vultr preflight did not confirm the authorized offer and owner SSH key.")
    if record["phase"] == "assigning_dns":
        assign_dns(path, record)
    if record["phase"] == "awaiting_receipt":
        poll_receipt(path, record)


def process_journal(path, record):
    if record["phase"] == "claiming":
        try:
            claim = picker("claim", record["task"], token=record["token"])
        except subprocess.CalledProcessError as error:
            if not (error.stderr or b"").startswith(b"SmartDev returned HTTP 409:"):
                raise
            # Another owner worker already holds this claim.
            path.unlink()
            sync_directory(path.parent)
            return
        if claim["id"] != record["task"] or claim["claim_token"] != record["token"]:
            raise ValueError("The queue did not acknowledge the durable claim token.")
        record = {"claim": claim, "phase": "claimed"}
        save(path, record)
    process(path, record)


def cycle(root):
    legacy = root / "claimed.json"
    if legacy.exists():
        record = json.loads(legacy.read_text())
        task = record.get("task") if record["phase"] == "claiming" else record["claim"]["id"]
        destination = root / (str(UUID(task)) + ".json")
        if destination.exists():
            raise ValueError("Conflicting deployment journals require recovery.")
        legacy.replace(destination)
        sync_directory(root)

    if os.getenv("SMARTDEV_SMD_ENVIRONMENT_FIXES") == "1":
        process_environment_fix(root)
    pending = sorted(root.glob("*.json"), key=lambda path: path.stat().st_mtime)
    errors = False
    # Retain each in-flight claim while allowing new jobs to start on subsequent ticks.
    if pending:
        path = pending[0]
        try:
            process_journal(path, json.loads(path.read_text()))
        except RECOVERABLE:
            errors = True
        if path.exists():
            path.touch()

    active = {path.stem for path in pending}
    job = next((entry for entry in picker("list")
                if entry["status"] == "queued" and entry["id"] not in active), None)
    if job is not None:
        task = str(UUID(job["id"]))
        path = root / (task + ".json")
        record = {"phase": "claiming", "task": task, "token": token_urlsafe(32)}
        save(path, record)
        process_journal(path, record)
    if errors:
        raise ValueError("A retained deployment journal could not advance.")


def main():
    os.umask(0o077)
    root = Path(os.environ.get("STATE_DIRECTORY", "/var/lib/smartdev-smd-runner"))
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (root / "runner.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        cycle(root)


if __name__ == "__main__":
    try:
        main()
    except RECOVERABLE:
        sys.exit("SMD runner could not complete the queue cycle. Check service configuration and retained state.")
