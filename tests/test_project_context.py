import hashlib
import json
import hashlib
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from workspace.project_context import current_notes, prompt_context, refresh


class ProjectContextTests(unittest.TestCase):
    def test_sql_summary_expires_when_its_source_changes_or_disappears(self):
        with TemporaryDirectory() as folder:
            checkout = Path(folder)
            document = checkout / "architecture.md"
            document.write_bytes(b"Architecture v1")
            response = {
                "notes": ["Summary of v1", "Legacy historical note"],
                "note_sources": {"0": {"path": "architecture.md",
                    "sha256": hashlib.sha256(document.read_bytes()).hexdigest()}},
            }
            self.assertEqual(current_notes(checkout, response), response["notes"])
            document.write_bytes(b"Architecture v2")
            self.assertEqual(current_notes(checkout, response), ["Legacy historical note"])
            document.unlink()
            self.assertEqual(current_notes(checkout, response), ["Legacy historical note"])

    def test_checkout_scope_and_stale_sources(self):
        with TemporaryDirectory() as folder:
            project = Path(folder)
            main = project / "repository"
            task = project / "worktrees" / "task-one"
            other = project / "other-project"
            for directory in (main, task, other):
                directory.mkdir(parents=True)
            files = {
                "AGENTS.md": "root instructions",
                "src/AGENTS.override.md": "nested instructions",
                "src/.agents/skills/development/SKILL.md": "native skill",
                "docs/architecture.md": "architecture is authoritative",
                "docs/decisions.md": "decision record",
                "pyproject.toml": "[project]",
                ".codex/config.toml": "secret-shaped fixture must stay out of the index",
            }
            for name, body in files.items():
                source = main / name
                source.parent.mkdir(exist_ok=True, parents=True)
                source.write_text(body)
            (task / "AGENTS.md").write_text("task instructions")
            (main / "docs/symlink.md").symlink_to(other / "secret.md")
            index = refresh(main, project)
            self.assertEqual(set(index["sources"]), set(files))
            self.assertEqual(index["stack"], ["Python"])
            self.assertEqual(index["source_roots"], ["src"])
            self.assertEqual(index["sources"]["src/.agents/skills/development/SKILL.md"]["kind"], "skills")
            self.assertNotIn("secret-shaped", json.dumps(index))
            context = prompt_context(main, project, "Review architecture")
            self.assertIn("docs/architecture.md", context)
            self.assertNotIn(files["docs/architecture.md"], context)
            self.assertEqual(set(refresh(task, project)["sources"]), {"AGENTS.md"})
            self.assertEqual(refresh(other, other)["sources"], {})
            (main / "docs/architecture.md").write_text("revised architecture")
            (main / "docs/decisions.md").unlink()
            self.assertEqual(refresh(main, project)["stale"], ["docs/architecture.md", "docs/decisions.md"])
            self.assertEqual((main / "AGENTS.md").read_text(), files["AGENTS.md"])
            self.assertEqual(len(list((project / "config").glob("context-*.json"))), 2)

    def test_asset_reference_survives_new_context_and_excludes_tombstones(self):
        with TemporaryDirectory() as folder:
            root=Path(folder)
            project=root / "project"
            main=project / "repository"
            task=project / "worktrees" / "one"
            uploads=project / "uploads"
            home=root / "home"
            key=hashlib.sha256(str(project).encode()).hexdigest()
            catalog=home / ".local" / "state" / "command-web-chat" / (key+".uploads")
            for directory in (main,task,uploads,catalog):
                directory.mkdir(parents=True,exist_ok=True)
            data=b'[{"lead":"fixture-private-data"}]'
            asset=uploads / "asset-content"
            asset.write_bytes(data)
            entry={"id":"fixture","filename":asset.name,"originalName":"full-leads.json",
                "size":len(data),"sha256":hashlib.sha256(data).hexdigest(),"mimeType":"application/json"}
            metadata=catalog / "fixture.json"
            metadata.write_text(json.dumps(entry))
            with patch("workspace.project_context.Path.home",return_value=home):
                for run in range(10):
                    prompt=prompt_context(main if run%2 else task,project,"Generate projections from my JSON leads.")
                    self.assertIn(str(asset),prompt)
                    self.assertNotIn("fixture-private-data",prompt)
                self.assertNotIn(str(asset),prompt_context(task,project,"Change the navigation."))
                metadata.write_text(json.dumps({**entry,"deletedAt":"deleted"}))
                self.assertNotIn(str(asset),prompt_context(main,project,"Generate projections from my JSON leads."))


if __name__ == "__main__":
    unittest.main()
