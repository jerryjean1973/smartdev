"""Build and run each approved project in a separate Docker network."""

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import stat
import tarfile
from tempfile import TemporaryDirectory
from uuid import UUID

IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}")
LABEL = "io.smartdev.project"
REVISION = "io.smartdev.revision"


def docker(*args, missing=False, timeout=60):
    result = subprocess.run(["docker", *map(str, args)], capture_output=True, text=True,
                            check=False, timeout=timeout)
    if result.returncode:
        detail = result.stderr.lower()
        if missing and ("no such" in detail or "not found" in detail):
            return None
        raise RuntimeError(f"Docker {args[0]} failed while preparing an approved application.")
    return result.stdout.strip()


def project_port(checkout, image):
    config = checkout / ".smartdev" / "deploy.json"
    if config.exists():
        if not config.resolve().is_relative_to(checkout.resolve()) or config.stat().st_size > 4096:
            raise ValueError("Invalid .smartdev/deploy.json export.")
        data = json.loads(config.read_text())
        if not isinstance(data, dict) or set(data) != {"port"} or type(data["port"]) is not int:
            raise ValueError("Set the application TCP port in .smartdev/deploy.json.")
        port = data["port"]
    else:
        exposed = json.loads(docker("image", "inspect", "--format", "{{json .Config.ExposedPorts}}", image))
        ports = [item.split("/", 1)[0] for item in (exposed or {}) if item.endswith("/tcp")]
        if len(ports) != 1:
            raise ValueError("Declare one Dockerfile EXPOSE TCP port or set .smartdev/deploy.json.")
        port = int(ports[0])
    if not 1 <= port <= 65535:
        raise ValueError("Application TCP port is outside the valid range.")
    return port


def project_environment(data, project_id):
    path = data / "project-runtime" / f"{project_id}.env"
    if not path.exists() and not path.is_symlink():
        return path, {}
    metadata = path.lstat()
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid()
            or metadata.st_mode & 0o077 or metadata.st_size > 65536
            or path.resolve() != data.resolve() / "project-runtime" / path.name):
        raise ValueError("Protect the project runtime file with owner-only permissions.")
    environment = {}
    for line in path.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if (not separator or not re.fullmatch(r"[A-Z_][A-Z0-9_]{0,95}", key)
                or not value or key in environment):
            raise ValueError("Invalid project runtime environment entry.")
        environment[key] = value
    return path, environment


def build_image(checkout, revision, tag, project_id):
    with TemporaryDirectory() as temporary:
        work = Path(temporary)
        archive = work / "source.tar"
        source = work / "source"
        source.mkdir()
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        with archive.open("wb") as output:
            subprocess.run(["git", "-C", checkout, "archive", "--format=tar", revision],
                           env=env, stdout=output, stderr=subprocess.DEVNULL, check=True, timeout=120)
        with tarfile.open(archive) as saved:
            saved.extractall(source, filter="data")
        docker("build", "--tag", tag, "--label", f"{LABEL}={project_id}",
               "--label", f"{REVISION}={revision}", source, timeout=900)


