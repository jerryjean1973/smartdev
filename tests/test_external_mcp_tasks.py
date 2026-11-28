import os
import sys
import unittest
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import Mock, patch
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))
from fastapi import HTTPException
from external_mcp_tasks import ScopedTasks
from external_mcp_transport import TOOLS, dispatch, handle


class ExternalTaskTests(unittest.TestCase):
    def setUp(self):
        self.history = self.enterContext(patch("external_mcp_tasks.history"))
        self.project, self.other, self.task, self.foreign, self.request = map(
            lambda n: str(UUID(int=n)), range(1, 6))
        self.client = ScopedTasks({"project_ids": [self.project], "owner_id": str(UUID(int=6))},
            projects=lambda: [{"id": self.project, "name": "Allowed", "status": "created"},
                              {"id": self.other, "name": "Foreign", "status": "created"}],
            task_navigation=lambda: [{"id": self.task, "project_id": self.project, "title": "Task", "status": "Active"},
                                     {"id": self.foreign, "project_id": self.other, "title": "Foreign", "status": "Active"}])
        intake = self.enterContext(patch("external_mcp_tasks.mcp_intake.receive"))
        intake.side_effect = lambda project,request,kind,prompt,title,task,*args,**kwargs: {
            "task_id":task or self.task,"board_item":task or self.task,
            "admission":"created" if kind=="create" else kind,"delivery":"received"}
        self.history.pending_task.side_effect = LookupError("Task not available")
        self.client._workspace = Mock(return_value={"objective": self.task, "conversationId": self.task,
                                                    "messageId": self.request, "executionId": "receipt"})

    def test_idempotent_request_identity_and_dedicated_conversation(self):
        self.client.send_message(self.task, "Follow up", self.request)
        self.client.send_message(self.task, "Follow up", self.request)
        first, second = self.client._workspace.call_args_list
        self.assertEqual(first, second)
        self.assertEqual(first.kwargs["payload"]["conversationId"], self.task)
        self.assertFalse(first.kwargs["payload"]["waitingReply"])
        self.client.reply_to_waiting_task(self.task, "Yes", self.request)
        self.assertTrue(self.client._workspace.call_args.kwargs["payload"]["waitingReply"])

    def test_create_preserves_full_instructions(self):
        instructions = "# CODEX BUILD — Test\n\n## Objective\n" + "x" * 26000
        result = self.client.create_task(self.project, instructions, self.request)
        payload = self.client._workspace.call_args.kwargs["payload"]
        self.assertEqual(payload["prompt"], instructions)
        self.assertTrue(payload["newTask"])
        self.assertEqual(result["execution_id"], "receipt")

    def test_foreign_project_and_task_never_reach_workspace(self):
        for action, args in [
            (self.client.create_task, (self.other, "Test", self.request)),
            (self.client.send_message, (self.foreign, "Test", self.request)),
            (self.client.reply_to_waiting_task, (self.foreign, "Test", self.request)),
            (self.client.get_latest_response, (self.foreign,)),
        ]:
            with self.assertRaises(LookupError):
                action(*args)
        self.client._workspace.assert_not_called()

    @patch("external_mcp_tasks.database")
    def test_latest_response_uses_exact_task(self, database):
        database.return_value.__enter__.return_value.execute.return_value.fetchall.return_value = []
        self.client._workspace.return_value = {"text": "## Result\n\n- One\n  - Two"}
        result = self.client.get_latest_response(self.task)
        self.assertEqual(result["text"], "## Result\n\n- One\n  - Two")
        self.client._workspace.assert_called_once_with(UUID(self.project), "GET", "task-response",
                                                       params={"task": self.task})

    @patch("external_mcp_tasks.database")
    def test_successful_receipt_sync_preserves_waiting_and_filters_completed(self, database):
        runs = database.return_value.__enter__.return_value.execute.return_value.fetchall
        runs.return_value = [{"objective_id": UUID(self.task), "id": UUID(self.request),
                              "finished_at": datetime(2026, 11, 25, tzinfo=timezone.utc),
                              "exit_code": 0, "head": "a" * 40}]
        self.client._workspace.return_value = {"decisions": [{"objective": self.task, "status": "pending"}]}
        self.assertEqual(self.client.get_task(self.task)["status"], "Awaiting input")
        self.assertEqual(self.client.list_tasks(self.project, status="Completed"), [])
        self.client._workspace.return_value = {"decisions": []}
        [task] = self.client.list_tasks(self.project, status="Completed")
        self.assertEqual(task["execution_receipt"]["execution_id"], self.request)
        self.assertEqual(task["execution_receipt"]["checkout_sha"], "a" * 40)

    @patch("external_mcp_tasks.database")
    def test_execution_history_keeps_failed_runs_and_distinguishes_waiting_task(self, database):
        latest = {"objective_id": UUID(self.task), "id": UUID(self.request),
                  "finished_at": datetime(2026, 11, 25, tzinfo=timezone.utc),
                  "exit_code": 0, "head": "a" * 40}
        failed = {**latest, "id": UUID(int=10), "exit_code": 1}
        runs = database.return_value.__enter__.return_value.execute.return_value.fetchall
        runs.side_effect = [[latest, failed], [latest]]
        self.client._workspace.return_value = {"decisions": [{"objective": self.task, "status": "pending"}]}
        task = self.client.get_task(self.task)
        self.assertEqual(task["status"], "Awaiting input")
        self.assertEqual(task["execution_receipt"]["status"], "Completed")
        self.assertEqual([row["status"] for row in task["execution_history"]], ["Completed", "Failed"])

    def test_write_scope_is_advertised_and_required(self):
        operation = "smartdev_send_message"
        tool = next(tool for tool in TOOLS if tool["name"] == operation)
        self.assertEqual(tool["securitySchemes"][0]["scopes"], ["write"])
        self.assertFalse(tool["annotations"]["readOnlyHint"])
        frame = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": operation}}
        with (patch.dict(os.environ, {"PORTAL_ORIGIN": "https://example.invalid"}),
              patch.dict(sys.modules, {"main": Mock()}),
              patch("external_mcp_transport.require_grant", side_effect=HTTPException(403)) as require):
            with self.assertRaises(HTTPException) as failure:
                handle(frame, "Bearer example")
            require.assert_called_once_with("Bearer example", "write", "https://example.invalid/api/mcp")
            self.assertIn('error="insufficient_scope"', failure.exception.headers["WWW-Authenticate"])
        self.assertIn("error", dispatch(frame, self.client))

    def test_environment_fix_is_explicitly_selected_and_write_scoped(self):
        operation = "smartdev_request_environment_fix"
        tool = next(tool for tool in TOOLS if tool["name"] == operation)
        self.assertEqual(tool["securitySchemes"][0]["scopes"], ["write"])
        self.assertEqual(tool["inputSchema"]["properties"]["environment"]["enum"],
                         ["DEV", "QA", "DEPLOYED"])
        self.assertIn("deployment_id", tool["inputSchema"]["required"])
        self.assertIn("project_id", tool["inputSchema"]["required"])
