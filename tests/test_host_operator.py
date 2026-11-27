import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import Mock

spec = importlib.util.spec_from_file_location(
    "host_operator", Path(__file__).resolve().parents[1] / "operator" / "host_operator.py"
)
operator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(operator)
ID = "a" * 64


def container(project=operator.PROJECT):
    return json.dumps([{
        "Id": ID, "Name": f"/{project}-portal-1", "Image": "sha256:" + ID,
        "State": {"Status": "running"},
        "Config": {"Labels": {
            "com.docker.compose.project": project,
            "com.docker.compose.service": "portal",
        }, "Env": ["PRIVATE=fixture-only"]},
    }])


class OperatorTests(unittest.TestCase):
    def test_inspect_projects_only_public_fields(self):
        run = Mock(return_value=container())
        result = operator.execute({"operation": "container.inspect", "service": "portal"}, {"portal"}, run)
        self.assertEqual(result["state"], "running")
        self.assertNotIn("fixture-only", json.dumps(result))

    def test_restart_pins_verified_id(self):
        run = Mock(side_effect=[container(), "", container()])
        operator.execute({"operation": "service.restart", "service": "portal"}, {"portal"}, run)
        self.assertEqual(run.call_args_list[1].args, ("container", "restart", "--timeout", "15", ID))
        self.assertEqual(run.call_args_list[2].args, ("container", "inspect", ID))

    def test_foreign_project_never_restarts(self):
        run = Mock(return_value=container("bcentrisk-full-customer"))
        with self.assertRaises(ValueError):
            operator.execute({"operation": "service.restart", "service": "portal"}, {"portal"}, run)
        self.assertEqual(run.call_count, 1)

    def test_unenrolled_and_unrecognized_actions_never_reach_docker(self):
        run = Mock()
        for operation, service in (("service.restart", "worker"), ("shell", "portal")):
            with self.subTest(operation=operation, service=service), self.assertRaises(ValueError):
                operator.execute({"operation": operation, "service": service}, {"portal"}, run)
        run.assert_not_called()

    def test_invalid_frame_returns_sanitized_receipt(self):
        stream = io.BytesIO(b'{"operation":"private input"}\n')
        operator.handle(stream, {"portal"})
        receipt = json.loads(stream.getvalue().splitlines()[1])
        self.assertEqual(receipt["status"], "denied")
        self.assertNotIn("private input", json.dumps(receipt))