def build_product_image(checkout, project, archive, tag):
    digest = project["build_context_sha256"]
    revision = project["revision"]
    if not archive.is_file() or archive.is_symlink():
        raise ValueError("The approved product context was not delivered.")
    with archive.open("rb") as source, TemporaryDirectory() as temporary:
        checksum = hashlib.sha256()
        for block in iter(lambda: source.read(1024 * 1024), b""):
            checksum.update(block)
        if checksum.hexdigest() != digest:
            raise ValueError("Product context digest does not match the approved offer.")
        source.seek(0)
        work = Path(temporary)
        with tarfile.open(fileobj=source, mode="r:gz") as saved:
            members = saved.getmembers()
            names = set()
            for member in members:
                path = PurePosixPath(member.name)
                if (not member.isfile() or path.is_absolute() or ".." in path.parts
                        or path.as_posix() != member.name or member.name in names):
                    raise ValueError("Unexpected file in product context.")
                names.add(member.name)
            saved.extractall(work, members=members, filter="data")
        manifest = json.loads((work / "product-package.json").read_text())
        distribution = json.loads(subprocess.check_output(
            ["git", "-C", checkout, "show", revision + ":product-distribution/distribution.json"], text=True))
        gitlink = subprocess.check_output(["git", "-C", checkout, "ls-tree", revision,
                                           "product-distribution/canonical-source"], text=True).split()
        product, engine = manifest.get("product"), manifest.get("engine")
        if (manifest.get("schema_version") != "product-package.v1"
                or product != distribution.get("product") or product not in ("bcentrisk", "vendorgate", "bripe")
                or engine not in ("embedded", "remote")
                or manifest.get("source_revision") != distribution.get("source_revision")
                or gitlink[:3] != ["160000", "commit", manifest.get("source_revision")]
                or set(manifest["files"]) != names - {"product-package.json"}
                or "Dockerfile.products" not in names):
            raise ValueError("Product context is not bound to the approved distribution.")
        for name, expected in manifest["files"].items():
            with (work / name).open("rb") as member:
                if hashlib.file_digest(member, "sha256").hexdigest() != expected:
                    raise ValueError("Product context file differs from its seal.")
        docker("build", "--file", work / "Dockerfile.products", "--tag", tag,
               "--label", f"{LABEL}={project['id']}", "--label", f"{REVISION}={revision}",
               "--label", f"io.smartdev.build-context={digest}", "--build-arg", f"PRODUCT={product}",
               "--build-arg", f"ENGINE_LOCATION={engine}", "--build-arg", f"BUILD_ID={revision}", work,
               timeout=1800)


# The product's published browser bundle is usable before tenant runtime credentials exist.
# Express serves only the compiled public directory. API calls keep their actual setup status.
PREVIEW_SERVER = """
const express = require('express'), fs = require('fs'), path = require('path');
const app = express(), root = path.resolve('dist/public'), index = path.join(root,'index.html');
if (!fs.statSync(index).isFile()) throw Error('Missing packaged browser application');
app.disable('x-powered-by');
app.use('/api', (req,res)=>res.status(503).json({status:'tenant_configuration_required', detail:'Tenant application services require configuration.'}));
app.use(express.static(root,{dotfiles:'deny',index:false,redirect:false}));
app.get(/.*/, (req,res)=>req.accepts('html') ? res.sendFile(index) : res.sendStatus(404));
app.listen(Number(process.env.PORT),'0.0.0.0');
"""


def browser_preview(name, image, project_id, revision, port):
    preview = name + "-preview"
    existing = docker("container","inspect","--format","{{json .}}",preview,missing=True)
    if existing:
        container = json.loads(existing)
        if (container["Image"] != image or container["Config"]["Labels"].get(LABEL) != project_id
                or container["Config"]["Labels"].get(REVISION) != revision
                or set(container["NetworkSettings"]["Networks"]) != {name}):
            raise ValueError("Existing browser preview differs from the approved application.")
        if not container["State"]["Running"]:
            docker("start",preview)
    else:
        docker("run","-d","--name",preview,"--network",name,"--restart","unless-stopped","--read-only",
               "--cap-drop","ALL","--security-opt","no-new-privileges","--pids-limit","96","--memory","256m",
               "--label",f"{LABEL}={project_id}","--label",f"{REVISION}={revision}","-e",f"PORT={port}",
               "--entrypoint","node",image,"-e",PREVIEW_SERVER)
    return preview


