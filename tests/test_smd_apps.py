"""Check isolated app launch and route generation without starting Docker."""

from contextlib import chdir
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

source = Path(__file__).resolve().parents[1] / "release" / "smd" / "apps.py"
spec = importlib.util.spec_from_file_location("smd_apps", source)
apps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(apps)

PROJECT = "12345678-1234-1234-1234-123456789abc"
REVISION = "a" * 40
IMAGE = "sha256:" + "b" * 64


class AppDeployment(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.checkout = self.directory / "checkouts" / PROJECT / REVISION
        self.checkout.mkdir(parents=True)
        (self.checkout / "Dockerfile").touch()
        self.project = {"id": PROJECT, "revision": REVISION, "name": "Example", "hostname": "app.example.test"}

    def test_builds_pinned_checkout_and_routes_private_container(self):
        expected_labels = json.dumps({apps.LABEL: PROJECT, apps.REVISION: REVISION})
        with patch.object(apps, "docker", side_effect=[
            None, IMAGE, expected_labels, '{"3000/tcp":{}}', None, "", None, "new-container",
        ]) as docker, patch.object(apps, "build_image") as build, chdir(self.directory):
            route = apps.prepare(self.directory, [{**self.project, "verify_installation": True}])
            network_config = json.loads(Path("app-networks.json").read_text())
        build.assert_called_once()
        name = "smd-app-12345678123412341234123456789abc"
        self.assertEqual(network_config["services"]["edge"]["networks"], ["default", name])
        self.assertIn(f"reverse_proxy {name}:3000", route)
        self.assertIn(f'/.well-known/smartdev-installation "{REVISION}" 200', route)
        create_network = docker.call_args_list[5].args
        self.assertIn("--internal", create_network)
        run = docker.call_args_list[7].args
        self.assertIn("no-new-privileges", run)
        self.assertNotIn("--publish", run)
        self.assertEqual(run[-1], IMAGE)

    def test_project_environment_is_private_and_scoped(self):
        path = self.directory / "project-runtime" / f"{PROJECT}.env"
        path.parent.mkdir()
        path.write_text("DATABASE_URL=postgresql://example.test/db?x=y\n")
        with self.assertRaisesRegex(ValueError, "owner-only"):
            apps.project_environment(self.directory, PROJECT)

        path.chmod(0o600)
        self.assertEqual(apps.project_environment(self.directory, PROJECT),
                         (path, {"DATABASE_URL": "postgresql://example.test/db?x=y"}))
        path.write_text("DATABASE_URL=one\nDATABASE_URL=two\n")
        with self.assertRaisesRegex(ValueError, "Invalid project runtime"):
            apps.project_environment(self.directory, PROJECT)

    def test_prepared_image_installs_without_tenant_credentials(self):
        digest = "d" * 64
        project = {**self.project, "tenant_setup": True, "build_context_sha256": digest}
        labels = json.dumps({apps.LABEL: PROJECT, apps.REVISION: REVISION, "io.smartdev.build-context": digest})
        with patch.object(apps, "docker", side_effect=[IMAGE, labels, '{"5000/tcp":{}}', None, "", None]) as docker, chdir(self.directory):
            routes = apps.prepare(self.directory, [project])
        self.assertIn(f'/.well-known/smartdev-installation "{digest}" 200', routes)
        self.assertIn('Tenant configuration is pending." 503', routes)
        self.assertNotIn("reverse_proxy", routes)
        self.assertNotIn("run", [call.args[0] for call in docker.call_args_list])

    def test_requires_project_port_when_image_exposes_multiple(self):
        with patch.object(apps, "docker", return_value='{"80/tcp":{},"443/tcp":{}}'):
            with self.assertRaisesRegex(ValueError, "Declare one"):
                apps.project_port(self.checkout, IMAGE)
        config = self.checkout / ".smartdev" / "deploy.json"
        config.parent.mkdir()
        config.write_text('{"port":4000}')
        self.assertEqual(apps.project_port(self.checkout, IMAGE), 4000)

    def test_missing_dockerfile_blocks_before_any_docker_action(self):
        (self.checkout / "Dockerfile").unlink()
        with patch.object(apps, "docker") as docker:
            with self.assertRaisesRegex(ValueError, "needs a committed Dockerfile"):
                apps.prepare(self.directory, [self.project])
        docker.assert_not_called()


if __name__ == "__main__":
    unittest.main()
