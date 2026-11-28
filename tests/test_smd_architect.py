import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / "release" / "smd" / "architect.py"
spec = importlib.util.spec_from_file_location("smd_architect", SOURCE)
architect = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = architect
spec.loader.exec_module(architect)


class SmdArchitectTests(unittest.TestCase):
    def review(self, task, plan):
        body = architect.PlannedTask(task=task, plan=plan,
                                     architecture_context="Tenant isolation is canonical.")
        with patch.object(architect, "model_request",
                          return_value={"response": "ARCHITECTURE_PRESERVING"}):
            return architect.review(body)

    def test_slm_cannot_downgrade_explicit_canonical_change(self):
        result = self.review("Replace tenant isolation and evidence authority.",
                             {"scope": "platform"})
        self.assertEqual(result["classification"], "CANONICAL_CHANGE")
        self.assertFalse(result["coherence_approved"])

    def test_preserving_read_only_plan_remains_approved(self):
        result = self.review("Report the current branch and commit without edits.",
                             {"kind": "read-only verification"})
        self.assertEqual(result["classification"], "ARCHITECTURE_PRESERVING")
        self.assertTrue(result["coherence_approved"])

    def test_smd_release_cannot_omit_architect_model(self):
        builder = SOURCE.parents[2] / "scripts" / "build-smd-release.py"
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, str(builder), "test-revision", str(Path(directory) / "release.tar")],
                text=True, capture_output=True, timeout=10,
            )
        self.assertEqual(result.returncode, 2)
        self.assertIn("--model-store", result.stderr)

    def test_smd_release_defaults_include_architect_taskboard(self):
        builder = (SOURCE.parents[2] / "scripts" / "build-smd-release.py").read_text()
        self.assertIn('"API": "smartdev-api:architect-taskboard-', builder)
        self.assertIn('"PORTAL": "smartdev-portal:architect-column-', builder)


if __name__ == "__main__":
    unittest.main()
