"""Generate a one-shot cloud-init script for a pinned SmartDev release."""

from ipaddress import IPv4Address
from hashlib import file_digest
import base64
import gzip
from pathlib import Path
import json
import re
import shlex
import subprocess
from urllib.parse import urlsplit
from uuid import UUID


BOOTSTRAP = r'''
set -euo pipefail
umask 077
if [[ -n "$recovery_key" ]]; then
    install -d -m 0700 /root/.ssh
    authorized=/root/.ssh/authorized_keys
    touch "$authorized"
    chmod 0600 "$authorized"
    entry="from=\"$recovery_ingress\",restrict $recovery_key smartdev-qa-$task_id"
    if ! grep -Fqx -- "$entry" "$authorized"; then
        printf '\n%s\n' "$entry" >> "$authorized"
    fi
fi
work=$(mktemp -d)
git_tokens="/run/smartdev-git/$project_id"
cleanup() {
    rm -f -- "$git_tokens/"*.token "$git_tokens/"*.bundle "$git_tokens/"*.part
    rmdir -- "$git_tokens" 2>/dev/null || true
    rm -rf -- "$work"
}
trap cleanup EXIT

printf '%s' "$manifest_json" > "$work/manifest.json"
python3 - "$work/manifest.json" "$work/ipv4" <<'PY_DNS'
import ipaddress
import json
import socket
import subprocess
import sys
import time

manifest_path, ip_path = sys.argv[1:]
with open(manifest_path) as source:
    manifest = json.load(source)
hosts = [manifest["deployment_hostname"], manifest["coder_hostname"],
         *(host for project in manifest["projects"] for host in (project["hostname"],project.get("preview_hostname")) if host)]
if hosts:
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        addresses = json.loads(subprocess.check_output(["ip", "-j", "-4", "address", "show", "scope", "global"]))
        own_ips = {item["local"] for interface in addresses for item in interface.get("addr_info", [])
                   if ipaddress.IPv4Address(item["local"]).is_global}
        try:
            groups = [{entry[4][0] for entry in socket.getaddrinfo(host, 443, socket.AF_INET)}
                      for host in hosts]
            ingress = manifest.get("qa_ingress_ip")
            expected = {ingress} if ingress else own_ips
            if len(groups[0]) == 1 and groups[0] <= expected and all(group == groups[0] for group in groups) and len(own_ips) == 1:
                with open(ip_path, "w") as output:
                    output.write(next(iter(own_ips)))
                break
        except socket.gaierror:
            pass
        time.sleep(10)
    else:
        raise SystemExit("The deployment hostnames did not resolve to the approved ingress.")
PY_DNS

instance_id=$(curl --fail --silent --show-error --noproxy '*' --retry 5 --max-time 15 \
    http://169.254.169.254/v1/instance-v2-id)
[[ $instance_id =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]]
printf '%s' "$private_key" | base64 -d > "$work/key"
unset private_key

install -d -m 0700 "$git_tokens"
python3 - "$work" "$git_tokens" "/opt/smartdev/$project_id/data/git-bundles" "$task_id" "$instance_id" "${callback_url%/receipt}/git-token" <<'PY_GIT'
from base64 import b64encode
from hashlib import sha256
import json
from pathlib import Path
from secrets import token_hex
from shutil import copyfileobj
from tempfile import TemporaryDirectory
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID

work, token_dir, bundle_dir, task, instance, endpoint = sys.argv[1:]
work, token_dir, bundle_dir = map(Path, (work, token_dir, bundle_dir))
projects = json.loads((work / "manifest.json").read_text())["projects"]


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


client = build_opener(NoRedirect)
downloads = []
for project in projects:
    downloads.append((project, "git-token"))
    if project.get("build_context_sha256"):
        downloads.append((project, "build-context"))
    if project.get("qa_runtime"):
        downloads.append((project, "qa-runtime"))
for project, kind in downloads:
    selected = str(UUID(project["id"]))
    for _ in range(60):
        now, nonce = int(time.time()), token_hex(32)
        message = f"{kind}\n{task}\n{selected}\n{instance}\n{now}\n{nonce}\n".encode("ascii")
        (work / "git-message").write_bytes(message)
        signature = subprocess.check_output(
            ["openssl", "pkeyutl", "-sign", "-rawin", "-keyform", "DER",
             "-inkey", work / "key", "-in", work / "git-message"],
        )
        payload = json.dumps({
            **({"artifact": kind} if kind != "git-token" else {}),
            "project_id": selected, "instance_id": instance, "timestamp": now,
            "nonce": nonce, "signature": b64encode(signature).decode("ascii"),
        }).encode("ascii")
        request = Request(endpoint, data=payload, headers={"Content-Type": "application/json"})
        try:
            with client.open(request, timeout=540) as response:
                if kind == "qa-runtime":
                    if response.headers.get_content_type() != "application/x-smd-runtime-env":
                        raise ValueError("Unexpected QA runtime response.")
                    content = response.read(65537)
                    if not content or len(content) > 65536:
                        raise ValueError("Invalid QA runtime file size.")
                    runtime_dir = bundle_dir.parent / "project-runtime"
                    runtime_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
                    with TemporaryDirectory(prefix=".runtime-", dir=runtime_dir) as scratch:
                        pending = Path(scratch) / (selected + ".env")
                        pending.write_bytes(content)
                        pending.chmod(0o600)
                        pending.replace(runtime_dir / pending.name)
                    break
                if kind == "build-context":
                    if response.headers.get_content_type() != "application/gzip":
                        raise ValueError("Unexpected product context response.")
                    bundle_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
                    with TemporaryDirectory(prefix=".context-", dir=bundle_dir) as scratch:
                        pending = Path(scratch) / (selected + ".tar.gz")
                        digest = sha256()
                        with pending.open("xb") as target:
                            for block in iter(lambda: response.read(1024 * 1024), b""):
                                target.write(block)
                                digest.update(block)
                        if digest.hexdigest() != project["build_context_sha256"]:
                            raise ValueError("Product context differs from the approved offer.")
                        pending.replace(bundle_dir / pending.name)
                    break
                if getattr(response, "headers", None) and response.headers.get_content_type() == "application/x-git-bundle":
                    bundle_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
                    with TemporaryDirectory(prefix=".incoming-", dir=bundle_dir) as scratch:
                        pending = Path(scratch) / f"{selected}.bundle"
                        with pending.open("xb") as target:
                            copyfileobj(response, target)
                        pending.replace(bundle_dir / pending.name)
                    break
                token = json.loads(response.read(4096))["token"]
            if not isinstance(token, str) or not token or any(char.isspace() for char in token):
                raise ValueError("Received unusable Git credential.")
            with (token_dir / f"{selected}.token").open("x") as target:
                target.write(token)
            break
        except HTTPError as error:
            if error.code not in (409, 429, 500, 502, 503, 504):
                raise SystemExit(f"Command Center denied Git authorization for project {selected} (HTTP {error.code}).")
        except OSError:
            pass
        time.sleep(10)
    else:
        raise SystemExit(f"Could not obtain scoped Git authorization for project {selected}.")
PY_GIT

install -d -m 0700 "/opt/smartdev/$project_id/data"
if [[ -n "$upstream_certificate" && -n "$upstream_key" ]]; then
    install -d -m 0700 "/opt/smartdev/$project_id/data/edge-tls"
    printf '%s' "$upstream_certificate" > "/opt/smartdev/$project_id/data/edge-tls/certificate.pem"
    printf '%s' "$upstream_key" > "/opt/smartdev/$project_id/data/edge-tls/key.pem"
    unset upstream_key
fi
printf '%s\n' "$deployment_hostname" > "/opt/smartdev/$project_id/data/public-host"
printf '%s\n' "$coder_hostname" > "/opt/smartdev/$project_id/data/coder-host"
run_installer() {
    curl --fail --silent --show-error --retry 5 --proto '=https' \
        --max-time 300 "$installer_url" -o "$work/installer" &&
        printf '%s  %s\n' "$installer_sha256" "$work/installer" | sha256sum -c --status &&
        timeout 1800 bash "$work/installer" --project-id "$project_id" --revision "$revision" \
            --project-manifest "$work/manifest.json" > "$work/revision" &&
        [[ $(cat "$work/revision") == "$revision" ]]
}
run_installer || { echo 'Pinned SMD installation or health verification failed.' >&2; exit 1; }
if [[ -n "$workbench_url" ]]; then
    assets="/opt/smartdev/$project_id/data/workbench/$workbench_sha256"
    install -d -m 0700 "$assets"
    curl --fail --silent --show-error --retry 4 --proto '=https' --max-time 90 "$workbench_url" -o "$work/workbench.tgz"
    printf '%s  %s\n' "$workbench_sha256" "$work/workbench.tgz" | sha256sum -c --status
    python3 - "$work/workbench.tgz" "$assets" <<'PY_WORKBENCH'
import sys,tarfile
with tarfile.open(sys.argv[1]) as archive:
    members=archive.getmembers()
    if len(members)!=5 or not all(m.isfile() for m in members) or {m.name for m in members}!={"apps.py","enroll-api.py","managed_workspace.py","next.tar.gz","portal-components.gz"}:
        raise SystemExit("Unexpected managed workbench package")
    archive.extractall(sys.argv[2],filter="data")
PY_WORKBENCH
    cp "$assets/portal-components.gz" "$work/portal-components.gz"
    python3 "$assets/managed_workspace.py" "$project_id" "$workbench_sha256"
fi
python3 - "$work" "$portal_receiver_files" <<'PY_PORTAL_FILES'
from base64 import b64decode
from gzip import decompress
import json
from pathlib import Path
import sys
target = Path(sys.argv[1])
payload=b64decode(sys.argv[2],validate=True) if sys.argv[2] else (target/"portal-components.gz").read_bytes()
for name, contents in json.loads(decompress(payload)).items():
    if name not in {"smd_handoff_cleanup.py","runtime.py","smd_portal_generation.py","vault.py","smd_custom_ingress.py","domains.py","challenge_engine.py","smd_owner_client.py","smd_preview_domains.py","codex_access.py","preview.py","portal_handoff.py", "deployment_portal_receiver.py", "enroll_deployment_portal.py", "development.py", "041_frozen_business_objectives.sql"}:
        raise SystemExit("Unknown portal enrollment component.")
    (target / name).write_text(contents)
PY_PORTAL_FILES
python3 "$work/enroll_deployment_portal.py" "$project_id" "$task_id" "$instance_id" \
    "$portal_issuer" "https://$deployment_hostname" "$work"

'''


