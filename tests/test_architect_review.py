import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))
from fastapi import HTTPException
import architect_review as review


class ArchitectReviewTests(unittest.TestCase):
    def setUp(self):
        self.project, self.execution, self.objective = [UUID(int=value) for value in (1, 2, 3)]
        self.body = review.ArchitectReviewRequest(
            execution_id=self.execution, objective_id=self.objective,
            instruction="Repair the navigation within existing component boundaries.",
            plan={"criterion": "criterion-1", "files": ["client/navigation.tsx"]},
            git_head="a" * 40, branch="codex/task", architecture_context="Layered UI architecture.",
        )

    def database(self, classification="ARCHITECTURE_PRESERVING"):
        db = Mock()
        missing = Mock(); missing.fetchone.return_value = None
        inserted = Mock(); inserted.fetchone.return_value = {
            "id": UUID(int=4), "execution_id": self.execution,
            "model": "qwen2.5:1.5b", "classification": classification,
            "coherence_approved": classification == "ARCHITECTURE_PRESERVING",
            "constraints": ["Preserve boundaries."], "plan_sha256": "b" * 64,
            "reviewed_at": datetime(2026, 11, 28, tzinfo=timezone.utc),
        }
        db.execute.side_effect = [missing, inserted]
        context = Mock(); context.__enter__ = Mock(return_value=db); context.__exit__ = Mock(return_value=False)
        return context

    def service(self, classification="ARCHITECTURE_PRESERVING"):
        response = Mock()
        response.json.return_value = {
            "model": "qwen2.5:1.5b", "classification": classification,
            "coherence_approved": classification == "ARCHITECTURE_PRESERVING",
            "constraints": ["Preserve boundaries."],
        }
        client = Mock(); client.post.return_value = response
        context = Mock(); context.__enter__ = Mock(return_value=client); context.__exit__ = Mock(return_value=False)
        return context

    def test_preserving_review_is_durable_and_bound_to_active_execution(self):
        with (patch.dict(os.environ, {"SMARTDEV_ARCHITECT_URL": "http://architect:8060"}),
              patch.object(review, "active_run") as active,
              patch.object(review, "database", return_value=self.database()),
              patch.object(review.httpx, "Client", return_value=self.service())):
            result = review.review(self.project, self.body)
        self.assertTrue(result["coherence_approved"])
        self.assertEqual(result["classification"], "ARCHITECTURE_PRESERVING")
        active.assert_called_once()

    def test_impacting_review_blocks_before_execution(self):
        with (patch.dict(os.environ, {"SMARTDEV_ARCHITECT_URL": "http://architect:8060"}),
              patch.object(review, "active_run"),
              patch.object(review, "database", return_value=self.database("ARCHITECTURE_IMPACTING")),
              patch.object(review.httpx, "Client", return_value=self.service("ARCHITECTURE_IMPACTING")),
              self.assertRaisesRegex(HTTPException, "ARCHITECTURE_IMPACTING")):
            review.review(self.project, self.body)

    def test_missing_architect_fails_closed(self):
        with (patch.dict(os.environ, {}, clear=True), patch.object(review, "active_run"),
              patch.object(review, "database", return_value=self.database()),
              self.assertRaisesRegex(HTTPException, "not configured")):
            review.review(self.project, self.body)


if __name__ == "__main__":
    unittest.main()
