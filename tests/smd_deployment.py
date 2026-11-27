"""Exercise the owner deployment queue against temporary PostgreSQL tables."""
import os
from base64 import b64encode
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import HTTPException
from fastapi.testclient import TestClient

import smd_deployment as smd
import smd_receipts as receipts
from core import issue_session
from store import database


def rejects(status, operation, *args):
    try:
        operation(*args)
    except HTTPException as error:
        assert error.status_code == status
    else:
        raise AssertionError(f"Expected HTTP {status}")


def run():
    assert_owner_authorization()
    project_id, fixture_id = uuid4(), uuid4()
    with database() as conn:
        conn.execute("CREATE TEMP TABLE projects (id uuid PRIMARY KEY, name text, repository text, runtime text DEFAULT 'other', archived bool DEFAULT false)")
        conn.execute("CREATE TEMP TABLE acceptance_fixture (project_id uuid)")
        conn.execute(Path("api/038_smd_deploy_task.sql").read_text().replace(
            "CREATE TABLE IF NOT EXISTS", "CREATE TEMP TABLE"))
        other_id, legacy_project_id, legacy_job_id = uuid4(), uuid4(), uuid4()
        for identity in (project_id, fixture_id, other_id, legacy_project_id):
            conn.execute("INSERT INTO projects (id,name) VALUES (%s,'Test SMD')", (identity,))
        conn.execute("INSERT INTO acceptance_fixture VALUES (%s)", (fixture_id,))
        conn.execute("INSERT INTO smd_deploy_task (id,project_id,status) VALUES (%s,%s,'queued')",
                     (legacy_job_id, legacy_project_id))
        migration = Path("api/043_smd_deploy_offer.sql").read_text()
        conn.execute(migration)
        conn.execute(migration)
        conn.execute(Path("api/044_smd_receipts.sql").read_text().replace(
            "CREATE TABLE IF NOT EXISTS", "CREATE TEMP TABLE"))
        conn.execute(Path("api/047_smd_connection_receipts.sql").read_text().replace(
            "CREATE TABLE IF NOT EXISTS", "CREATE TEMP TABLE"))
        reconciliation_migration = Path("api/045_smd_legacy_reconciliation.sql").read_text()
        conn.execute(reconciliation_migration)
        conn.execute(reconciliation_migration)
        conn.execute(Path("api/046_smd_project_manifest.sql").read_text())
        conn.execute(Path("api/036_command_center_tenant.sql").read_text().replace(
            "CREATE TABLE IF NOT EXISTS", "CREATE TEMP TABLE"))
        conn.execute("UPDATE smd_deploy_task SET claim_digest=%s WHERE id=%s",
                     (sha256(b"old-claim").hexdigest(), legacy_job_id))

        @contextmanager
        def transaction():
            with conn.transaction():
                yield conn

        config = {
            "SMARTDEV_SMD_INSTALLER_URL": "https://example.invalid/install.sh",
            "SMARTDEV_SMD_INSTALLER_SHA256": "a" * 64,
            "SMARTDEV_SMD_RELEASE_REVISION": "fixture-rev",
        }
        with patch.object(smd, "database", transaction), patch.object(receipts, "database", transaction), \
                patch.dict(os.environ, config):
            assert smd.deployment(project_id)["deployment"] is None
            assert smd.tasks()[0]["status"] == "needs_attention"
            rejects(409, smd.claim, legacy_job_id)
            request = smd.DeploymentRequest(offer_token=smd.deployment(project_id)["offer_token"])
            rejects(404, smd.deploy, fixture_id, request)
            rejects(409, smd.deploy, other_id, request)
            with patch.dict(os.environ, {"SMARTDEV_SMD_RELEASE_REVISION": "replacement"}):
                rejects(409, smd.deploy, project_id, request)
            with patch.dict(os.environ, {"SMARTDEV_SMD_INSTALLER_SHA256": ""}):
                assert not smd.deployment(project_id)["configured"]
                rejects(503, smd.deploy, project_id, request)
            job = smd.deploy(project_id, request)
            with patch.dict(os.environ, {"SMARTDEV_SMD_RELEASE_REVISION": "replacement"}):
                assert smd.deploy(project_id, request)["id"] == job["id"]
            assert smd.tasks()[0]["status"] == "queued"
            claim_request = smd.ClaimRequest(claim_token="A" * 43)
            picked = smd.claim(job["id"], claim_request)
            assert picked["plan"] == "vc2-4c-8gb"
            assert picked["release_revision"] == "fixture-rev"
            assert picked["installer_sha256"] == "a" * 64
            assert "installer_url" not in job
            rejects(409, smd.claim, job["id"])
            rejects(409, smd.claim, job["id"], smd.ClaimRequest(claim_token="B" * 43))
            assert smd.claim(job["id"], claim_request) == picked
            token = picked["claim_token"]
            rejects(403, smd.progress, job["id"], smd.Progress(
                claim_token="incorrect", status="processing", note="Unauthorized"))
            rejects(422, smd.progress, job["id"], smd.Progress(
                claim_token=token, status="deployed", note="Missing evidence"))
            instance = uuid4()
            smd.progress(job["id"], smd.Progress(
                claim_token=token, status="processing", instance_id=instance, note="Installing"))
            reported = smd.Progress(
                claim_token=token, status="deployed", observed_revision="fixture-rev", note="Healthy")
            rejects(409, smd.progress, job["id"], reported)
            key = Ed25519PrivateKey.generate()
            receipt = receipts.Receipt(
                project_id=project_id, instance_id=instance, revision="fixture-rev",
                signature=b64encode(key.sign(
                    f"{job['id']}\n{project_id}\n{instance}\nfixture-rev\n".encode("ascii")
                )).decode("ascii"),
            )
            receipts.enroll(job["id"], receipts.Enrollment(
                claim_token=token, instance_id=instance,
                public_key=b64encode(key.public_key().public_bytes_raw()).decode("ascii")))
            rejects(403, receipts.receive, job["id"], receipt.model_copy(
                update={"signature": b64encode(b"0" * 64).decode("ascii")}))
            rejects(409, smd.progress, job["id"], reported)
            receipts.receive(job["id"], receipt)
            rejects(409, smd.progress, job["id"], reported.model_copy(
                update={"observed_revision": "unapproved"}))
            smd.progress(job["id"], reported)
            rejects(409, smd.claim, job["id"], claim_request)
            result = smd.deployment(project_id)["deployment"]
            assert (result["status"], result["instance_id"], result["observed_revision"]) == (
                "deployed", instance, "fixture-rev")
            reconcile_legacy(conn, legacy_project_id, legacy_job_id)
            grouped_deployment(conn, fixture_id)