CALLBACK = r'''
python3 - "$work" "$task_id" "$project_id" "$instance_id" "$revision" \
    "/opt/smartdev/$project_id/data/verified-projects.json" "$portal_issuer" <<'PY_RECEIPT'
from base64 import b64encode
import json
from pathlib import Path
import subprocess
import sys
import time

work, task_id, project_id, instance_id, revision, evidence, issuer = sys.argv[1:]
work = Path(work)
manifest = json.loads((work / "manifest.json").read_text())
ipv4 = (work / "ipv4").read_text()
portal, coder = manifest["deployment_hostname"], manifest["coder_hostname"]
apps = manifest["projects"]
observed = json.loads(Path(evidence).read_text())
def identity(app):
    return (app["id"], app["revision"], app["hostname"], app.get("build_context_sha256", ""),
            app.get("tenant_setup", False), app.get("verify_installation", False),app.get("preview_hostname",""))

if sorted(map(identity, observed)) != sorted(map(identity, apps)):
    raise SystemExit("The installer did not verify every approved project revision and build context.")
checks = [(portal, "/", None), (portal, "/api/health", None), (coder, "/healthz", None)]
checks.append((portal, "/api/deployment-portal/metadata",
               {"protocol": "owner-pkce-v1", "account": "owner", "issuer": issuer,
                "task_id": task_id, "instance_id": instance_id, "portal": f"https://{portal}"}))
def matches(marker):
    if isinstance(marker, dict):
        try:
            return json.loads((work / "probe").read_bytes()) == marker
        except (OSError, ValueError):
            return False
    return not marker or (work / "probe").read_bytes() == marker.encode("ascii")
runtime = {}
for app in apps:
    if app.get("tenant_setup"):
        checks.append((app["hostname"], "/.well-known/smartdev-installation", app["build_context_sha256"]))
        runtime[app["id"]] = "awaiting_tenant_configuration"
    elif app.get("verify_installation"):
        checks.append((app["hostname"], "/.well-known/smartdev-installation", app["revision"]))
    else:
        checks.append((app["hostname"], "/", None))
    if app.get("preview_hostname"):
        checks.append((app["preview_hostname"],checks[-1][1],checks[-1][2]))
for hostname, route, marker in checks:
    for _ in range(40):
        reply = subprocess.run(
            ["curl", "--silent", "--noproxy", "*", "--proto", "=https", "--max-time", "10",
             "--output", str(work / "probe") if marker else "/dev/null", "--write-out", "%{http_code}", "--resolve",
             f"{hostname}:443:{manifest.get('qa_ingress_ip') or ipv4}",
             f"https://{hostname}{route}"],
            capture_output=True, text=True,
        )
        if (reply.returncode == 0 and len(reply.stdout) == 3 and reply.stdout.startswith("2")
                and matches(marker)):
            break
        time.sleep(5)
    else:
        raise SystemExit(f"Verified HTTPS health failed for {hostname}{route}.")

for app in apps:
    if app.get("verify_installation") and not app.get("tenant_setup"):
        hostname = app["hostname"]
        probe = subprocess.run(["curl", "--silent", "--noproxy", "*", "--proto", "=https", "--max-time", "10",
                                "--output", "/dev/null", "--write-out", "%{http_code}", "--resolve",
                                f"{hostname}:443:{manifest.get('qa_ingress_ip') or ipv4}", f"https://{hostname}/"],
                               capture_output=True, text=True)
        if probe.returncode or not probe.stdout.startswith("2"):
            runtime[app["id"]] = "runtime_unverified"

key_path=Path("/opt/smartdev")/project_id/"data/portal-access/installation.pem"
public=subprocess.check_output(["openssl","pkey","-in",str(key_path),"-pubout","-outform","DER"])
if len(public)!=44 or public[:12]!=bytes.fromhex("302a300506032b6570032100"):
    raise SystemExit("Invalid installation identity")
connections = {
    "installation_key": b64encode(public[12:]).decode("ascii"),
    "portal_access": "owner-pkce-v1",
    "ipv4": ipv4, "ssh_user": "root", "portal_url": f"https://{portal}", "coder_url": f"https://{coder}",
    "apps": [{"project_id": app["id"], "revision": app["revision"],
              **({"runtime_status": runtime[app["id"]]} if app["id"] in runtime else {}),
              **({"preview_url":"https://"+app["preview_hostname"]} if app.get("preview_hostname") else {}),
              "url": f"https://{app['hostname']}"} for app in apps],
}
canonical = json.dumps(connections, sort_keys=True, separators=(",", ":"))
(work / "message").write_text(f"{task_id}\n{project_id}\n{instance_id}\n{revision}\n{canonical}\n", encoding="ascii")
signature = subprocess.check_output(
    ["openssl", "pkeyutl", "-sign", "-rawin", "-keyform", "DER",
     "-inkey", work / "key", "-in", work / "message"],
)
(work / "receipt.json").write_text(json.dumps({
    "project_id": project_id, "instance_id": instance_id, "revision": revision,
    "connections": connections, "signature": b64encode(signature).decode("ascii"),
}))
PY_RECEIPT
rm -f "$work/key"
for ((attempt=0; attempt<120; attempt++)); do
    code=$(curl --silent --show-error --proto '=https' --max-time 15 --output /dev/null \
        --write-out '%{http_code}' --header 'Content-Type: application/json' \
        --data-binary "@$work/receipt.json" "$callback_url") || code=000
    case "$code" in
        2??) exit 0 ;;
        000|409|429|5??) sleep 15 ;;
        *) echo "Command Center rejected the signed deployment receipt (HTTP $code)." >&2; exit 1 ;;
    esac
done
echo 'Command Center did not accept the signed deployment receipt within the retry window.' >&2
exit 1
'''


