"""Install the managed portal receiver on one already provisioned SmartDev deployment."""
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit
from uuid import UUID


def output(*argv):
    return subprocess.check_output(argv, text=True).strip()


def enroll(project, task, instance, issuer, portal, source):
    project, task, instance = (str(UUID(v)) for v in (project, task, instance))
    for url in (issuer, portal):
        parts = urlsplit(url)
        if not parts.hostname or parts.scheme != "https" or parts.username is not None or url != "https://" + parts.netloc:
            raise ValueError("Enrollment requires canonical HTTPS origins")
    if issuer == portal:
        raise ValueError("Issuer must be distinct from the deployment portal")
    project_name = "smd-" + project
    ids = output("docker", "ps", "-q", "--filter", "label=com.docker.compose.project=" + project_name,
                 "--filter", "label=com.docker.compose.service=api").splitlines()
    if len(ids) != 1:
        raise ValueError("Expected exactly one running API for the selected project")
    obj = json.loads(output("docker", "inspect", ids[0]))[0]
    labels = obj["Config"]["Labels"]
    root = Path(labels["com.docker.compose.project.working_dir"]).resolve(strict=True)
    data = Path("/opt/smartdev") / project / "data"
    if (data / "public-host").read_text().strip() != urlsplit(portal).netloc:
        raise ValueError("Deployment hostname does not match its installed configuration")
    mounted = {m["Destination"] for m in obj["Mounts"]}
    environment = dict(v.split("=", 1) for v in obj["Config"]["Env"])
    if environment["PORTAL_ORIGIN"].rstrip("/") != portal:
        raise ValueError("Deployment API origin does not match")
    command = list(obj["Config"]["Cmd"])
    if command[0] != "uvicorn":
        raise ValueError("Unsupported native API entry point")
    if "main:app" in command:
        command[command.index("main:app")] = "deployment_portal_receiver:app"
    elif "deployment_portal_receiver:app" not in command:
        raise ValueError("Unsupported native API application")
    command_files = ("portal_handoff.py", "deployment_portal_receiver.py", "development.py", "041_frozen_business_objectives.sql")
    content = {name:(source / name).read_bytes() for name in command_files}
    settings = {"SMD_PORTAL_TASK": task, "SMD_PORTAL_INSTANCE": instance, "SMD_PORTAL_ISSUER": issuer}
    settings["PGOPTIONS"] = "-c smartdev.install_kind=tenant"
    manifest = json.loads((data / "verified-projects.json").read_text())
    settings["SMD_PORTAL_PROJECTS"] = ",".join(str(UUID(item["id"])) for item in manifest)
    if project not in settings["SMD_PORTAL_PROJECTS"].split(","):
        raise ValueError("Deployment project is missing from its verified manifest")
    digest = sha256(json.dumps([settings, command],sort_keys=True).encode() + b"".join(content.values())).hexdigest()
    directory = data / "portal-access"
    version = directory / digest
    version.mkdir(parents=True, exist_ok=True)
    for name, body in content.items():
        target = version / name
        target.write_bytes(body)
        target.chmod(0o644)
    mounts = {"/app/" + name for name in content}
    overlay = version / "compose.json"
    overlay.write_text(json.dumps({"services": {"api": {"command": command, "environment": settings,
                                 "volumes":[f"{version/name}:/app/{name}:ro" for name in content]}}}))
    cfg = ["docker","compose","--project-directory",str(root),"--project-name",project_name]
    for envfile in (root/"images.env", root/".env"):
        if envfile.is_file():
            cfg.extend(("--env-file", str(envfile)))
    previous = labels["com.docker.compose.project.config_files"].split(",")
    def stack(files):
        return cfg + [arg for file in files for arg in ("-f", str(file))]
    next_files = [f for f in previous if not Path(f).resolve().is_relative_to(directory)] + [overlay]
    updated = stack(next_files)
    model = json.loads(output(*updated, "config","--format","json"))["services"]["api"]
    if output("docker","image","inspect",model["image"],"--format","{{.Id}}") != obj["Image"]:
        raise ValueError("Running API image differs from its compose definition")
    if any(environment.get(k) != v for k,v in model["environment"].items() if k not in settings):
        raise ValueError("Running API settings differ from its compose definition")
    if {m["target"] for m in model.get("volumes",[])} - mounts != mounted - mounts:
        raise ValueError("Running API mounts differ from its compose definition")
    up = ["up","-d","--no-deps","--no-build","--pull","never","--wait","--wait-timeout","150","api"]
    verification = ("import json,sys,urllib.request; expected=json.loads(sys.argv[1]); "
                    "actual=json.load(urllib.request.urlopen('http://127.0.0.1:8000/deployment-portal/metadata',timeout=4)); "
                    "sys.exit(actual!=expected)")
    expected = dict(issuer=issuer, portal=portal, task_id=task, instance_id=instance, protocol="owner-pkce-v1",account="owner")
    try:
        subprocess.run([*updated,*up],check=True)
        api = output(*updated,"ps","-q","api")
        subprocess.run(["docker","exec",api,"python","-c",verification,json.dumps(expected)],check=True)
        workspace_probe = """import json,sys,time
from uuid import UUID
from development import workspace_request
outstanding={UUID(p) for p in json.loads(sys.argv[1])}
deadline=time.monotonic()+120
while outstanding and time.monotonic()<deadline:
    for project in tuple(outstanding):
        try:
            status=workspace_request(project)
        except Exception:
            continue
        if status.get('chat_ready') and status.get('editor_ready') and status.get('preview_ready'):
            outstanding.remove(project)
    if outstanding: time.sleep(2)
if outstanding: raise SystemExit('Coder could not reach the registered chat, editor and preview for '+','.join(map(str,outstanding)))
"""
        subprocess.run(["docker","exec",api,"python","-c",workspace_probe,
                        json.dumps(settings["SMD_PORTAL_PROJECTS"].split(","))],check=True,timeout=160)
    except (OSError, subprocess.SubprocessError):
        subprocess.run([*stack(previous),*up],check=True)
        raise
    link = directory / (".current-" + str(os.getpid()))
    link.symlink_to(overlay)
    link.replace(directory / "current.json")


if __name__ == "__main__":
    enroll(*sys.argv[1:6], Path(sys.argv[6]))
