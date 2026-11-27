import sys
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "workspace"))
from checkpoints import checkpoint, command, restore


class CheckpointTests(unittest.TestCase):
    def test_recovery_round_trip_preserves_unrelated_changes_and_refuses_overlap(self):
        with tempfile.TemporaryDirectory() as root:
            project = Path(root)
            (project / "config").mkdir()
            repo = project / "repository"
            repo.mkdir()
            command(repo, "init", "-b", "main")
            (repo / "app.js").write_text("export const version = 1;\n")
            (repo / "other.js").write_text("export const unrelated = false;\n")
            command(repo, "add", ".")
            command(repo, "-c", "user.name=Test", "-c", "user.email=test@localhost", "commit", "-m", "initial")
            head = command(repo, "rev-parse", "HEAD").decode().strip()
            args = {"objective": str(uuid4()), "execution": str(uuid4()), "branch": "main", "head": head}
            first = checkpoint(repo, project, phase="before-execution", **args)
            (repo / "app.js").write_text("export const version = 2;\n")
            second = checkpoint(repo, project, phase="after-execution", result=0, **args)
            self.assertEqual(second["changedFiles"], ["app.js"])
            self.assertEqual(command(repo, "rev-parse", f"refs/smartdev/checkpoints/{first['id']}").decode().strip(), first["sha"])
            (repo / "other.js").write_text("export const unrelated = true;\n")
            restore(repo, project, second, first, "app.js")
            self.assertIn("version = 1", (repo / "app.js").read_text())
            self.assertIn("unrelated = true", (repo / "other.js").read_text())
            restore(repo, project, first, second, "app.js")
            self.assertIn("version = 2", (repo / "app.js").read_text())
            (repo / "app.js").write_text("export const version = 3;\n")
            with self.assertRaisesRegex(ValueError, "Affected files have changed"):
                restore(repo, project, second, first)
            self.assertIn("version = 3", (repo / "app.js").read_text())
            (repo / ".env").write_text("PRIVATE=test-only\n")
            private = checkpoint(repo, project, phase="before-execution", **args)
            self.assertIn(".env", private["excluded"])
            self.assertNotIn(b".env", command(repo, "ls-tree", "-r", "--name-only", private["sha"]))
            self.assertEqual(command(repo, "rev-parse", "HEAD").decode().strip(), head)