def prepare(data, selected):
    networks = {}
    routes = []
    for project in selected:
        project_id = str(UUID(project["id"]))
        name = "smd-app-" + UUID(project_id).hex
        hostname = project.get("hostname")
        if not hostname:
            raise ValueError(f"Missing application hostname for {project['name']}.")
        revision = project["revision"]
        checkout = data / "checkouts" / project_id / revision
        dockerfile = checkout / "Dockerfile"
        context_digest = project.get("build_context_sha256")
        if not context_digest and (not dockerfile.is_file() or dockerfile.is_symlink()):
            raise ValueError(f"{project['name']} needs a committed Dockerfile.")
        tag = f"{name}:{revision}" + (f"-{context_digest}" if context_digest else "")
        observed = docker("image", "inspect", "--format", "{{.Id}}", tag, missing=True)
        if observed is None:
            if context_digest:
                build_product_image(checkout, project, data / "git-bundles" / (project_id + ".tar.gz"), tag)
            else:
                build_image(checkout, revision, tag, project_id)
            observed = docker("image", "inspect", "--format", "{{.Id}}", tag)
        if not IMAGE_ID.fullmatch(observed):
            raise ValueError("Docker did not return a content-addressed application image.")
        image_labels = json.loads(docker("image", "inspect", "--format", "{{json .Config.Labels}}", observed))
        if not image_labels or image_labels.get(LABEL) != project_id or image_labels.get(REVISION) != revision:
            raise ValueError("Application image does not match this project and Git revision.")
        if context_digest and image_labels.get("io.smartdev.build-context") != context_digest:
            raise ValueError("Application image does not match the approved context.")
        port = project_port(checkout, observed)
        network = docker("network", "inspect", "--format", "{{json .}}", name, missing=True)
        if network is None:
            docker("network", "create", "--internal", "--label", f"{LABEL}={project_id}", name)
        else:
            network = json.loads(network)
            if not network["Internal"] or (network.get("Labels") or {}).get(LABEL) != project_id:
                raise ValueError("An existing network does not belong to this project.")
        environment_file, environment = project_environment(data, project_id)
        environment_args = ["--env-file", environment_file] if environment else []
        existing = docker("container", "inspect", "--format", "{{json .}}", name, missing=True)
        if project.get("tenant_setup") and not environment and existing is None:
            preview = browser_preview(name, observed, project_id, revision, port)
            networks[name] = {"external":True, "name":name}
            routes.append(f'{hostname}:443 {{\n'
                          f'    respond /.well-known/smartdev-installation "{context_digest}" 200\n'
                          f'    reverse_proxy {preview}:{port}\n}}\n')
            continue
        if existing is None:
            docker("run", "-d", "--name", name, "--network", name, "--restart", "unless-stopped",
                   "--security-opt", "no-new-privileges", "--cap-drop", "ALL",
                   "--cap-add", "NET_BIND_SERVICE", "--pids-limit", "256",
                   "--memory", "1g", *environment_args, "--label", f"{LABEL}={project_id}", "--label",
                   f"{REVISION}={revision}", observed)
        else:
            container = json.loads(existing)
            config = container["Config"]
            configured_environment = dict(value.split("=", 1) for value in config.get("Env", []) or [])
            runtime = container["HostConfig"]
            ports = container["NetworkSettings"]
            if (container["Image"] != observed or config["Labels"].get(LABEL) != project_id
                    or config["Labels"].get(REVISION) != revision
                    or any(configured_environment.get(key) != value for key, value in environment.items())
                    or set(ports["Networks"]) != {name}
                    or runtime["Privileged"] or runtime.get("PortBindings") or container["Mounts"]
                    or runtime.get("Memory") != 1073741824 or runtime.get("PidsLimit") != 256
                    or "ALL" not in (runtime.get("CapDrop") or [])
                    or (runtime.get("CapAdd") or []) != ["CAP_NET_BIND_SERVICE"]
                    or not any(option.startswith("no-new-privileges") for option in runtime.get("SecurityOpt") or [])):
                raise ValueError("An existing application container conflicts with the approved project.")
            if not container["State"]["Running"]:
                docker("start", name)
        networks[name] = {"external": True, "name": name}
        marker = (f'    respond /.well-known/smartdev-installation "{revision}" 200\n'
                  if project.get("verify_installation") else "")
        routes.append(f"{hostname}:443 {{\n{marker}    reverse_proxy {name}:{port}\n}}\n")
    Path("app-networks.json").write_text(json.dumps({
        "services": {"edge": {"networks": ["default", *networks]}},
        "networks": networks,
    }))
    return "\n".join(routes)
