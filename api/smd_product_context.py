"""Look up operator-prepared, content-addressed product contexts for an SMD offer."""
import json
import os
from pathlib import Path
import re
from uuid import UUID

from fastapi import HTTPException
import vault


# Only application inputs from the customer's QA profile may be delivered.
QA_INPUTS = {"DATABASE_URL", "SESSION_SECRET", "LICENSE_KEY", "LICENSE_SERVER_URL", "LICENSE_SERVER_TOKEN",
             "CUSTOMER_OPENAI_BASE_URL", "CUSTOMER_OPENAI_API_KEY", "CUSTOMER_GEMINI_BASE_URL", "CUSTOMER_GEMINI_API_KEY"}
QA_ALIASES = {
    "CUSTOMER_OPENAI_BASE_URL": ("AI_INTEGRATIONS_OPENAI_BASE_URL", "OPENAI_BASE_URL"),
    "CUSTOMER_OPENAI_API_KEY": ("AI_INTEGRATIONS_OPENAI_API_KEY", "OPENAI_API_KEY"),
    "CUSTOMER_GEMINI_BASE_URL": ("AI_INTEGRATIONS_GEMINI_BASE_URL",),
    "CUSTOMER_GEMINI_API_KEY": ("AI_INTEGRATIONS_GEMINI_API_KEY",),
}


def directory(project):
    root = os.getenv("SMARTDEV_SMD_PRODUCT_CONTEXT_DIRECTORY")
    return Path(root) / str(UUID(str(project["id"]))) if root else None


def qa_inputs(store):
    requirements = store / "qa-required.json"
    if not requirements.exists():
        return set()
    try:
        keys = json.loads(requirements.read_text())
        if not isinstance(keys, list) or not keys or not all(isinstance(k, str) and k in QA_INPUTS for k in keys):
            raise ValueError()
        return set(keys)
    except (OSError, ValueError):
        raise HTTPException(503, "Cannot verify this project's QA runtime requirements.") from None


def bind_contexts(manifest):
    for project in manifest:
        store = directory(project)
        if store is None or not store.is_dir():
            project["verify_installation"] = True
            continue
        project.pop("verify_installation", None)
        revision = project["revision"]
        if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise HTTPException(409, "Pin the project revision before selecting a prepared context.")
        try:
            digest = (store / (revision + ".sha256")).read_text().strip()
            if not re.fullmatch(r"[0-9a-f]{64}", digest) or not (store / (digest + ".tar.gz")).is_file():
                raise ValueError()
        except (OSError, ValueError):
            raise HTTPException(409, "Prepare a verified product context for this project revision.") from None
        project["build_context_sha256"] = digest
        project["tenant_setup"] = True


def approved_context(project):
    digest = project.get("build_context_sha256")
    store = directory(project)
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or store is None:
        raise HTTPException(403, "This deployment did not approve a product context.")
    path = store / (digest + ".tar.gz")
    if not path.is_file() or path.is_symlink():
        raise HTTPException(409, "The approved product context is unavailable.")
    return path


def approved_qa_environment(project):
    store = directory(project)
    versions = project.get("qa_runtime_versions")
    if (not isinstance(versions, dict) or not versions or not store or not project.get("build_context_sha256")
            or set(versions) != qa_inputs(store) or any(type(v) is not int or v < 1 for v in versions.values())):
        raise HTTPException(403, "This deployment did not approve the requested QA runtime profile.")
    project_id = UUID(str(project["id"]))
    env = {}
    for key, version in versions.items():
        result = vault.call("GET", f"data/projects/{project_id}/QA/{key}?version={version}", missing=True)
        record = (result or {}).get("data") or {}
        value = (record.get("data") or {}).get("value")
        if (record.get("metadata", {}).get("version") != version or not isinstance(value, str) or not value
                or any(ch in value for ch in ("\x00", "\n", "\r"))):
            raise HTTPException(409, "An approved QA setting is unavailable or incompatible with the runtime file.")
        for name in (key, *QA_ALIASES.get(key, ())):
            env[name] = value
    content = "".join(f"{key}={value}\n" for key, value in env.items()).encode()
    if len(content) > 65536:
        raise HTTPException(409, "The QA settings exceed the supported runtime file size.")
    vault.audit(project_id, "deploy", "QA", ",".join(sorted(versions)))
    return content
