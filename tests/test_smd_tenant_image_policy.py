import importlib.util
import io
from pathlib import Path
import sys
import tarfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "build-smd-release.py"
spec = importlib.util.spec_from_file_location("smd_release_builder", SOURCE)
builder = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = builder
spec.loader.exec_module(builder)


def flattened(files):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w") as archive:
        for name, contents in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(contents)
            archive.addfile(member, io.BytesIO(contents))
    data.seek(0)
    return tarfile.open(fileobj=data, mode="r:")


class TenantImagePolicyTests(unittest.TestCase):
    def test_tenant_api_rejects_command_center_deployer(self):
        with flattened({"app/smd_deployment.py": b"controller"}) as archive:
            with self.assertRaisesRegex(ValueError, "Command Center deployment files"):
                builder.audit_flattened_image(archive, "API")

    def test_tenant_api_accepts_local_handoff_runtime(self):
        with flattened({
            "app/deployment_portal_receiver.py": b"receiver",
            "app/smd_owner_client.py": b"signed handoff",
            "app/main.py": b"tenant runtime",
        }) as archive:
            builder.audit_flattened_image(archive, "API")

    def test_tenant_portal_rejects_compiled_deployment_endpoint(self):
        with flattened({"app/.next/server/chunk.js": b"fetch('/api/admin/smd-deploy-tasks')"}) as archive:
            with self.assertRaisesRegex(ValueError, "deployment endpoints"):
                builder.audit_flattened_image(archive, "PORTAL")


if __name__ == "__main__":
    unittest.main()
