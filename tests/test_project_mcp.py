import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))
import project_mcp


class ProjectMcpTests(unittest.TestCase):
    def test_endpoint_requires_exact_https_allowlist_entry(self):
        with patch.dict("os.environ", {"SMARTDEV_MCP_ALLOWED_ENDPOINTS": "https://native.invalid/mcp"}):
            self.assertTrue(project_mcp.allowed_endpoint("https://native.invalid/mcp"))
            for endpoint in ("http://native.invalid/mcp", "https://native.invalid/other",
                             "https://native.invalid/mcp?redirect=1", "https://other.invalid/mcp"):
                self.assertFalse(project_mcp.allowed_endpoint(endpoint))

    def test_lease_token_cannot_access_another_project_or_binding(self):
        project = UUID(int=1)
        with patch.object(project_mcp, "queue") as queue, patch.object(project_mcp, "active_run") as validate:
            queue.return_value.get.return_value = json.dumps([str(project), {}, {"bripe": 1}])
            for scope, name in ((UUID(int=2), "bripe"), (project, "other")):
                with self.assertRaises(HTTPException) as error:
                    project_mcp.target(scope, name, "Bearer " + "a" * 43)
                self.assertEqual(error.exception.status_code, 403)
            validate.assert_not_called()

    def test_credential_rotation_resolves_current_project_vault_secret(self):
        project = UUID(int=1)
        config = {"secret_name": "BRIPE_MCP_CLIENT_KEY", "credential_header": "x-mig-api-key"}
        with patch.object(project_mcp, "call") as vault:
            for value in ("credential-before-rotation", "credential-after-rotation"):
                vault.return_value = {"data": {"data": {"value": value}}}
                self.assertEqual(project_mcp.upstream_headers(project, config, {}), {"x-mig-api-key": value})
                self.assertEqual(vault.call_args.args[1], f"data/projects/{project}/DEV/BRIPE_MCP_CLIENT_KEY")
