"""Server-side separation between owner workspaces and acceptance runners."""
import hashlib
import hmac
import os
import re

from core import workspace_name
from uuid import UUID


def acceptance_projects():
    configured = os.getenv("SMARTDEV_ACCEPTANCE_PROJECT_IDS", "")
    return {UUID(value.strip()) for value in configured.split(",") if value.strip()}


def allowed_submission(project_id, credential, project_ids, digest):
    isolated = project_id in project_ids
    if credential is None:
        return not isolated
    if not isolated or not re.fullmatch(r"[a-f0-9]{64}", digest) or not re.fullmatch(r"[\x21-\x7e]{32,256}", credential):
        return False
    return hmac.compare_digest(hashlib.sha256(credential.encode()).hexdigest(), digest)


def fixture_binding(project_id, row, credential, conversation_id, workspace):
    expected_workspace = workspace_name(project_id)
    return (row and row["conversation_id"] and not row["closed_at"]
            and row["status"] == "created" and row["workspace_url"]
            and row["workspace_url"].rsplit("/", 1)[-1] == expected_workspace
            and workspace == expected_workspace and conversation_id == str(row["conversation_id"])
            and allowed_submission(project_id, credential, {project_id}, row["token_digest"]))
