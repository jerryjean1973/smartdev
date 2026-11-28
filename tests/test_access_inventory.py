import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))
import access_inventory


class AccessInventoryTests(unittest.TestCase):
    project = UUID(int=42)
    project_row = {
        "id": project, "name": "SmartDev", "repository": "owner/smartdev",
        "workspace_url": "https://coder.qa.example/@smartdev/p-workspace", "tenant": "current-tenant",
    }
    environment = {
        "PORTAL_ORIGIN": "https://cc.qa.example",
        "CODER_PUBLIC_URL": "https://coder.qa.example",
        "SMARTDEV_ENVIRONMENT": "QA",
        "SMD_PORTAL_TASK": str(UUID(int=10)),
        "SMD_PORTAL_INSTANCE": str(UUID(int=11)),
        "SMD_PORTAL_ISSUER": "https://owner.example",
    }

    def resolve(self, marker="credential-v1", environment="QA"):
        with (patch.dict(os.environ, self.environment, clear=True),
              patch.object(access_inventory, "project_record", return_value=dict(self.project_row))):
            return access_inventory.resolve(self.project, environment, "codex_cli", marker)

    def test_fresh_session_inventories_all_four_paths_and_exact_scope(self):
        result = self.resolve()
        self.assertEqual(set(result["paths"]), {
            "cc_app", "cc_host_operator", "cc_coder_workspaces", "cc_deployments",
        })
        self.assertTrue(all(path["status"] == "available" for path in result["paths"].values()))
        self.assertEqual(result["selection"], {
            "tenant": "current-tenant", "project_id": str(self.project), "environment": "QA",
        })
        self.assertEqual(result["actual_executor"], "codex_cli")
        self.assertNotIn("credential-v1", str(result))

    def test_environment_mismatch_is_explicit_and_never_rerouted(self):
        result = self.resolve(environment="DEV")
        deployment = result["paths"]["cc_deployments"]
        self.assertEqual(deployment["status"], "unavailable")
        self.assertIn("is QA, not DEV", deployment["missing_binding_or_permission"])
        self.assertEqual(result["selection"]["environment"], "DEV")

    def test_missing_coder_binding_fails_closed_with_exact_binding(self):
        project = {**self.project_row, "workspace_url": "https://other.example/p-workspace"}
        with (patch.dict(os.environ, self.environment, clear=True),
              patch.object(access_inventory, "project_record", return_value=project),
              self.assertRaisesRegex(HTTPException, "Missing Coder binding")):
            access_inventory.resolve(self.project, "QA", "codex_cli", "credential-v1")

    def test_credential_renewal_changes_receipt_without_exposing_credential(self):
        first = self.resolve("credential-v1")
        renewed = self.resolve("credential-v2")
        self.assertNotEqual(first["receipt"]["sha256"], renewed["receipt"]["sha256"])
        self.assertNotEqual(first["receipt"]["id"], renewed["receipt"]["id"])
        self.assertNotIn("credential-v2", str(renewed))

    def test_environment_must_be_explicit(self):
        with self.assertRaisesRegex(HTTPException, "Explicit environment required"):
            self.resolve(environment="")


if __name__ == "__main__":
    unittest.main()
