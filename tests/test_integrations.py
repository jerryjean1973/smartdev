from io import BytesIO
from pathlib import Path
import json
import sys
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))
from integration_registry import NoRedirect, PROVIDERS, probe


class ProviderContractTests(unittest.TestCase):
    def call(self, name, payload, credential="test-private-credential"):
        with patch("integration_registry.build_opener") as build:
            opened = build.return_value.open
            opened.return_value.__enter__.return_value.read.return_value = json.dumps(payload).encode()
            result = probe(name, credential)
            return result, opened.call_args.args[0]

    def test_static_https_registry_and_guides(self):
        for name, provider in PROVIDERS.items():
            with self.subTest(name=name):
                self.assertEqual(urlsplit(provider.documentation).scheme, "https")
                self.assertTrue(provider.steps and provider.permission and provider.verification)
                if provider.endpoint:
                    self.assertEqual(urlsplit(provider.endpoint).scheme, "https")
                    self.assertTrue(provider.capability)

    def test_openai_models_and_secret_exclusion(self):
        result, request = self.call("openai", {"data": [{"id": "model"}, {"id": "test-private-credential"}]})
        self.assertEqual(result.status, "OPERATIONAL")
        self.assertEqual(result.items, ("model",))
        self.assertEqual(request.get_header("Authorization"), "Bearer test-private-credential")
        self.assertNotIn("test-private-credential", repr(result))
        self.assertEqual(request.get_method(), "GET")

    def test_anthropic_headers_and_model_discovery(self):
        result, request = self.call("anthropic", {"data": [{"id": "claude-model"}]})
        self.assertEqual(result.items, ("claude-model",))
        self.assertEqual(request.get_header("X-api-key"), "test-private-credential")
        self.assertEqual(request.get_header("Anthropic-version"), "2023-06-01")

    def test_gitlab_and_cloudflare_response_contracts(self):
        result, _ = self.call("gitlab", {"username": "developer"})
        self.assertEqual(result.items, ("developer",))
        result, _ = self.call("cloudflare", {"success": True, "result": [{"name": "example.com"}]})
        self.assertEqual(result.items, ("example.com",))
        rejected, _ = self.call("cloudflare", {"success": False, "result": []})
        self.assertEqual(rejected.status, "FAILED")

    def test_provider_error_states_and_redaction(self):
        outcomes = {401: "AUTHORIZATION_REQUIRED", 403: "CONFIGURATION_REQUIRED",
                    429: "DEGRADED", 503: "DEGRADED", 302: "FAILED"}
        for code, expected in outcomes.items():
            with self.subTest(code=code), patch("integration_registry.build_opener") as build:
                build.return_value.open.side_effect = HTTPError(
                    "https://api.openai.com", code, "private message", {}, BytesIO(b"secret body"))
                result = probe("openai", "test-private-credential")
                self.assertEqual(result.status, expected)
                self.assertNotIn("private message", repr(result))
        with patch("integration_registry.build_opener") as build:
            build.return_value.open.side_effect = URLError("private details")
            self.assertEqual(probe("openai", "test-private-credential").status, "DEGRADED")

    def test_redirect_handler_refuses_to_forward_credentials(self):
        self.assertIsNone(NoRedirect().redirect_request(MagicMock(), None, 302, "", {}, "https://other.example"))


if __name__ == "__main__":
    unittest.main()
