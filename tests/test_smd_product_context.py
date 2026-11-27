"""Exercise the publisher and installer with a real pinned Git distribution."""
from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


publisher = module("publisher", "scripts/stage-smd-product-context.py")
apps = module("apps", "release/smd/apps.py")


class PreparedContext(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.wrapper, self.context, self.store = (root / n for n in ("wrapper", "context", "store"))
        self.wrapper.mkdir()
        self.context.mkdir()
        self.project_id = uuid4()
        self.canonical = "abcde12345" * 4
        self.git("init", "-q")
        distribution = self.wrapper / "product-distribution"
        distribution.mkdir()
        (distribution / "distribution.json").write_text(json.dumps(
            {"source_revision": self.canonical, "product": "vendorgate"}))
        self.git("add", "product-distribution/distribution.json")
        self.git("update-index", "--add", "--cacheinfo",
                 f"160000,{self.canonical},product-distribution/canonical-source")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "commit", "-qm", "fixture")
        self.revision = self.git("rev-parse", "HEAD")
        recipe = b"FROM scratch\n"
        (self.context / "Dockerfile.products").write_bytes(recipe)
        self.manifest = {"schema_version": "product-package.v1", "product": "vendorgate", "engine": "embedded",
                         "source_revision": self.canonical, "files": {"Dockerfile.products": hashlib.sha256(recipe).hexdigest()}}
        self.seal = self.context / "product-package.json"
        self.seal.write_text(json.dumps(self.manifest))

    def git(self, *args):
        return subprocess.check_output(["git", "-C", self.wrapper, *args], text=True).strip()

    def publish(self):
        with redirect_stdout(io.StringIO()):
            publisher.publish(self.wrapper, self.context, self.project_id, self.store)
        directory = self.store / str(self.project_id)
        digest = (directory / (self.revision + ".sha256")).read_text().strip()
        return {"id": str(self.project_id), "revision": self.revision, "build_context_sha256": digest}, directory / (digest + ".tar.gz")

    def test_pinned_context_survives_transfer_and_selects_product_recipe(self):
        project, archive = self.publish()
        self.assertEqual(self.publish(), (project, archive))
        with patch.object(apps, "docker") as build:
            apps.build_product_image(self.wrapper, project, archive, "fixture:tested")
        args = build.call_args.args
        self.assertEqual(args[0], "build")
        self.assertEqual(args[args.index("--file") + 1].name, "Dockerfile.products")
        self.assertIn("PRODUCT=vendorgate", args)
        self.assertIn("ENGINE_LOCATION=embedded", args)
        self.assertIn(f"BUILD_ID={self.revision}", args)
        with patch.object(apps, "docker") as build, self.assertRaisesRegex(ValueError, "digest"):
            apps.build_product_image(self.wrapper, {**project, "build_context_sha256": "0" * 64}, archive, "unused")
        build.assert_not_called()

    def test_rejects_wrong_canonical_commit_and_changed_file(self):
        self.seal.write_text(json.dumps({**self.manifest, "source_revision": "0" * 40}))
        with self.assertRaisesRegex(ValueError, "pin differ"):
            self.publish()
        self.seal.write_text(json.dumps(self.manifest))
        (self.context / "Dockerfile.products").write_text("changed")
        with self.assertRaisesRegex(ValueError, "differ from its seal"):
            self.publish()
        self.assertFalse(list(self.store.rglob("*.sha256")))
