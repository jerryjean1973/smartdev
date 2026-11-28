import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))
import smd_environment_fixes as fixes


class EnvironmentFixTests(unittest.TestCase):
    def setUp(self):
        self.owner = UUID(int=1)
        self.tenant = UUID(int=2)
        self.project = UUID(int=3)
        self.deployment = UUID(int=4)
        self.client_request = UUID(int=5)
        self.grant = {"id": UUID(int=6), "owner_id": self.owner,
                      "project_ids": [self.project]}
        self.deployment_row = {
            "deployment_id": self.deployment,
            "deployment_project_id": self.project,
            "deployment_hostname": "smd-example.qa.example.test",
            "dns_zone": "qa.example.test", "instance_id": UUID(int=7),
            "project_manifest": [{"id": str(self.project)}], "status": "deployed",
            "reconciled_at": None, "tenant_id": self.tenant, "assignment_version": 1,
            "connections": {"portal_url": "https://smd-example.qa.example.test"},
            "verified_at": datetime(2026, 11, 28, tzinfo=timezone.utc),
        }

    def test_request_is_bound_to_explicit_tenant_deployment_project_and_environment(self):
        db = Mock()
        tenant_result = Mock()
        tenant_result.fetchall.return_value = [{"tenant_id": self.tenant}]
        environment_result = Mock()
        environment_result.fetchone.return_value = self.deployment_row
        missing_result = Mock()
        missing_result.fetchone.return_value = None
        inserted_result = Mock()
        inserted_result.fetchone.return_value = {
            "id": UUID(int=9), "payload_digest": "a" * 64, "status": "queued",
            "receipt": None, "failure_reason": None,
        }
        db.execute.side_effect = [tenant_result, environment_result, missing_result, inserted_result]
        context = Mock()
        context.__enter__ = Mock(return_value=db)
        context.__exit__ = Mock(return_value=False)
        with patch.object(fixes, "database", return_value=context), patch.object(fixes, "notify"):
            result = fixes.request_fix(self.grant, self.deployment, self.project, "QA",
                                       "Repair the tenant QA navigation.", self.client_request,
                                       "Repair QA navigation")
        self.assertEqual(result["status"], "queued")
        insert_args = db.execute.call_args_list[-1].args[1]
        self.assertEqual(insert_args[3:7], (self.tenant, self.deployment, self.project, "QA"))

    def test_cross_project_and_wrong_environment_fail_before_queue_write(self):
        with self.assertRaises(LookupError):
            fixes.request_fix(self.grant, self.deployment, UUID(int=20), "QA",
                              "Repair navigation.", self.client_request)
        db = Mock()
        tenant_result = Mock()
        tenant_result.fetchall.return_value = [{"tenant_id": self.tenant}]
        environment_result = Mock()
        environment_result.fetchone.return_value = self.deployment_row
        db.execute.side_effect = [tenant_result, environment_result]
        context = Mock()
        context.__enter__ = Mock(return_value=db)
        context.__exit__ = Mock(return_value=False)
        with patch.object(fixes, "database", return_value=context):
            with self.assertRaises(LookupError):
                fixes.request_fix(self.grant, self.deployment, self.project, "DEV",
                                  "Repair navigation.", self.client_request)
        self.assertEqual(db.execute.call_count, 2)

    def test_multi_tenant_grant_is_rejected(self):
        db = Mock()
        result = Mock()
        result.fetchall.return_value = [{"tenant_id": self.tenant}, {"tenant_id": UUID(int=30)}]
        db.execute.return_value = result
        with self.assertRaisesRegex(LookupError, "one current tenant"):
            fixes.tenant_scope(db, self.grant)


if __name__ == "__main__":
    unittest.main()
