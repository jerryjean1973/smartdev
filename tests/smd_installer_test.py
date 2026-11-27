"""Exercise the generated installer with a local bundle and simulated Docker."""
import hashlib
import io
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

BUILDER = Path(__file__).resolve().parents[1] / "scripts" / "build-smd-installer.py"
PROJECT = "12345678-1234-1234-1234-123456789abc"
DOCKER = """#!/usr/bin/env python3
import json, os, sys
command = sys.argv[1:]
services = ("api", "portal", "openbao")
image_id = "sha256:" + "9" * 64
if command[-2:] == ["--format", "json"]:
    suffix = "@" + image_id if os.environ["PINNED"] == "yes" else ":latest"
    print(json.dumps({"services": {
        service: {"image": image_id if os.environ["PINNED"] == "local" else service + suffix, "healthcheck": {"test": ["CMD", "true"]}}
        for service in services
    }}))
elif command == ["info"]:
    pass
elif command[-1] == "--images":
    print(image_id)
elif command[:2] == ["image", "load"]:
    assert command[2:] == ["--input", "images.tar.gz"]
elif command[:2] == ["image", "inspect"]:
    assert command[-1] == image_id or command[-1].endswith("@" + image_id)
    print(image_id)
elif command[0] == "inspect":
    print(image_id, os.environ["HEALTH"], os.environ["COMPOSE_PROJECT"], command[-1])
elif command[-2] == "-q":
    print(command[-1])
else:
    assert command[0] == "compose" and (
        command[-1] in ("version", "pull") or "up" in command
    ), command
"""


class InstallerTest(unittest.TestCase):
    def run_installer(self, files, *, checksum=None, health="healthy",
                      compose_project=f"smd-{PROJECT}", pinned="yes", manifest=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "bundle.tar"
            with tarfile.open(bundle, "w") as archive:
                for name, content in files.items():
                    info = tarfile.TarInfo(name)
                    info.size = len(content)
                    archive.addfile(info, io.BytesIO(content))
            installer = root / "install.sh"
            subprocess.run(
                ["python3", BUILDER, "https://release.invalid/bundle.tar",
                 checksum or hashlib.sha256(bundle.read_bytes()).hexdigest(), "test-release", installer],
                check=True, capture_output=True,
            )
            installer.write_text(installer.read_text().replace("/opt/smartdev", str(root / "target")))
            for name, script in {
                "curl": '#!/bin/bash\ncp -- "$BUNDLE" "${@: -1}"\n',
                "docker": DOCKER,
            }.items():
                path = root / name
                path.write_text(script)
                path.chmod(0o700)
            command = ["bash", installer, "--project-id", PROJECT, "--revision", "test-release"]
            if manifest is not None:
                path = root / "manifest.json"
                path.write_text(manifest)
                command.extend(("--project-manifest", str(path)))
            return subprocess.run(
                command,
                env={**os.environ, "PATH": f"{root}:{os.environ['PATH']}",
                     "BUNDLE": str(bundle), "PROJECT": PROJECT, "HEALTH": health,
                     "COMPOSE_PROJECT": compose_project, "PINNED": pinned},
                check=False, capture_output=True, text=True,
            )

    def setUp(self):
        self.files = {
            "compose.yaml": b"services: {}\n",
            "configure.sh": b'test "$1" = "$PROJECT"\n',
            "revision": b"test-release\n",
        }

    def test_healthy_pinned_stack_receipt(self):
        result = self.run_installer(self.files)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "test-release\n")

    def test_loads_bundled_content_addressed_images(self):
        result = self.run_installer({**self.files, "images.tar.gz": b"simulated image archive"}, pinned="local")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "test-release\n")

    def test_project_enrollment_must_succeed_before_receipt(self):
        fields = {**self.files, "enrollment-compose.yaml": b"services: {}\n",
                  "enrollment.py": b'import sys\nassert sys.argv[2].endswith("manifest.json")\nraise SystemExit(19)\n'}
        result = self.run_installer(fields, manifest='{"projects": []}')
        self.assertEqual(result.returncode, 19)
        self.assertEqual(result.stdout, "")

    def test_checksum_failure_has_no_receipt(self):
        result = self.run_installer(self.files, checksum="0" * 64)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("FAILED", result.stderr)

    def test_rejects_archive_traversal(self):
        result = self.run_installer({**self.files, "../outside": b"unexpected"})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("Unexpected release bundle entry", result.stderr)

    def test_rejects_bundle_with_wrong_revision(self):
        result = self.run_installer({**self.files, "revision": b"other-release\n"})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("Release bundle revision does not match", result.stderr)

    def test_unhealthy_stack_has_no_receipt(self):
        result = self.run_installer(self.files, health="unhealthy")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("failed project, image, or health verification", result.stderr)

    def test_rejects_containers_from_another_compose_project(self):
        result = self.run_installer(self.files, compose_project="another-project")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("failed project, image, or health verification", result.stderr)

    def test_rejects_unpinned_service_images(self):
        result = self.run_installer(self.files, pinned="no")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("Unpinned service image", result.stderr)


if __name__ == "__main__":
    unittest.main()
