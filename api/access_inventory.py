"""Non-secret, fail-closed access inventory for every managed coding session."""
import hashlib
import json
import os
from datetime import datetime, timezone
from urllib.parse import urlsplit
from uuid import UUID, uuid5

from fastapi import HTTPException

from store import database

RECEIPT_NAMESPACE = UUID("21495f31-957e-4bde-a864-c288cb1809f0")
ENVIRONMENTS = {"DEV", "QA", "PREPROD", "PROD"}


def environment_from_host(hostname):
    hostname = (hostname or "").lower().strip(".")
    labels = hostname.split(".")
    for value in ("qa", "preprod", "prod", "dev"):
        if value in labels:
            return value.upper()
    return os.getenv("SMARTDEV_ENVIRONMENT", "DEV").upper()


def unavailable(entry_point, identity, scope, renewal, receipt_path, missing):
    return {
        "status": "unavailable", "entry_point": entry_point, "identity": identity,
        "scope": scope, "permitted_actions": [], "credential_renewal": renewal,
        "receipt_path": receipt_path, "missing_binding_or_permission": missing,
    }


def available(entry_point, identity, scope, actions, renewal, receipt_path):
    return {
        "status": "available", "entry_point": entry_point, "identity": identity,
        "scope": scope, "permitted_actions": actions, "credential_renewal": renewal,
        "receipt_path": receipt_path,
    }


def project_record(project_id):
    with database() as db:
        project = db.execute("""SELECT p.id,p.name,p.repository,p.workspace_url,
            COALESCE(binding.tenant_name,(SELECT name FROM command_center_tenant
                WHERE id=smd_admin_tenant_id())) AS tenant
            FROM projects p LEFT JOIN command_center_project_tenant binding ON binding.project_id=p.id
            WHERE p.id=%s AND NOT p.archived""", (project_id,)).fetchone()
    if not project:
        raise HTTPException(404, "Missing project binding: the selected project is unavailable.")
    return project


def deployment_records(project_id, tenant):
    with database() as db:
        return db.execute("""SELECT t.id,t.deployment_hostname,t.dns_zone,t.instance_id,
            r.verified_at,r.connections
            FROM smd_deploy_task t
            JOIN smd_deployment_assignment a ON a.task_id=t.id
            JOIN command_center_tenant c ON c.id=a.tenant_id
            LEFT JOIN smd_deploy_receipt r ON r.task_id=t.id AND r.instance_id=t.instance_id
            WHERE t.status='deployed' AND t.reconciled_at IS NULL AND c.name=%s
              AND (t.project_id=%s OR EXISTS (
                SELECT 1 FROM jsonb_array_elements(COALESCE(t.project_manifest,'[]'::jsonb)) item
                WHERE item->>'id'=%s))
            ORDER BY t.queued_at,t.id""", (tenant, project_id, str(project_id))).fetchall()


def host_operator(project_id, scope):
    task = os.getenv("SMD_PORTAL_TASK")
    instance = os.getenv("SMD_PORTAL_INSTANCE")
    issuer = os.getenv("SMD_PORTAL_ISSUER", "").rstrip("/")
    if task and instance and issuer:
        entry = issuer + "/api/smd-deploy-tasks/" + task + "/configuration"
        return available(entry, f"smd-install:{task}:{instance}", scope,
            ["read_only_check", "owner_credential_handoff", "signed_receipt"],
            "Rotate through the enrolled owner identity generation; private material stays with the operator.", entry)
    required = {
        "SMARTDEV_OPERATOR_PROJECT_ID": os.getenv("SMARTDEV_OPERATOR_PROJECT_ID"),
        "SMARTDEV_OPERATOR_TOKEN_SHA256": os.getenv("SMARTDEV_OPERATOR_TOKEN_SHA256"),
        "SMARTDEV_OPERATOR_TASK_MIG_PUBLIC_KEY": os.getenv("SMARTDEV_OPERATOR_TASK_MIG_PUBLIC_KEY"),
    }
    missing = [name for name, value in required.items() if not value]
    if not missing and required["SMARTDEV_OPERATOR_PROJECT_ID"] == str(project_id):
        entry = os.environ["PORTAL_ORIGIN"].rstrip("/") + "/api/operator-machine"
        return available(entry, "enrolled-host-operator", scope,
            ["read_only_check", "mig_authorized_task", "signed_receipt"],
            "Re-enroll the operator token and MIG public authority; private keys remain operator-held.", entry + "/task-result")
    if not missing:
        missing = ["SMARTDEV_OPERATOR_PROJECT_ID does not match the selected project"]
    return unavailable(None, "host-operator", scope,
        "Enroll the project-scoped operator and MIG public authority.", None,
        "Missing host/operator binding: " + ", ".join(missing) + ".")


