"""Enroll the deployment's authorized pinned workspaces with its own Coder."""
import base64
import hashlib
import hmac
import json
from pathlib import Path
import os
import secrets
import shutil
import subprocess
import sys
import time
from uuid import UUID

from projects import load_manifest


def workspace_name(project_id):
    return "p-" + base64.b32encode(UUID(project_id).bytes).decode().rstrip("=").lower()


def write_atomic(path, contents, mode=0o600):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(contents)
    temporary.chmod(mode)
    temporary.replace(path)


def prepare(data, selected):
    template = data / "coder-template"
    template.mkdir(mode=0o755, exist_ok=True)
    # The bootstrap uses umask 077. Coder reads this directory as a non-root user.
    template.chmod(0o755)
    for source in Path("coder-template").iterdir():
        shutil.copyfile(source, template / source.name)
        (template / source.name).chmod(0o644)
    environment = dict(line.split("=", 1) for line in Path(".env").read_text().splitlines())
    key = bytes.fromhex(environment["SMD_MEMORY_KEY"])
    workspaces = {workspace_name(project["id"]): project for project in selected}
    for name, project in workspaces.items():
        directory = data / "workspace-config" / name
        directory.mkdir(parents=True, mode=0o755, exist_ok=True)
        os.chown(directory, 0, 1000)
        directory.chmod(0o750)
        memory = {"url": f'{environment["SMD_PORTAL_ORIGIN"]}/api/workspace-memory/{project["id"]}',
                  "token": hmac.new(key, f'memory:{project["id"]}'.encode(), hashlib.sha256).hexdigest(),
                  "environment": environment["SMD_ENVIRONMENT"]}
        # The client credential is visible only through the workspace's own mounted directory.
        write_atomic(directory / "memory.json", json.dumps(memory), 0o444)
    deployment = {"workspaces": workspaces, "data_dir": str(data),
                  "workspace_image": environment["SMD_WORKSPACE_IMAGE"]}
    write_atomic(template / "deployment.json", json.dumps(deployment, sort_keys=True), 0o644)
    with Path(".env").open("a") as configuration:
        configuration.write(f'SMD_DOCKER_GROUP={Path("/var/run/docker.sock").stat().st_gid}\n')
    password = data / "coder-owner-password"
    if not password.exists():
        write_atomic(password, secrets.token_urlsafe(36) + "\n")
    coder_session = data / "coder-session"
    coder_session.mkdir(mode=0o755, exist_ok=True)
    os.chown(coder_session, 0, 65532)
    coder_session.chmod(0o750)
    owner = coder_session / "owner.json"
    email = f'owner@{(data / "coder-host").read_text().strip()}'
    write_atomic(owner, json.dumps({"email": email, "password": password.read_text().strip()}), 0o440)
    os.chown(owner, 0, 65532)


def invoke(args, payload=None, *, timeout=600, check=True):
    command = subprocess.run(args, input=payload, stdout=subprocess.PIPE, text=True, timeout=timeout)
    if check and command.returncode:
        raise RuntimeError(f"{args[0]} failed (status {command.returncode}) during workspace enrollment")
    return command


