import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))
from inference import ProviderFailure, run_model


class InferenceTests(unittest.TestCase):
    def transport(self, replies):
        request = Mock(side_effect=replies)
        cm = Mock()
        cm.__enter__ = Mock(return_value=SimpleNamespace(post=request))
        cm.__exit__ = Mock(return_value=False)
        transport = SimpleNamespace(Client=Mock(return_value=cm), Timeout=Mock(),
                                    ConnectError=ConnectionError, ConnectTimeout=TimeoutError, RequestError=OSError)
        return transport, request

    def test_openai_inference_and_accounting(self):
        payload = {"output": [{"type": "message", "content": [{"type": "output_text", "text": "Hello"}]}],
                   "usage": {"input_tokens": 8, "output_tokens": 2}}
        transport, request = self.transport([Mock(status_code=200, **{"json.return_value": payload})])
        output, usage = run_model("openai", "model-from-discovery", "private-key-placeholder",
                                  "Greet", "Hello", transport=transport)
        self.assertEqual((output, usage), ("Hello", payload["usage"]))
        args, kwargs = request.call_args
        self.assertEqual(args[0], "https://api.openai.com/v1/responses")
        self.assertFalse(kwargs["json"]["store"])
        self.assertFalse(transport.Client.call_args.kwargs["follow_redirects"])

    def test_claude_bounded_rate_limit_retry(self):
        payload = {"content": [{"type": "text", "text": "Hello"}], "usage": {"input_tokens": 8, "output_tokens": 2}}
        transport, request = self.transport([Mock(status_code=429), Mock(status_code=200, **{"json.return_value": payload})])
        with patch("inference.time.sleep"):
            run_model("anthropic", "model-from-discovery", "private-key-placeholder", "Greet", "Hello", transport=transport)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args.args[0], "https://api.anthropic.com/v1/messages")

    def test_ambiguous_request_is_not_retried_and_secrets_are_redacted(self):
        transport, request = self.transport([OSError("private-credential")])
        with self.assertRaises(ProviderFailure) as failure:
            run_model("openai", "model", "private-credential", "Greet", "Hello", transport=transport)
        self.assertTrue(failure.exception.uncertain)
        self.assertNotIn("private-credential", str(failure.exception))
        self.assertEqual(request.call_count, 1)

    def test_credential_reflection_is_rejected(self):
        reply = Mock(status_code=200, **{"json.return_value": {"content": [{"type": "text", "text": "private-credential"}]}})
        transport, _ = self.transport([reply])
        with self.assertRaisesRegex(ProviderFailure, "no usable text"):
            run_model("anthropic", "model", "private-credential", "Greet", "Hello", transport=transport)