def deployment(project_id, environment, tenant, scope):
    task = os.getenv("SMD_PORTAL_TASK")
    instance = os.getenv("SMD_PORTAL_INSTANCE")
    portal = os.getenv("PORTAL_ORIGIN", "").rstrip("/")
    if task and instance:
        actual = environment_from_host(urlsplit(portal).hostname)
        if actual != environment:
            return unavailable(portal, f"smd-deployment:{task}:{instance}", scope,
                "Re-enroll after an explicit environment reassignment.", None,
                f"Environment selection mismatch: this deployment is {actual}, not {environment}.")
        return available(portal, f"smd-deployment:{task}:{instance}", scope,
            ["read_only_check", "workspace_task", "receipt_return"],
            "Re-enroll the signed installation identity after credential renewal.",
            os.getenv("SMD_PORTAL_ISSUER", "").rstrip("/") + "/api/admin/compute/deployments/" + task + "/history")
    rows = [row for row in deployment_records(project_id, tenant)
            if environment_from_host(row["deployment_hostname"] or row["dns_zone"]) == environment]
    if not rows:
        return unavailable(None, "smd-deployment", scope,
            "Renew through the deployment assignment and verified enrollment receipt.", None,
            f"Missing deployment binding: no active verified {environment} deployment contains this project in tenant {tenant}.")
    if len(rows) != 1:
        return unavailable(None, "smd-deployment", scope,
            "Resolve the duplicate assignment before renewal.", None,
            f"Ambiguous deployment binding: {len(rows)} active {environment} deployments contain this project; select deployment_id explicitly.")
    row = rows[0]
    if not row["verified_at"] or not row["connections"]:
        return unavailable(None, f'smd-deployment:{row["id"]}', scope,
            "Repeat signed enrollment and connection verification.", None,
            "Missing receipt binding: the selected deployment has no verified connection receipt.")
    links = row["connections"]
    return available(links.get("portal_url"), f'smd-deployment:{row["id"]}:{row["instance_id"]}', scope,
        ["read_only_check", "workspace_task", "receipt_return"],
        "Repeat signed enrollment after assignment or credential renewal.",
        f'/api/admin/compute/deployments/{row["id"]}/history')


def resolve(project_id, environment, executor, credential_marker):
    environment = str(environment or "").upper()
    if environment not in ENVIRONMENTS:
        raise HTTPException(422, "Explicit environment required: choose DEV, QA, PREPROD, or PROD.")
    project = project_record(project_id)
    portal = os.getenv("PORTAL_ORIGIN", "").rstrip("/")
    coder = os.getenv("CODER_PUBLIC_URL", "").rstrip("/")
    scope = {"tenant": project["tenant"], "project_id": str(project_id), "environment": environment}
    app_entry = portal + "/api/workspace-memory/" + str(project_id) if portal else None
    if urlsplit(portal).scheme != "https":
        app = unavailable(app_entry, "project-workspace-service", scope,
            "Rotate the workspace HMAC through protected service configuration.", app_entry,
            "Missing CC app binding: PORTAL_ORIGIN is not an HTTPS origin.")
    else:
        app = available(app_entry, "project-workspace-service", scope,
            ["project_read", "managed_coding_preflight", "receipt_return"],
            "Rotate the workspace HMAC through protected service configuration.", app_entry + "/codex-access")
    workspace = project["workspace_url"]
    if (urlsplit(coder).scheme != "https" or not workspace
            or urlsplit(workspace).netloc != urlsplit(coder).netloc):
        coder_path = unavailable(workspace or coder or None, "coder-workspace", scope,
            "Renew the Coder owner/session service identity and reprovision the same project workspace.", None,
            "Missing Coder binding: the project has no workspace on the configured CODER_PUBLIC_URL.")
    else:
        coder_path = available(workspace, "coder-workspace:" + workspace.rsplit("/", 1)[-1], scope,
            ["read_only_check", "workspace_read", "workspace_write_when_task_authorized"],
            "Renew the Coder service identity and reprovision the same workspace; never select another workspace.",
            app_entry + "/codex-access")
    paths = {
        "cc_app": app,
        "cc_host_operator": host_operator(project_id, scope),
        "cc_coder_workspaces": coder_path,
        "cc_deployments": deployment(project_id, environment, project["tenant"], scope),
    }
    required = ("cc_app", "cc_coder_workspaces")
    missing = [name for name in required if paths[name]["status"] != "available"]
    if missing:
        raise HTTPException(409, paths[missing[0]]["missing_binding_or_permission"])
    binding = {"schema": "smartdev-access-inventory-v1", "selection": scope,
               "actual_executor": executor, "paths": paths}
    private_binding = {**binding, "credential_marker": credential_marker}
    digest = hashlib.sha256(json.dumps(private_binding, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {**binding, "verified_at": datetime.now(timezone.utc).isoformat(),
            "receipt": {"id": str(uuid5(RECEIPT_NAMESPACE, digest)), "sha256": digest,
                        "path": app_entry + "/codex-access"}}
