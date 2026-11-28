import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))
import smd_git_tokens


class SmdGitWriteTests(unittest.TestCase):
    def test_workspace_token_is_repository_scoped_and_write_capable(self):
        lookup = Mock()
        lookup.json.return_value = {"id": 42}
        issued = Mock()
        issued.json.return_value = {"token": "scoped-token"}
        client = Mock()
        client.get.return_value = lookup
        client.post.return_value = issued
        context = Mock()
        context.__enter__ = Mock(return_value=client)
        context.__exit__ = Mock(return_value=False)
        with (patch.dict(os.environ, {"GITHUB_CLIENT_ID": "client", "GITHUB_INSTALLATION_ID": "42"}),
              patch.object(smd_git_tokens, "app_token", return_value="app-jwt"),
              patch.object(smd_git_tokens.httpx, "Client", return_value=context)):
            self.assertEqual(smd_git_tokens.make_token("jerryjean1973/smartdev"), "scoped-token")
        client.post.assert_called_once_with("/app/installations/42/access_tokens", json={
            "repositories": ["smartdev"], "permissions": {"contents": "write"},
        })


if __name__ == "__main__":
    unittest.main()