def grouped_deployment(conn, fixture_id):
    first, second = sorted((uuid4(), uuid4()))
    third = uuid4()
    for project_id, name in ((first, "First"), (second, "Second"), (third, "Third")):
        conn.execute("INSERT INTO projects (id,name,repository) VALUES (%s,%s,%s)",
                     (project_id, name, "example/" + name.lower()))
    pinned = "a" * 40
    replacement = "b" * 40
    candidate = smd.Selection(projects=[smd.ProjectSelection(id=second, revision=pinned),
                                        smd.ProjectSelection(id=first)])

    outbound = []

    def github(request):
        outbound.append((request.method, request.url.path))
        if request.method == "POST":
            assert request.url.path == "/app/installations/42/access_tokens"
            return httpx.Response(201, json={"token": "fixture-installation-token"})
        assert request.headers.get("authorization") == "Bearer fixture-installation-token"
        return httpx.Response(200, json={"sha": pinned})

    original_client = httpx.Client

    def github_client(**kwargs):
        return original_client(transport=httpx.MockTransport(github), **kwargs)

    with patch.dict(os.environ, {
        "SMARTDEV_SMD_DNS_ZONE": "isolated.example.invalid",
        "SMARTDEV_SMD_VULTR_SSH_KEY_ID": str(uuid4()),
        "SESSION_KEY": "ab" * 32,
        "GITHUB_CLIENT_ID": "fixture-client",
        "GITHUB_INSTALLATION_ID": "42",
    }), patch.object(smd.httpx, "Client", side_effect=github_client), \
            patch.object(smd, "app_token", return_value="fixture-jwt"):
        rejects(404, smd.quote_selection, smd.Selection(projects=[smd.ProjectSelection(id=fixture_id)]))
        rejects(422, smd.quote_selection, smd.Selection(projects=[candidate.projects[0]] * 2))
        assert not outbound
        quote = smd.quote_selection(candidate)
        assert [row["id"] for row in quote["projects"]] == [str(first), str(second)]
        assert set(outbound) == {("POST", "/app/installations/42/access_tokens"),
                                 ("GET", "/repos/example/first/commits/HEAD"),
                                 ("GET", f"/repos/example/second/commits/{pinned}")}
        request = smd.GroupDeployment(offer_token=quote["offer_token"], projects=[
            smd.ProjectSelection(id=row["id"], revision=row["revision"]) for row in quote["projects"]
        ])
        assert smd.deployment(first)["deployment"] is None
        tampered = request.model_copy(update={"projects": [
            smd.ProjectSelection(id=first, revision=replacement), request.projects[1]]})
        rejects(409, smd.deploy_selection, tampered)
        with patch.dict(os.environ, {"SMARTDEV_SMD_DNS_ZONE": "changed.example.invalid"}):
            rejects(409, smd.deploy_selection, request)
        conn.execute("INSERT INTO command_center_project_tenant VALUES (%s,'Other')", (second,))
        rejects(409, smd.deploy_selection, request)
        conn.execute("DELETE FROM command_center_project_tenant WHERE project_id=%s", (second,))
        job = smd.deploy_selection(request)
        assert job == smd.deploy_selection(request)
        assert len(outbound) == 3
        assert smd.deployment(second)["deployment"]["id"] == job["id"]
        overlapping = smd.Selection(projects=[smd.ProjectSelection(id=third), request.projects[1]])
        rejects(409, smd.quote_selection, overlapping)
        conn.execute("UPDATE projects SET repository='example/edited',archived=true WHERE id=%s", (second,))
        rejects(409, smd.claim, job["id"])
        conn.execute("UPDATE projects SET archived=false WHERE id=%s", (second,))
        claim = smd.claim(job["id"])
        assert [entry["repository"] for entry in claim["projects"]] == ["example/first", "example/second"]
        assert all(entry["revision"] == pinned for entry in claim["projects"])
        assert claim["deployment_hostname"] == f"smd-{job['id'].hex}.isolated.example.invalid"
        assert claim["projects"][1]["hostname"] == f"app-{second.hex[:12]}-{job['id'].hex[:12]}.isolated.example.invalid"


