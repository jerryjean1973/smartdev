"""Package SmartDev's standalone Compose release and immutable image references."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"api"))
from smd_export_policy import RELEASE_FILES, TEMPLATE, additions

SOURCE = Path(__file__).resolve().parents[1] / "release" / "smd"
IMAGES = {
    "API": "smartdev-api:tenant-smd",
    "PORTAL": "smartdev-portal:tenant-smd",
    "POSTGRES": "pgvector/pgvector:pg17",
    "REDIS": "redis:7-alpine",
    "OPENBAO": "quay.io/openbao/openbao:2.7.3",
    "CODER": "ghcr.io/coder/coder:v2.38.10",
    "CODER_DB": "postgres:17",
    "EDGE": "caddy:2.10.2-alpine",
    "WORKSPACE": "command-dev-workspace:architect-gate-b5e3b00",
}
MODEL_MANIFEST = Path("manifests/registry.ollama.ai/library/qwen2.5/1.5b")
CONTENT_ID = re.compile(r"sha256:[a-f0-9]{64}")
DIGEST_REFERENCE = re.compile(r"[^\s@=]+@sha256:[a-f0-9]{64}")
TENANT_PROFILE = "tenant-smd"
PROFILE_LABEL = "org.smartdev.image-profile"
API_EXCLUDES = frozenset(
    line.strip() for line in (SOURCE.parents[1] / "api" / "tenant-image-excludes.txt").read_text().splitlines()
    if line.strip() and not line.lstrip().startswith("#")
)
PORTAL_FORBIDDEN = (
    b"/api/admin/smd-deploy-tasks",
    b"/api/admin/compute/deployments",
    b"/api/admin/docker-export",
    b"/development/smd",
    b"/smd-export",
)


def add_bytes(archive, name, contents):
    member = tarfile.TarInfo(name)
    member.size = len(contents)
    member.mode = 0o644
    archive.addfile(member, io.BytesIO(contents))


def local_image_ids(references):
    result = subprocess.run(
        ["docker", "image", "inspect", *references.values()],
        check=True, capture_output=True,
    )
    images = json.loads(result.stdout)
    ids = {key: image["Id"] for key, image in zip(references, images, strict=True)}
    if not all(CONTENT_ID.fullmatch(identifier) for identifier in ids.values()):
        raise ValueError("Docker did not return pinned content IDs")
    return ids


def audit_flattened_image(archive, service):
    """Reject forbidden controller files or compiled deployment-console endpoints."""
    def contains_forbidden(source):
        overlap = max(map(len, PORTAL_FORBIDDEN)) - 1
        tail = b""
        while chunk := source.read(1024 * 1024):
            window = tail + chunk
            if any(marker in window for marker in PORTAL_FORBIDDEN):
                return True
            tail = window[-overlap:]
        return False

    forbidden_paths = []
    forbidden_content = []
    for member in archive:
        name = member.name.lstrip("./")
        if service == "API" and (name.startswith("app/") and Path(name).name in API_EXCLUDES):
            forbidden_paths.append(name)
        if service == "PORTAL" and member.isfile():
            source = archive.extractfile(member)
            if source and contains_forbidden(source):
                forbidden_content.append(name)
    if forbidden_paths:
        raise ValueError("Tenant API image contains Command Center deployment files: " + ", ".join(forbidden_paths))
    if forbidden_content:
        raise ValueError("Tenant portal image contains Command Center deployment endpoints: " + ", ".join(forbidden_content))


def verify_tenant_images(references):
    selected = {key: references[key] for key in ("API", "PORTAL")}
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", *selected.values()],
            check=True, capture_output=True,
        )
    except subprocess.CalledProcessError as failure:
        raise ValueError("Tenant API and portal images must be locally available for content inspection") from failure
    images = json.loads(result.stdout)
    for (service, reference), image in zip(selected.items(), images, strict=True):
        labels = (image.get("Config") or {}).get("Labels") or {}
        if labels.get(PROFILE_LABEL) != TENANT_PROFILE:
            raise ValueError(f"{service} image is not labeled as a tenant-smd image: {reference}")
        container = subprocess.run(
            ["docker", "create", reference], check=True, capture_output=True, text=True,
        ).stdout.strip()
        if not re.fullmatch(r"[a-f0-9]{12,64}", container):
            raise ValueError("Docker did not return a container ID for image inspection")
        try:
            with subprocess.Popen(["docker", "export", container], stdout=subprocess.PIPE) as exported:
                with tarfile.open(fileobj=exported.stdout, mode="r|") as flattened:
                    audit_flattened_image(flattened, service)
                if exported.wait():
                    raise RuntimeError(f"Could not inspect flattened {service} image")
        finally:
            subprocess.run(["docker", "rm", container], check=True, capture_output=True)


def add_docker_archive(archive, references):
    # Fill in the member length after compression so a second copy is unnecessary.
    target = archive.fileobj
    start = target.tell()
    member = tarfile.TarInfo("images.tar.gz")
    member.mode = 0o644
    target.write(member.tobuf(archive.format))
    target.flush()
    with subprocess.Popen(["docker", "save", *references.values()], stdout=subprocess.PIPE) as save:
        subprocess.run(["gzip", "-1"], stdin=save.stdout, stdout=target, check=True)
        if save.wait():
            raise RuntimeError("Docker image export failed")
    end = target.seek(0, io.SEEK_CUR)
    member.size = end - start - tarfile.BLOCKSIZE
    target.seek(start)
    target.write(member.tobuf(archive.format))
    target.seek(end)
    target.write(b"\0" * (-member.size % tarfile.BLOCKSIZE))
    archive.offset = target.tell()


def add_model(archive, model_store):
    manifest = model_store / MODEL_MANIFEST
    model = json.loads(manifest.read_bytes())
    archive.add(manifest, arcname=str(Path("models") / MODEL_MANIFEST), recursive=False)
    for descriptor in [model["config"], *model["layers"]]:
        digest = descriptor["digest"]
        if not CONTENT_ID.fullmatch(digest):
            raise ValueError("Expected a SHA-256 model blob")
        name = Path("blobs") / digest.replace(":", "-")
        with (model_store / name).open("rb") as blob:
            if hashlib.file_digest(blob, "sha256").hexdigest() != digest.removeprefix("sha256:"):
                raise ValueError(f"Model blob digest mismatch: {name}")
        archive.add(model_store / name, arcname=str(Path("models") / name), recursive=False)


def add_workspace_tools(archive, runtime, staging):
    root = SOURCE.parents[1]
    clients = ("workspace-browser-start","workspace-screenshot","workspace-dev-login",
               "command-dev-login.cjs","git-remote-smartdev","workspace-git-credential","codex-governance-hook","workspace-tools-check","workspace_git.py","git-mcp.toml")
    for client in clients:
        add_bytes(archive, "workspace-tools/clients/" + client, (root/"workspace"/client).read_bytes())
    for original, name in {
        "workspace_tool_service.py":"service.py", "workspace_health.py":"health.py",
        "smartdev-workspace-health.service":"smartdev-workspace-health.service",
        "smartdev-workspace-health.timer":"smartdev-workspace-health.timer", "workspace_dev_accounts.py":"accounts.py",
        "smartdev-workspace-tools.service":"smartdev-workspace-tools.service",
        "smartdev-workspace-accounts.service":"smartdev-workspace-accounts.service",
        "smartdev-workspace-accounts.timer":"smartdev-workspace-accounts.timer",
        "chromium-seccomp.json":"chromium-seccomp.json",
    }.items():
        add_bytes(archive, "workspace-tools/" + name, (root/"operator"/original).read_bytes())
    add_bytes(archive,"workspace-tools/github_known_hosts",(root/"api/github_known_hosts").read_bytes())
    for required in ("bin/chromium","bin/wget","fonts.conf"):
        if not (runtime/required).is_file():
            raise ValueError("Workspace browser runtime is missing " + required)
    bundle = staging / "workspace-chromium.tar.gz"
    with tarfile.open(bundle, "w:gz", compresslevel=2, dereference=True) as package:
        for file in sorted(runtime.rglob("*")):
            if file.is_file():
                package.add(file, arcname=str(file.relative_to(runtime)), recursive=False)
    archive.add(bundle, arcname="workspace-tools/chromium.tar.gz", recursive=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("revision")
    parser.add_argument("output", type=Path)
    parser.add_argument("--images", type=Path, help="JSON object mapping each service key to an image")
    parser.add_argument("--offline", action="store_true", help="Embed locally available images")
    parser.add_argument("--model-store", type=Path, required=True,
                        help="Required Ollama model directory with qwen2.5:1.5b for the Architect gate")
    parser.add_argument("--workspace-browser", type=Path, default=Path("/opt/smartdev-workspace-tools/chromium"),
                        help="Unpacked, architecture-matched Chromium and wget release runtime")
    parser.add_argument("--dns-zone",default="smd.youkandoit.com",help="Approved managed domain suffix")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", args.revision):
        parser.error("Expected a simple release identifier")
    expected_images = dict(IMAGES)
    expected_images["OLLAMA"] = "ollama/ollama:latest"
    references = json.loads(args.images.read_text()) if args.images else expected_images
    if set(references) != set(expected_images):
        parser.error(f"Image keys must match {', '.join(expected_images)}")
    if not args.offline and not all(DIGEST_REFERENCE.fullmatch(value) for value in references.values()):
        parser.error("A registry release requires digest-pinned image references")
    verify_tenant_images(references)
    with tempfile.TemporaryDirectory(dir=args.output.resolve().parent) as workspace:
        temporary = Path(workspace)
        output = temporary / "release.tar"
        images = local_image_ids(references) if args.offline else references
        with output.open("wb", buffering=0) as target, tarfile.open(
                fileobj=target, mode="w", format=tarfile.GNU_FORMAT) as archive:
            for name in sorted(RELEASE_FILES):
                source=SOURCE/name
                if source.is_symlink():
                    raise ValueError("Linked release source")
                if source.name == "architect-compose.yaml":
                    continue
                if source.name == "compose.yaml":
                    config = b"include: [architect-compose.yaml]\n" + source.read_bytes()
                    add_bytes(archive, source.name, config)
                else:
                    add_bytes(archive, source.name, source.read_bytes())
            for name,body in additions(args.dns_zone).items():
                add_bytes(archive,name,body)
            for template_name in sorted(TEMPLATE):
                template=SOURCE/"coder-template"/template_name
                if template.is_symlink():
                    raise ValueError("Linked template source")
                add_bytes(archive, f"coder-template/{template.name}", template.read_bytes())
            add_workspace_tools(archive,args.workspace_browser,temporary)
            profiles = SOURCE.parents[1] / "workspace" / "security"
            for name in ("seccomp.json", "LICENSE.moby-profiles", "command-codex-workspace"):
                add_bytes(archive, f"coder-template/{name}", (profiles / name).read_bytes())
            add_bytes(archive, "architect-compose.yaml", (SOURCE / "architect-compose.yaml").read_bytes())
            add_model(archive, args.model_store)
            add_bytes(archive, "images.env", "".join(
                f"SMD_{key}_IMAGE={value}\n" for key, value in sorted(images.items())
            ).encode())
            add_bytes(archive, "revision", (args.revision + "\n").encode())
            add_bytes(archive, "README.md", (SOURCE.parent / "README.md").read_bytes())
            if args.offline:
                add_bytes(archive, "INSTALL-OFFLINE.md", (SOURCE.parent / "INSTALL-OFFLINE.md").read_bytes())
            if args.offline:
                add_docker_archive(archive, references)
        with output.open("rb") as staged:
            digest = hashlib.file_digest(staged, "sha256").hexdigest()
        if args.offline:
            proof=args.output.resolve().parent/("clean-"+digest+".json")
            subprocess.run([sys.executable,str(Path(__file__).with_name("attest-smd-export.py")),str(output),digest,str(proof)],check=True)
        args.output.hardlink_to(output)
        print(digest)


if __name__ == "__main__":
    main()
