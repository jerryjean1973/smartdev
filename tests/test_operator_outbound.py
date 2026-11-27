import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock
from uuid import uuid4

spec = importlib.util.spec_from_file_location(
    "operator_outbound", Path(__file__).resolve().parents[1] / "operator" / "outbound.py"
)
outbound = importlib.util.module_from_spec(spec)
spec.loader.exec_module(outbound)


class OutboundTests(unittest.TestCase):
    def test_claim_returns_correlated_receipt(self):
        claim = {"request_id": str(uuid4()), "claim_id": str(uuid4())}
        post = Mock(side_effect=[claim, {"ok": True}])
        receipt = {"id": str(uuid4()), "status": "observed", "result": {"service": "portal"}}
        outbound.cycle(post, Mock(return_value=receipt))
        post.assert_called_with("result", {**claim, "receipt_id": receipt["id"],
                                           "status": "observed", "result": receipt["result"]})

    def test_no_work_and_invalid_claim_do_not_invoke_operator(self):
        for claim in ({}, {"request_id": "invalid"}):
            observe = Mock()
            if claim:
                with self.assertRaises(ValueError):
                    outbound.cycle(Mock(return_value=claim), observe)
            else:
                outbound.cycle(Mock(return_value=claim), observe)
            observe.assert_not_called()

    def test_only_https_origin_accepted(self):
        for invalid in ("http://smartdev.example", "https://user@smartdev.example",
                        "https://smartdev.example/other", "https://smartdev.example?x=1"):
            with self.assertRaises(ValueError):
                outbound.endpoint(invalid)
        self.assertEqual(outbound.endpoint("https://smartdev.example/"),
                         "https://smartdev.example/api/operator-machine")