def reconcile_legacy(conn, project_id, job_id):
    request = smd.LegacyReconciliation(
        prior_runner_stopped=True, untagged_inventory_reviewed=True,
        evidence_reference="owner inventory review fixture")
    offer = smd.DeploymentRequest(offer_token=smd.deployment(project_id)["offer_token"])
    rejects(409, smd.deploy, project_id, offer)
    historical_instance = uuid4()
    conn.execute("UPDATE smd_deploy_task SET instance_id=%s WHERE id=%s", (historical_instance, job_id))
    rejects(409, smd.reconcile_legacy, job_id, request)
    conn.execute("UPDATE smd_deploy_task SET instance_id=NULL WHERE id=%s", (job_id,))
    tag = f"smd-{job_id}"
    end = {"meta": {"links": {"next": ""}}}
    next_page = {"meta": {"links": {"next": "next-page"}}, "instances": []}
    matching = {**end, "instances": [{"label": "different-label", "tags": [tag]}]}
    clear = {**end, "instances": [{"label": "unrelated-label", "tags": []}]}
    with patch.dict(os.environ, {"SMARTDEV_SMD_RECONCILIATION_VULTR_TOKEN_FILE": ""}):
        rejects(503, smd.reconcile_legacy, job_id, request)
    with patch.dict(os.environ, {"SMARTDEV_SMD_RECONCILIATION_VULTR_TOKEN_FILE": __file__}), \
            patch.object(smd.Path, "read_text", return_value="dummy"), \
            patch.object(smd, "vultr_inventory_page", side_effect=[
                next_page, next_page, next_page, matching, next_page, clear]) as inventory:
        rejects(503, smd.reconcile_legacy, job_id, request)
        rejects(409, smd.reconcile_legacy, job_id, request)
        assert smd.deployment(project_id)["deployment"]["id"] == job_id
        retired = smd.reconcile_legacy(job_id, request)
        assert retired == smd.reconcile_legacy(job_id, request)
        assert inventory.call_count == 6
    assert smd.deployment(project_id)["deployment"] is None
    assert smd.deployment(project_id)["reconciled_deployment"]["id"] == job_id
    assert all(row["status"] != "queued" for row in smd.tasks())
    legacy = next(row for row in smd.tasks() if row["id"] == job_id)
    assert legacy["reconciliation"]["provider_tag"] == tag
    rejects(409, smd.progress, job_id,
            smd.Progress(claim_token="old-claim", status="processing", note="Stale claimant"))
    replacement = smd.deploy(project_id, offer)
    assert replacement["id"] != job_id and replacement["status"] == "queued"


def assert_owner_authorization():
    origin = "https://command.example.invalid"
    key = "ab" * 32
    with patch.dict(os.environ, {
        "PORTAL_ORIGIN": origin,
        "SESSION_KEY": key,
        "MEMORY_ACCESS_KEY": "cd" * 32,
        "OWNER_PASSWORD_HASH": "00:00",
    }):
        from main import app

        endpoint = f"/admin/smd-deploy-tasks/{uuid4()}/reconcile-legacy"
        client = TestClient(app, base_url=origin)
        response = client.post(endpoint, headers={"Origin": origin}, json={})
        assert response.status_code == 401
        client.cookies.set("__Host-command", issue_session(bytes.fromhex(key)))
        response = client.post(endpoint, headers={"Origin": "https://wrong.example.invalid"}, json={})
        assert response.status_code == 403
        response = client.post(endpoint, headers={"Origin": origin}, json={})
        assert response.status_code == 422


if __name__ == "__main__":
    run()
    print("SMD queue integration checks passed.")