def enroll(project_id, manifest_path):
    selected = load_manifest(manifest_path)
    profile = Path("coder-template/command-codex-workspace")
    if profile.is_file():
        import shutil
        shutil.copyfile(profile,"/etc/apparmor.d/command-codex-workspace")
        invoke(["apparmor_parser","-r","/etc/apparmor.d/command-codex-workspace"])
    data = Path.cwd().parent.parent / "data"
    coder_host = (data / "coder-host").read_text().strip()
    email = f"owner@{coder_host}"
    stack = ["docker", "compose", "--project-name", f"smd-{project_id}",
             "-f", "compose.yaml", "-f", "enrollment-compose.yaml"]
    if Path("app-networks.json").is_file():
        stack.extend(("-f", "app-networks.json"))
    credentials = {"email": email, "password": (data / "coder-owner-password").read_text().strip()}
    token = invoke([*stack, "exec", "-T", "api", "python", "/app/smd_enrollment.py", "start"],
                   json.dumps(credentials), timeout=30).stdout.strip()
    if not token or any(char.isspace() for char in token):
        raise ValueError("Coder did not return a session token")
    marker = data / "coder-owner-ready"
    if not marker.exists():
        if (data / "Caddyfile-enrolled").exists():
            write_atomic(data / "Caddyfile", (data / "Caddyfile-enrolled").read_text(), 0o644)
        invoke([*stack, "up", "-d", "--force-recreate", "--no-deps", "--no-build", "--pull", "never", "edge"])
        write_atomic(marker, "ready\n")
    coder = [*stack, "exec", "-T", "coder", "sh", "-c",
             'IFS= read -r CODER_SESSION_TOKEN; export CODER_SESSION_TOKEN; exec coder --url http://127.0.0.1:7080 "$@"', "coder"]
    workspace_image = json.loads((data / "coder-template" / "deployment.json").read_text())["workspace_image"]
    if invoke(["docker", "image", "inspect", workspace_image], check=False).returncode:
        if "@sha256:" not in workspace_image:
            raise ValueError("The pinned workspace image is missing from the offline release")
        invoke(["docker", "pull", workspace_image])
    existing = coder_workspaces(coder, token)
    deployed = data / "coder-template.sha256"
    template_key = hashlib.sha256()
    for file in sorted((data / "coder-template").iterdir()):
        template_key.update(hashlib.sha256(file.read_bytes()).digest())
    template_hash = template_key.hexdigest()
    template_changed = not deployed.exists() or deployed.read_text().strip() != template_hash
    if template_changed:
        invoke([*coder, "templates", "push", "smartdev-deployed", "--directory", "/standalone-template", "--yes"],
               token + "\n")
        write_atomic(deployed, template_hash + "\n")
    for project in selected:
        name = workspace_name(project["id"])
        if name not in existing:
            invoke([*coder, "create", name, "--template", "smartdev-deployed", "--yes"], token + "\n")
        elif template_changed:
            invoke([*coder, "update", name], token + "\n")
        elif existing[name]["latest_build"]["status"] == "stopped":
            invoke([*coder, "start", name], token + "\n")
        verify_checkout(project, name)
    verify_agents(coder, token, {workspace_name(project["id"]) for project in selected})
    invoke([*stack, "exec", "-T", "api", "python", "/app/smd_enrollment.py", "finish"],
           json.dumps(selected), timeout=30)
    if Path("workspace-tools/chromium.tar.gz").is_file():
        from workspace_tools import install
        install(data, [workspace_name(project["id"]) for project in selected])
    write_atomic(data / "verified-projects.json", json.dumps(selected) + "\n")


def coder_workspaces(command, token):
    output = invoke([*command, "list", "--output", "json"], token + "\n", timeout=10).stdout
    return {workspace["name"]: workspace for workspace in json.loads(output)}


def verify_agents(command, token, names):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        connected = set()
        for name, workspace in coder_workspaces(command, token).items():
            for resource in workspace["latest_build"].get("resources") or []:
                for agent in resource.get("agents") or []:
                    if agent["name"] == "dev" and agent["status"] == "connected":
                        connected.add(name)
        if names <= connected:
            return
        time.sleep(3)
    raise RuntimeError("One or more deployed Coder workspace agents did not connect")


def verify_checkout(project, name):
    project_filter = f'label=smartdev.project.id={project["id"]}'
    revision_filter = f'label=smartdev.project.revision={project["revision"]}'
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        matches = invoke(["docker", "ps", "-q", "--filter", project_filter, "--filter", revision_filter])
        containers = matches.stdout.split()
        if len(containers) > 1:
            raise ValueError("Multiple workspaces claim the same deployment project and revision")
        if containers:
            checkout = invoke(["docker", "exec", "--user", "1000:1000", containers[0], "sh", "-ec",
                               'curl -fsS --max-time 2 http://127.0.0.1:13337/healthz >/dev/null; '
                               'curl -fsS --max-time 2 http://127.0.0.1:13338/ >/dev/null; '
                               'git -C "$1" rev-parse HEAD', "workspace-check", f"/projects/{name}/repository"],
                              check=False, timeout=10)
            if checkout.returncode == 0 and checkout.stdout.strip() == project["revision"]:
                return
        time.sleep(3)
    raise RuntimeError(f"Coder workspace Git checkout did not verify for {project['id']}")


if __name__ == "__main__":
    enroll(sys.argv[1], Path(sys.argv[2]))
