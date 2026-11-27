"""Exercise the pinned project checkout and credential boundary."""
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "release" / "smd"))
from projects import load_manifest, stage_projects

PROJECT = "12345678-1234-1234-1234-123456789abc"


class ProjectStagingTest(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "user.name", "Fixture")
        (self.source / "index.txt").write_text("approved checkout\n")
        self.git("add", "index.txt")
        self.git("commit", "-m", "approved revision")
        self.revision = self.git("rev-parse", "HEAD")
        (self.source / "index.txt").write_text("newer checkout\n")
        self.git("commit", "-am", "another revision")
        self.data = self.root / "data"
        bundles = self.data / "git-bundles"
        bundles.mkdir(parents=True)
        self.git("bundle", "create", str(bundles / f"{PROJECT}.bundle"), "--all")
        self.project = {"id": PROJECT, "repository": "owner/repository", "revision": self.revision,
                        "name": "Fixture", "runtime": "other", "hostname": "example.invalid"}

    def git(self, *arguments):
        return subprocess.run(["git", *arguments], cwd=self.source, check=True, text=True,
                              capture_output=True).stdout.strip()

    def manifest(self, projects):
        filename = self.root / "manifest.json"
        filename.write_text(json.dumps({"projects": projects}))
        return load_manifest(filename)

    def test_pins_historical_git_commit_and_keeps_source_credentials_out(self):
        selected = self.manifest([self.project])
        stage_projects(self.data, selected)
        stage_projects(self.data, selected)
        checkout = self.data / "checkouts" / PROJECT / self.revision
        self.assertEqual((checkout / "index.txt").read_text(), "approved checkout\n")
        self.assertIn("https://github.com/owner/repository.git", (checkout / ".git/config").read_text())

    def test_unknown_pin_cannot_be_substituted_with_latest_commit(self):
        selected = self.manifest([{**self.project, "revision": "a" * 40}])
        with self.assertRaisesRegex(ValueError, "checkout failed"):
            stage_projects(self.data, selected)
        self.assertFalse((self.data / "checkouts" / PROJECT / ("a" * 40)).exists())

    def test_rejects_duplicate_project_and_invalid_git_revision(self):
        with self.assertRaises(ValueError):
            self.manifest([self.project, self.project])
        with self.assertRaises(ValueError):
            self.manifest([{**self.project, "revision": "main"}])


if __name__ == "__main__":
    unittest.main()
