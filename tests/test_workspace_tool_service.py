import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "workspace_tool_service",
    Path(__file__).resolve().parents[1] / "operator" / "workspace_tool_service.py",
)
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)


class WorkspaceGitPolicyTests(unittest.TestCase):
    def test_git_update_policy_is_system_scoped(self):
        calls = []
        with patch.object(service, "docker", side_effect=lambda *args, **kwargs: calls.append(args) or ""):
            service.configure_git_policy("workspace-container")

        configured = {call[-2]: call[-1] for call in calls}
        self.assertEqual(configured, {
            "credential.https://github.com.useHttpPath": "true",
            "protocol.smartdev.allow": "always",
            "pull.ff": "only",
            "submodule.recurse": "true",
            "fetch.recurseSubmodules": "on-demand",
        })
        self.assertTrue(all(call[:7] == (
            "exec", "-u", "0", "workspace-container", "git", "config", "--system"
        ) for call in calls))


if __name__ == "__main__":
    unittest.main()
