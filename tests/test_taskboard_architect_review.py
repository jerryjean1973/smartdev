import unittest
from datetime import datetime, timezone
from pathlib import Path
import sys
from unittest.mock import Mock
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))
from governance import task_architect_review


class TaskboardArchitectReviewTests(unittest.TestCase):
    def test_pending_task_keeps_required_review_on(self):
        db = Mock()
        db.execute.return_value.fetchone.return_value = None
        result = task_architect_review(db, UUID(int=1), UUID(int=2))
        self.assertEqual(result, {
            "required": True, "reviewed": False, "review_id": None, "execution_id": None,
            "model": None, "classification": None, "approved": None, "reviewed_at": None,
        })

    def test_completed_review_exposes_durable_receipt(self):
        reviewed_at = datetime(2026, 11, 28, tzinfo=timezone.utc)
        db = Mock()
        db.execute.return_value.fetchone.return_value = {
            "id": UUID(int=3), "execution_id": UUID(int=4), "model": "qwen2.5:1.5b",
            "classification": "ARCHITECTURE_PRESERVING", "coherence_approved": True,
            "reviewed_at": reviewed_at,
        }
        result = task_architect_review(db, UUID(int=1), UUID(int=2))
        self.assertTrue(result["required"])
        self.assertTrue(result["reviewed"])
        self.assertTrue(result["approved"])
        self.assertEqual(result["review_id"], UUID(int=3))


if __name__ == "__main__":
    unittest.main()
