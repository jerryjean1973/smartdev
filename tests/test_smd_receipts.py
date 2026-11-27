"""Exercise signed deployment receipt rules against isolated PostgreSQL."""

from base64 import b64encode
from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from time import time
from unittest.mock import patch
from uuid import UUID, uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import HTTPException

import smd_receipts as receipts
import smd_git_tokens as git_tokens
from smd_product_context import approved_qa_environment, bind_contexts
from smd_release_assets import release_asset
from store import database


def rejects(status, operation, *args):
    try:
        operation(*args)
    except HTTPException as error:
        assert error.status_code == status
    else:
        raise AssertionError(f"Expected HTTP {status}")


def run():
    with TemporaryDirectory() as tmp, patch.dict(os.environ, SMARTDEV_SMD_PRODUCT_CONTEXT_DIRECTORY=tmp,
            SMARTDEV_SMD_VERSIONED_INSTALLERS=tmp, SMARTDEV_SMD_RELEASE_DOWNLOAD_TOKEN="1" * 64,
            SMARTDEV_SMD_RELEASE_DIRECTORY=tmp):
        project_id, revision = str(uuid4()), "a" * 40
        project = {"id": project_id, "revision": revision}
        bind_contexts([project])
        assert "build_context_sha256" not in project and project["verify_installation"] is True
        folder = Path(tmp) / project_id
        folder.mkdir()
        (folder / "qa-required.json").write_text('["LICENSE_KEY"]')
        with patch("vault.list_secrets", return_value=[]) as metadata:
            rejects(409, bind_contexts, [project])
            metadata.assert_not_called()
        digest = "b" * 64
        (folder / (revision + ".sha256")).write_text(digest)
        (folder / (digest + ".tar.gz")).touch()
        with patch("vault.list_secrets", side_effect=AssertionError("Deployment cannot depend on tenant settings")):
            bind_contexts([project])
        assert project["build_context_sha256"] == digest
        assert project["tenant_setup"] and "qa_runtime_versions" not in project
        project["qa_runtime_versions"] = {"LICENSE_KEY": 3}  # previously authorized legacy offer
        with patch("vault.call", return_value={"data": {"data": {"value": "fixture"}, "metadata": {"version": 3}}}) as read, patch("vault.audit"):
            assert approved_qa_environment(project) == b"LICENSE_KEY=fixture\n"
            read.assert_called_once_with("GET", f"data/projects/{project_id}/QA/LICENSE_KEY?version=3", missing=True)
            rejects(403, approved_qa_environment, {**project, "qa_runtime_versions": {"OPENAI_API_KEY": 3}})
        with patch("vault.call", return_value=None):
            rejects(409, approved_qa_environment, project)
        rejects(404, release_asset, "2" * 64, "installer.sh")
        original = Path(tmp) / "bundle.tar"
        original.touch()
        assert release_asset("1" * 64, "bundle.tar").path == original
        versioned = Path(tmp) / sha256(("2" * 64).encode()).hexdigest()
        versioned.mkdir()
        candidate = versioned / "installer.sh"
        candidate.touch()
        assert release_asset("2" * 64, "installer.sh").path == candidate
        rejects(404, release_asset, "2" * 64, "bundle.tar")
    task, project, instance = uuid4(), uuid4(), uuid4()
    claim = "isolated-test"
    revision = "release-test"
    private = Ed25519PrivateKey.generate()
    public_key = b64encode(private.public_key().public_bytes_raw()).decode()
    message = f"{task}\n{project}\n{instance}\n{revision}\n".encode("ascii")
    signature = b64encode(private.sign(message)).decode()
    enrollment = receipts.Enrollment(claim_token=claim, instance_id=instance, public_key=public_key)
    document = receipts.Receipt(project_id=project, instance_id=instance, revision=revision, signature=signature)
    with database() as db:
        db.execute("""CREATE TEMP TABLE smd_deploy_task (
            id uuid PRIMARY KEY, project_id uuid, instance_id uuid,
            release_revision text, status text, claim_digest text,
            dns_zone text, project_manifest jsonb, reconciled_at timestamptz
        )""")
        migration = Path("api/044_smd_receipts.sql").read_text()
        db.execute(migration.replace("CREATE TABLE IF NOT EXISTS", "CREATE TEMP TABLE"))
        db.execute(Path("api/047_smd_connection_receipts.sql").read_text().replace(
            "CREATE TABLE IF NOT EXISTS", "CREATE TEMP TABLE"))
        db.execute("INSERT INTO smd_deploy_task VALUES (%s,%s,NULL,%s,'processing',%s,NULL,NULL)",
                   (task, project, revision, sha256(claim.encode()).hexdigest()))

        @contextmanager
        def transaction():
            with db.transaction():
                yield db

        with patch.object(receipts, "database", transaction):
            rejects(409, receipts.receive, task, document)
            rejects(403, receipts.enroll, task, enrollment.model_copy(update={"claim_token": "wrong"}))
            assert receipts.enroll(task, enrollment)["enrolled"]
            assert receipts.enroll(task, enrollment)["enrolled"]
            rejects(409, receipts.enroll, task, enrollment.model_copy(update={"instance_id": uuid4()}))
            rejects(409, receipts.receive, task, document.model_copy(update={"project_id": uuid4()}))
            rejects(409, receipts.receive, task, document.model_copy(update={"instance_id": uuid4()}))
            rejects(409, receipts.receive, task, document.model_copy(update={"revision": "other"}))
            rejects(403, receipts.receive, task, document.model_copy(update={
                "signature": b64encode(Ed25519PrivateKey.generate().sign(message)).decode(),
            }))
            assert not receipts.status(task)["verified"]
            db.execute("UPDATE smd_deploy_task SET status='needs_attention' WHERE id=%s", (task,))
            assert receipts.receive(task, document)["verified"]
            assert receipts.receive(task, document)["verified"]
            assert receipts.status(task)["verified"]
            job = {"id": task, "release_revision": revision}
            assert receipts.accepted_receipt(db, job, instance, revision)
            assert not receipts.accepted_receipt(db, job, uuid4(), revision)
            grouped, second_project, second_instance = uuid4(), uuid4(), uuid4()
            zone, git_revision = "deploy.example.test", "a" * 40
            db.execute("""INSERT INTO smd_deploy_task VALUES (%s,%s,NULL,%s,'processing',%s,%s,%s::jsonb)""",
                       (grouped, second_project, revision, sha256(claim.encode()).hexdigest(), zone,
                        json.dumps([{"id": str(second_project), "revision": git_revision, "repository": "owner/project"}])))
            grouped_key = enrollment.model_copy(update={"instance_id": second_instance})
            rejects(422, receipts.enroll, grouped, grouped_key)
            grouped_key = receipts.Enrollment.model_validate({
                **grouped_key.model_dump(), "ipv4": "8.8.8.8",
            })
            assert receipts.enroll(grouped, grouped_key)["enrolled"]
            timestamp, nonce = int(time()), "d" * 64
            request_message = (f"git-token\n{grouped}\n{second_project}\n{second_instance}\n"
                               f"{timestamp}\n{nonce}\n").encode("ascii")
            request = git_tokens.GitTokenRequest(project_id=second_project, instance_id=second_instance,
                timestamp=timestamp, nonce=nonce, signature=b64encode(private.sign(request_message)).decode())
            with patch.object(git_tokens, "database", transaction), patch.object(
                    git_tokens, "make_token", return_value="synthetic-scoped-token") as mint:
                rejects(403, git_tokens.issue, grouped, request.model_copy(update={"project_id": uuid4()}))
                rejects(403, git_tokens.issue, grouped, request.model_copy(update={"timestamp": timestamp - 121}))
                response = git_tokens.issue(grouped, request)
                assert response.headers["cache-control"] == "no-store"
                assert json.loads(response.body) == {"token": "synthetic-scoped-token"}
                mint.assert_called_once_with("owner/project")
                rejects(409, git_tokens.issue, grouped, request)
                with TemporaryDirectory() as tmp, patch.dict(os.environ, SMARTDEV_SMD_PRODUCT_CONTEXT_DIRECTORY=tmp):
                    rejects(403, git_tokens.issue, grouped, request.model_copy(update={"artifact": "build-context"}))
                    def signed_context(nonce, kind="build-context"):
                        message = (f"{kind}\n{grouped}\n{second_project}\n{second_instance}\n"
                                   f"{timestamp}\n{nonce}\n").encode("ascii")
                        return request.model_copy(update={"artifact": kind, "nonce": nonce,
                            "signature": b64encode(private.sign(message)).decode()})
                    rejects(403, git_tokens.issue, grouped, signed_context("a" * 64))
                    digest = sha256(b"test artifact").hexdigest()
                    store = Path(tmp) / str(second_project)
                    store.mkdir()
                    archive = store / (digest + ".tar.gz")
                    archive.write_bytes(b"test artifact")
                    db.execute("""UPDATE smd_deploy_task SET project_manifest=jsonb_set(
                        project_manifest,'{0,build_context_sha256}',to_jsonb(%s::text)) WHERE id=%s""", (digest, grouped))
                    typed = signed_context("b" * 64)
                    delivery = git_tokens.issue(grouped, typed)
                    assert delivery.path == archive and delivery.media_type == "application/gzip"
                    rejects(409, git_tokens.issue, grouped, typed)
                    mint.assert_called_once()
                    rejects(410, git_tokens.issue, grouped, signed_context("e" * 64, "qa-runtime"))
                    (store / "qa-required.json").write_text('["CUSTOMER_OPENAI_API_KEY"]')
                    db.execute("""UPDATE smd_deploy_task SET project_manifest=jsonb_set(
                        project_manifest,'{0,qa_runtime_versions}','{"CUSTOMER_OPENAI_API_KEY":2}') WHERE id=%s""", (grouped,))
                    runtime_request = signed_context("f" * 64, "qa-runtime")
                    with patch("vault.call", return_value={"data": {"data": {"value": "fixture-app-key"},
                            "metadata": {"version": 2}}}) as scoped, patch("vault.audit"):
                        rejects(410, git_tokens.issue, grouped, runtime_request)
                        scoped.assert_not_called()
                        rejects(409, git_tokens.issue, grouped, runtime_request)
            connection = {
                "ipv4": "8.8.8.8", "ssh_user": "root", "portal_url": f"https://smd-{grouped.hex}.{zone}",
                "coder_url": f"https://coder-{grouped.hex}.{zone}", "apps": [{
                    "project_id": str(second_project), "revision": git_revision,
                    "url": f"https://app-{second_project.hex[:12]}-{grouped.hex[:12]}.{zone}",
                }],
            }
            message = (f"{grouped}\n{second_project}\n{second_instance}\n{revision}\n"
                       + json.dumps(connection, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
            signed = receipts.Receipt(project_id=second_project, instance_id=second_instance,
                revision=revision, signature=b64encode(private.sign(message)).decode(), connections=connection)
            rejects(409, receipts.receive, grouped, signed.model_copy(update={"connections": None}))
            wrong_url = signed.connections.apps[0].model_copy(update={"url": "https://unrelated.example.test"})
            rejects(409, receipts.receive, grouped, signed.model_copy(update={"connections":
                signed.connections.model_copy(update={"apps": [wrong_url]})}))
            assert receipts.receive(grouped, signed)["verified"]
            assert receipts.status(grouped)["connections"] == connection
            unverified = signed.connections.model_copy(update={"apps": [
                signed.connections.apps[0].model_copy(update={"runtime_status":"runtime_unverified"})]})
            observation = {"expected_ipv4":unverified.ipv4}
            entry = {"id":str(second_project),"revision":git_revision}
            job = {"id":grouped,"dns_zone":zone,"project_manifest":[entry]}
            assert not receipts.connections_match(job, observation, unverified)
            entry["verify_installation"] = True
            assert receipts.connections_match(job, observation, unverified)
            entry["tenant_setup"] = True
            assert not receipts.connections_match(job, observation, unverified)


if __name__ == "__main__":
    run()
    print("SMD receipt integration checks passed.")
