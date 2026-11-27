"""Run native Git against local fixture remotes. Only network fetch is substituted."""

import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "workspace"))
import workspace_git as manager


class ManagedUpdateTest(unittest.TestCase):
    def native(self, cwd, *args):
        return subprocess.check_output(
            [
                "git",
                "-c",
                "user.name=Test",
                "-c",
                "user.email=fixture@example.test",
                "-c",
                "protocol.file.allow=always",
                *args,
            ],
            cwd=cwd,
            stderr=subprocess.PIPE,
            text=True,
        ).strip()

    def commit(self, repo, filename, text):
        (repo / filename).write_text(text)
        self.native(repo, "add", filename)
        self.native(repo, "commit", "-qm", filename)
        return self.native(repo, "rev-parse", "HEAD")

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "config").mkdir()
        self.upstream = self.root / "upstream"
        self.upstream.mkdir()
        self.native(self.upstream, "init", "-qb", "main")
        self.commit(self.upstream, "base.txt", "initial\n")
        self.cwd = self.root / "repository"
        self.native(self.root, "clone", "-q", str(self.upstream), str(self.cwd))
        self.remote = "https://github.com/fixture/app.git"
        self.native(self.cwd, "remote", "set-url", "origin", self.remote)
        self.remotes = {self.remote: self.upstream}
        self.enterContext(patch.dict(os.environ, {"PROJECT_REPOSITORY": "fixture/app"}))
        original = manager.git

        def transport(repo, *args, check=True, **kw):
            if args[0] == "fetch":
                translated = [str(self.remotes[x]) if x in self.remotes else x for x in args]
                return subprocess.run(
                    ["git", "-c", "protocol.file.allow=always", *translated],
                    cwd=repo,
                    check=check,
                    capture_output=True,
                )
            return original(repo, *args, check=check, **kw)

        self.enterContext(patch.object(manager, "git", side_effect=transport))

    def sync(self, op="pull", authorize=lambda: None):
        return manager.update(self.root, self.cwd, {"operation": op}, authorize)

    def test_real_forward_preserves_staged_unstaged_and_untracked(self):
        (self.cwd / "base.txt").write_text("staged\n")
        self.native(self.cwd, "add", "base.txt")
        (self.cwd / "base.txt").write_text("working\n")
        (self.cwd / "notes").write_text("untracked")
        index = self.native(self.cwd, "diff", "--cached", "--binary")
        work = self.native(self.cwd, "diff", "--binary")
        target = self.commit(self.upstream, "new.txt", "upstream\n")
        known = self.sync("fetch")
        self.assertNotEqual(known["head"], target)
        self.assertEqual(self.sync()["head"], target)
        self.assertEqual(self.native(self.cwd, "diff", "--cached", "--binary"), index)
        self.assertEqual(self.native(self.cwd, "diff", "--binary"), work)
        self.assertEqual((self.cwd / "notes").read_text(), "untracked")
        self.assertEqual(self.sync()["status"], "current")

    def test_nonoverlapping_changes_in_same_file_merge_and_keep_backup(self):
        text = "first\n" + "middle\n" * 15 + "last\n"
        self.commit(self.upstream, "both", text)
        self.sync()
        (self.cwd / "both").write_text(text.replace("first", "my change"))
        target = self.commit(self.upstream, "both", text.replace("last", "their change"))
        result = self.sync()
        self.assertEqual(result["head"], target)
        self.assertEqual(
            (self.cwd / "both").read_text(),
            text.replace("first", "my change").replace("last", "their change"),
        )
        self.assertIn(b"my change", (self.root / result["recovery_paths"][0] / "local.patch").read_bytes())
        self.assertEqual(self.native(self.cwd, "diff", "--cached"), "")

    def test_conflict_is_non_destructive_and_names_the_path(self):
        before = self.native(self.cwd, "rev-parse", "HEAD")
        (self.cwd / "base.txt").write_text("local\n")
        self.commit(self.upstream, "base.txt", "remote\n")
        with self.assertRaisesRegex(manager.UpdateError, "base.txt"):
            self.sync()
        self.assertEqual(self.native(self.cwd, "rev-parse", "HEAD"), before)
        self.assertEqual((self.cwd / "base.txt").read_text(), "local\n")

    def test_divergence_and_lost_lease(self):
        base = self.native(self.cwd, "rev-parse", "HEAD")
        self.commit(self.upstream, "remote", "remote")

        def revoked():
            raise manager.UpdateError("expired")

        with self.assertRaisesRegex(manager.UpdateError, "expired"):
            self.sync(authorize=revoked)
        self.assertEqual(self.native(self.cwd, "rev-parse", "HEAD"), base)
        own = self.commit(self.cwd, "local", "local")
        with self.assertRaisesRegex(manager.UpdateError, "diverged"):
            self.sync()
        self.assertEqual(self.native(self.cwd, "rev-parse", "HEAD"), own)

    def test_submodule_pin_and_local_change(self):
        inner = self.root / "inner"
        inner.mkdir()
        self.native(inner, "init", "-qb", "main")
        self.commit(inner, "file", "keep")
        self.native(self.upstream, "submodule", "add", "-q", str(inner), "product")
        url = "https://github.com/fixture/lib.git"
        self.native(self.upstream, "config", "--file", ".gitmodules", "submodule.product.url", url)
        self.native(self.upstream, "add", ".gitmodules", "product")
        self.native(self.upstream, "commit", "-qm", "first pin")
        self.assertIn("repository/product", self.sync()["uninitialized_submodules"])
        self.native(self.cwd, "clone", "-q", str(inner), "product")
        checkout = self.cwd / "product"
        self.native(checkout, "remote", "set-url", "origin", url)
        self.remotes[url] = inner
        (checkout / "file").write_text("local changes")
        pin = self.commit(inner, "new", "release")
        self.native(self.upstream / "product", "fetch", "-q")
        self.native(self.upstream / "product", "checkout", "-q", pin)
        self.native(self.upstream, "add", "product")
        self.native(self.upstream, "commit", "-qm", "new pin")
        self.assertEqual(self.sync()["submodules_updated"], 1)
        self.assertEqual(self.native(checkout, "rev-parse", "HEAD"), pin)
        self.assertEqual((checkout / "file").read_text(), "local changes")

    def test_different_origin_is_rejected_before_fetch(self):
        self.native(self.cwd, "remote", "set-url", "origin", "https://github.com/fixture/other.git")
        with self.assertRaisesRegex(manager.UpdateError, "registered|enrolled"):
            self.sync()
        self.assertEqual(
            self.native(self.cwd, "rev-parse", "HEAD"), self.native(self.upstream, "rev-parse", "HEAD")
        )

    def test_lease_scope_cannot_select_another_tree_or_a_read_only_run(self):
        context_path = self.root / "context.json"
        context = {
            "directory": str(self.cwd),
            "branch": "main",
            "mode": "workspace-write",
            "conversation": True,
            "authorization": {"action": "authorize_run"},
        }
        with patch.dict(os.environ, {"COMMAND_OBJECTIVE_CONTEXT": str(context_path)}), patch.object(
            manager, "project_root", return_value=self.root
        ), patch.object(manager, "exchange", return_value={"ok": True, "mode": "read-only"}) as check:
            context_path.write_text(json.dumps(context))
            with self.assertRaisesRegex(manager.UpdateError, "active write"):
                manager.lease()
            check.assert_called_once()
            context["directory"] = str(self.upstream)
            context_path.write_text(json.dumps(context))
            with self.assertRaisesRegex(manager.UpdateError, "active write"):
                manager.lease()
            check.assert_called_once()

    def test_no_active_lease_rejects_mcp_call(self):
        data = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "sync", "arguments": {"operation": "pull"}},
        }
        stdout = io.StringIO()
        with patch.dict(os.environ, {"COMMAND_OBJECTIVE_CONTEXT": "/nonexistent"}), patch.object(
            sys, "stdin", io.StringIO(json.dumps(data) + "\n")
        ), patch.object(sys, "stdout", stdout):
            manager.serve()
        self.assertTrue(json.loads(stdout.getvalue())["result"]["isError"])


if __name__ == "__main__":
    unittest.main()