def generate_receipt_key():
    key = subprocess.run(
        ["openssl", "genpkey", "-algorithm", "ED25519", "-outform", "DER"],
        capture_output=True, check=True, timeout=10,
    ).stdout
    public = subprocess.run(
        ["openssl", "pkey", "-inform", "DER", "-pubout", "-outform", "DER"],
        input=key, capture_output=True, check=True, timeout=10,
    ).stdout
    header = bytes.fromhex("302a300506032b6570032100")
    if not public.startswith(header) or len(public) != len(header) + 32:
        raise ValueError("Expected an Ed25519 public key.")
    return {
        "private_key": base64.b64encode(key).decode(),
        "public_key": base64.b64encode(public[len(header):]).decode(),
    }


def https_url(url, *, origin=False):
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None or
            parsed.fragment or any(char.isspace() for char in url) or
            (origin and (parsed.path or parsed.query))):
        raise ValueError("Expected an HTTPS URL without credentials or fragment.")
    return url


def release_parameters(claim):
    url = https_url(claim["installer_url"])
    digest = claim["installer_sha256"]
    revision = claim["release_revision"]
    if not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("The pinned installer SHA-256 is invalid.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", revision):
        raise ValueError("The pinned release revision is invalid.")
    return url, digest, revision


def cloud_init(claim, callback_url, private_key, *, ingress=None, upstream_tls=None, recovery_key=None):
    installer_url, digest, revision = release_parameters(claim)
    projects = [{
        "id": str(UUID(project["id"])),
        "name": project["name"],
        "repository": project["repository"],
        "revision": project["revision"],
        "runtime": project["runtime"],
        "hostname": project["hostname"],
        **({"preview_hostname":project["preview_hostname"]} if project.get("preview_hostname") else {}),
        **({"build_context_sha256": project["build_context_sha256"]} if "build_context_sha256" in project else {}),
        **({"qa_runtime": True} if project.get("qa_runtime_versions") else {}),
        **({"tenant_setup": True} if project.get("tenant_setup") is True else {}),
        **({"verify_installation": True} if project.get("verify_installation") is True else {}),
    } for project in claim["projects"]]
    manifest = {
        "projects": projects,
        "deployment_hostname": claim["deployment_hostname"],
        "coder_hostname": claim["coder_hostname"],
    }
    if ingress:
        manifest["qa_ingress_ip"] = ingress
    if recovery_key:
        if not ingress or not re.fullmatch(r"ssh-ed25519 [A-Za-z0-9+/]{68}(?: [^\r\n]+)?", recovery_key):
            raise ValueError("Invalid QA recovery public key.")
        recovery_key = " ".join(recovery_key.split()[:2])
    scripts = Path(__file__).resolve().parent
    from smd_portal_components import components
    workbench_url = ""
    workbench_digest = ""
    installer_path = urlsplit(installer_url)
    if re.fullmatch(r"/api/smd-release/[a-f0-9]{64}/installer\.sh",installer_path.path) and not installer_path.query:
        with (scripts.parent/"api"/"smd_workbench.tar.gz").open("rb") as archive:
            workbench_digest = file_digest(archive,"sha256").hexdigest()
        workbench_url = installer_url.rsplit("/",1)[0]+"/workbench-"+workbench_digest+".tar.gz"
    packed = json.dumps({p.name: p.read_text() for p in components(scripts)}, separators=(",", ":")).encode()
    callback = urlsplit(https_url(callback_url))
    variables = {
        "workbench_sha256":workbench_digest, "workbench_url":workbench_url,
        "portal_receiver_files": "" if workbench_url else base64.b64encode(gzip.compress(packed, mtime=0)).decode(),
        "portal_issuer": https_url(f"https://{callback.netloc}", origin=True),
        "recovery_key": recovery_key or "",
        "recovery_ingress": str(IPv4Address(ingress)) if recovery_key else "",
        "upstream_certificate": (upstream_tls or {}).get("certificate", ""),
        "upstream_key": (upstream_tls or {}).get("key", ""),
        "task_id": str(UUID(claim["id"])),
        "project_id": str(UUID(claim["project_id"])),
        "installer_url": installer_url,
        "installer_sha256": digest,
        "revision": revision,
        "callback_url": https_url(callback_url),
        "private_key": private_key,
        "manifest_json": json.dumps(manifest, separators=(",", ":")),
        "deployment_hostname": manifest["deployment_hostname"],
        "coder_hostname": manifest["coder_hostname"],
    }
    assignments = "\n".join(f"{name}={shlex.quote(value)}" for name, value in variables.items())
    return base64.b64encode(("#!/bin/bash\n" + assignments + BOOTSTRAP + CALLBACK).encode()).decode()
