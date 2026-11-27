import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import httpx
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))
import codex_access


class CodexCredentialTests(unittest.TestCase):
    project = UUID(int=42)
    key = "not-a-real-key-accepted-by-fixture"

    @patch.object(codex_access, "call", return_value=None)
    def test_missing_project_key_requires_project_configuration(self, vault):
        with self.assertRaisesRegex(HTTPException, "Set OPENAI_API_KEY"):
            codex_access.credential(self.project)
        self.assertEqual(vault.call_args_list[0].args[1], f"data/projects/{self.project}/DEV/OPENAI_API_KEY")

    @patch.object(codex_access, "call")
    def test_removed_vault_key_has_distinct_diagnostic(self, vault):
        vault.side_effect = [None, {"data": {"current_version": 2, "versions": {
            "2": {"deletion_time": "2026-01-01T00:00:00Z"}}}}]
        with self.assertRaisesRegex(HTTPException, "current DEV OpenAI key was removed"):
            codex_access.credential(self.project)

    @patch.object(codex_access.httpx, "Client")
    def test_provider_preflight_only_accepts_2xx(self, client):
        models = client.return_value.__enter__.return_value
        models.get.return_value = httpx.Response(200)
        codex_access.verify_provider_key(self.key)
        for status, text in ((401, "expired, revoked, or invalid"), (302, "unavailable or rejected")):
            models.get.return_value = httpx.Response(status)
            with self.assertRaisesRegex(HTTPException, text) as error:
                codex_access.verify_provider_key(self.key)
            self.assertNotIn(self.key, str(error.exception))
