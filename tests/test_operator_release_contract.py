import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from core import workspace_name
from operator_task import authorized_by_mig, mig_payload

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "operator"))
from host_operator import task_release


class OperatorReleaseContractTests(unittest.TestCase):
    def setUp(self):
        self.key = Ed25519PrivateKey.generate()
        self.row = {
            "id": uuid4(), "project_id": uuid4(), "task_id": uuid4(),
            "execution_id": uuid4(), "environment": "dev",
            "operation": "smartdev.dev.release", "mig_receipt": uuid4(),
            "token_sha256": "a" * 64,
            "expires_at": datetime.now(timezone.utc) + timedelta(seconds=45),
            "workspace": "p-xssqdt37lbgnnmsugo7u7zempq",
            "revision": "df1cb6650fe2cb04405f4189860a49ead6b691f0",
        }
        self.row["workspace"] = workspace_name(self.row["project_id"])
        self.config = {
            "SMARTDEV_OPERATOR_TASK_MIG_PUBLIC_KEY": self.key.public_key().public_bytes_raw().hex(),
            "SMARTDEV_OPERATOR_RELEASE_PROJECT_ID": str(self.row["project_id"]),
            "SMARTDEV_OPERATOR_RELEASE_WORKSPACE": self.row["workspace"],
            "SMARTDEV_OPERATOR_RELEASE_REVISION": self.row["revision"],
        }

    def signed(self, row):
        return {**row, "mig_signature": self.key.sign(mig_payload(row)).hex()}

    def test_signed_scope_rejects_substitution(self):
        with patch.dict(os.environ, self.config):
            grant = self.signed(self.row)
            self.assertTrue(authorized_by_mig(grant))
            for fields in (
                {"id": uuid4()}, {"project_id": uuid4()}, {"workspace": "p-" + "a" * 26},
                {"revision": "b" * 40}, {"expires_at": self.row["expires_at"] + timedelta(seconds=1)},
                {"mig_signature": ""},
            ):
                with self.subTest(fields=fields):
                    self.assertFalse(authorized_by_mig({**grant, **fields}))

    def test_valid_signature_does_not_override_enrollment(self):
        with patch.dict(os.environ, self.config):
            self.assertFalse(authorized_by_mig(self.signed({**self.row, "revision": "b" * 40})))
            expired = {**self.row, "expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}
            self.assertFalse(authorized_by_mig(self.signed(expired)))

    def test_legacy_codex_grant_keeps_wire_format(self):
        row = {**self.row, "operation": "codex.version", "workspace": None, "revision": None}
        self.assertTrue(mig_payload(row).startswith(b'["smartdev-operator-task-v1",'))
        with patch.dict(os.environ, self.config):
            self.assertTrue(authorized_by_mig(self.signed(row)))
            self.assertFalse(authorized_by_mig({**self.signed(row), "workspace": self.row["workspace"]}))

    def host_claim(self, row):
        fields = ("project_id", "task_id", "execution_id", "environment", "operation")
        grant = self.signed(row)
        wire = json.loads(json.dumps(grant, default=str))
        return {
            **{key: wire[key] for key in fields}, "approval_id": wire["id"],
            "claim_id": str(uuid4()), "grant": wire, "expires_at": wire["expires_at"],
            "lease_until": (datetime.now(timezone.utc) + timedelta(minutes=4)).isoformat(),
        }

    def run_host(self, directory, claim, deploy):
        cfg = directory / "config.json"
        cfg.write_text(json.dumps({
            "project_id": self.config["SMARTDEV_OPERATOR_RELEASE_PROJECT_ID"],
            "workspace": self.row["workspace"], "revision": self.row["revision"],
            "mig_public_key": self.config["SMARTDEV_OPERATOR_TASK_MIG_PUBLIC_KEY"],
        }))
        receipt = {"id": str(uuid4())}
        task_release(claim, receipt, deploy, directory, cfg)
        return receipt

    def test_operator_consumes_once_and_recovers_same_receipt(self):
        claim = self.host_claim(self.row)
        invoked = []
        with TemporaryDirectory() as temp:
            directory = Path(temp)
            # Repeated receipt recovery uses a newly opened durable journal.
            first = self.run_host(directory, claim, invoked.append)
            second = self.run_host(directory, claim, invoked.append)
            self.assertEqual(first, second)
            self.assertEqual(first["deployed_revision"], self.row["revision"])
            with self.assertRaises(ValueError):
                self.run_host(directory, {**claim, "claim_id": str(uuid4())}, invoked.append)
            self.assertEqual(len(invoked), 1)

    def test_operator_rejects_expired_and_altered_claim_before_execution(self):
        invoked = []
        with TemporaryDirectory() as temp:
            directory = Path(temp)
            expired = self.host_claim({**self.row, "expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)})
            self.assertEqual(self.run_host(directory, expired, invoked.append)["status"], "expired")
            claim = self.host_claim(self.row)
            for changed in ({"workspace": "p-" + "b" * 26}, {"revision": "c" * 40},
                            {"mig_signature": "0" * 128}):
                with self.assertRaises(ValueError):
                    self.run_host(directory, {**claim, "grant": {**claim["grant"], **changed}}, invoked.append)
            self.assertEqual(invoked, [])

    def test_concurrent_claims_use_one_deployment_attempt(self):
        from threading import Event

        claim = self.host_claim(self.row)
        invoked, entered, finish = [], Event(), Event()
        def deploy(grant):
            invoked.append(grant)
            entered.set()
            self.assertTrue(finish.wait(timeout=5))
        with TemporaryDirectory() as temp, ThreadPoolExecutor(max_workers=2) as pool:
            directory = Path(temp)
            first = pool.submit(self.run_host, directory, claim, deploy)
            self.assertTrue(entered.wait(timeout=5))
            duplicate = pool.submit(self.run_host, directory, claim, deploy)
            finish.set()
            self.assertEqual(first.result(), duplicate.result())
            self.assertEqual(len(invoked), 1)

    def test_interrupted_deployment_never_starts_again(self):
        claim = self.host_claim(self.row)
        invoked = []
        def interrupted(grant):
            invoked.append(grant)
            raise SystemExit(7)

        with TemporaryDirectory() as temp:
            directory = Path(temp)
            with self.assertRaises(SystemExit):
                self.run_host(directory, claim, interrupted)
            recovered = self.run_host(directory, claim, invoked.append)
            self.assertEqual(recovered["status"], "unconfirmed")
            self.assertEqual(len(invoked), 1)

    def test_poll_recovers_release_claim_after_legacy_timeout(self):
        from operator_task import poll_task

        claim_id = uuid4()
        row = {
            **self.signed(self.row), "claim_id": claim_id, "claim_timed_out": True,
            "completion_id": None, "invalid": None, "deadline": self.row["expires_at"],
            "release_lease_until": self.row["expires_at"] + timedelta(minutes=3),
        }
        with (patch.dict(os.environ, self.config),
              patch("operator_task.machine_project", return_value=uuid4()),
              patch("operator_task.database") as database,
              patch("operator_task.load", return_value=row),
              patch("operator_task.finish") as finish):
            connection = database.return_value.__enter__.return_value
            connection.execute.return_value.fetchall.return_value = [
                {"id": row["id"], "project_id": row["project_id"]},
            ]
            recovered = poll_task("Bearer test")
            self.assertEqual(recovered["claim_id"], claim_id)
            self.assertEqual(recovered["grant"].mig_signature, row["mig_signature"])
            finish.assert_not_called()
