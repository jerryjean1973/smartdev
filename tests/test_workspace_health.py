import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / "operator" / "workspace_health.py"
SPEC = importlib.util.spec_from_file_location("workspace_health", SOURCE)
health = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(health)


class WorkspaceGitOwnershipTest(TestCase):
    def record(self, root, project="p-xssqdt37lbgnnmsugo7u7zempq"):
        return {
            "Config": {"Env": ["PROJECT_NAME=" + project]},
            "Mounts": [{"Destination": "/projects", "RW": True, "Source": str(root)}],
        }

    def test_repairs_only_validated_git_metadata(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            metadata = root / "p-xssqdt37lbgnnmsugo7u7zempq" / "repository" / ".git"
            metadata.mkdir(parents=True)
            config = metadata / "config"
            config.write_text("[core]\n")
            worktree = metadata.parent / "owned-by-user"
            worktree.write_text("preserve\n")
            with patch.object(health.os, "chown") as chown:
                self.assertTrue(health.repair_git_metadata(self.record(root)))
            repaired = {call.args[0] for call in chown.call_args_list}
            self.assertEqual(repaired, {metadata, config})
            self.assertNotIn(worktree, repaired)

    def test_rejects_unbound_project_path(self):
        with TemporaryDirectory() as folder, patch.object(health.os, "chown") as chown:
            self.assertFalse(health.repair_git_metadata(self.record(folder, "../../etc")))
            chown.assert_not_called()
