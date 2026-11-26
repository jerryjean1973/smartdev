"""Signed lifecycle documents consumed by Command Center and the host operator."""
import json
import re
from datetime import datetime, timezone
from hashlib import sha256
from uuid import UUID

IMAGE = re.compile(r"[a-z0-9][a-z0-9./:_-]*@sha256:[a-f0-9]{64}")
SERVICES = {"portal", "api", "coder", "postgres", "redis", "openbao", "ingress"}


def payload(kind, document):
    return json.dumps(["smartdev-installation-v1", kind, document],
                      sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def verify(public_key, signature, kind, document):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key)).verify(
        bytes.fromhex(signature), payload(kind, document))


def installation_plan(project_id, zone, release):
    root = str(UUID(project_id))
    labels = zone.split(".")
    if (len(zone) > 213 or len(labels) < 2
            or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                   for label in labels)):
        raise ValueError("Invalid installation DNS zone")
    if release.get("release_profile") != "smd-host":
        raise ValueError("Signed release must identify a standalone SMD host artifact")
    images = release["images"]
    if set(images) != SERVICES or any(not IMAGE.fullmatch(image) for image in images.values()):
        raise ValueError("Every installation service needs a digest-pinned image")
    for artifact in ("coder_template_sha256", "installer_sha256"):
        if not re.fullmatch(r"[a-f0-9]{64}", release[artifact]):
            raise ValueError("Invalid release artifact digest")
    host = f"sd-{root}"
    return {
        "project_id": root,
        "release_profile": release["release_profile"],
        "host_id": host,
        "portal_origin": f"https://{host}.{zone}",
        "coder_origin": f"https://coder-{root}.{zone}",
        "https_port": 443,
        "private_ports": {"portal": 3000, "api": 8000},
        "api_environment": {
            "SMARTDEV_ROOT_PROJECT_ID": root,
            "PORTAL_ORIGIN": f"https://{host}.{zone}",
            "CODER_PUBLIC_URL": f"https://coder-{root}.{zone}",
        },
        "volumes": {role: f"{host}-{role}" for role in
                    ("postgres", "redis", "openbao", "workspaces", "workspace-config", "ingress")},
        "images": dict(images),
        "release_sha256": sha256(payload("release", release)).hexdigest(),
    }


def verified_installation_plan(project_id, zone, release, signature, release_public_key):
    verify(release_public_key, signature, "release", release)
    return installation_plan(project_id, zone, release)


def verify_operation(grant, signature, mig_public_key, plan, enrolled_host, active_release,
                     now=None):
    verify(mig_public_key, signature, "operation", grant)
    if (grant["project_id"] != plan["project_id"] or grant["host_id"] != plan["host_id"]
            or grant["enrollment_id"] != enrolled_host
            or grant["plan_sha256"] != sha256(payload("plan", plan)).hexdigest()
            or grant["expected_active_release"] != active_release
            or grant["operation"] not in {"install", "update", "monitor", "rollback", "retire"}):
        raise ValueError("Operation does not match this installation or its current release")
    UUID(grant["operation_id"])
    expires = datetime.fromisoformat(grant["expires_at"])
    if expires.tzinfo is None or expires <= (now or datetime.now(timezone.utc)):
        raise ValueError("Operation grant has expired")
    if (grant["operation"] == "install") != (active_release is None):
        raise ValueError("Operation requires a different installation state")


def verify_receipt(receipt, signature, enrolled_host_public_key, grant):
    verify(enrolled_host_public_key, signature, "receipt", receipt)
    if receipt["operation_sha256"] != sha256(payload("operation", grant)).hexdigest():
        raise ValueError("Receipt does not match the granted operation")
