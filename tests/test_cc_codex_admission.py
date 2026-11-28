from contextlib import contextmanager
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException

import cc_codex


class Database:
    def __init__(self, pending):
        self.pending = pending
        self.calls = 0

    def execute(self, _query, _arguments):
        self.calls += 1
        row = None if self.calls == 1 else self.pending
        result = type("Result", (), {"fetchone": lambda _self: row})
        return result()


class TaskAdmissionTest(TestCase):
    def task_context(self, pending):
        db = Database(pending)

        @contextmanager
        def connection():
            yield db

        with patch.object(cc_codex, "database", connection):
            return cc_codex.task_context(uuid4())

    def test_known_unadmitted_task_reports_conflict(self):
        with self.assertRaises(HTTPException) as raised:
            self.task_context({"delivery": "unconfirmed", "admission": None})
        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("admission is not complete", raised.exception.detail)

    def test_unknown_task_remains_not_found(self):
        with self.assertRaises(HTTPException) as raised:
            self.task_context(None)
        self.assertEqual(raised.exception.status_code, 404)
