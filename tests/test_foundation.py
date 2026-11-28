import importlib.machinery
import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from argparse import Namespace
from unittest.mock import patch
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(ROOT / "workspace"))
from core import REPOSITORY, issue_session, password_digest, project_token, valid_session, workspace_name

loader = importlib.machinery.SourceFileLoader("codex_task", str(ROOT / "workspace" / "codex-task"))
spec = importlib.util.spec_from_loader(loader.name, loader)
wrapper = importlib.util.module_from_spec(spec)
loader.exec_module(wrapper)


class FoundationTests(unittest.TestCase):
    def setUp(self):
        self.architect_original = wrapper.architect_review
        architect = patch.object(wrapper, "architect_review", return_value={
            "review_id": str(UUID(int=99)), "classification": "ARCHITECTURE_PRESERVING",
            "coherence_approved": True, "plan_sha256": "a" * 64, "constraints": ["preserve"],
        })
        self.architect = architect.start()
        self.addCleanup(architect.stop)

    def test_architect_review_sends_only_execution_scope_not_agent_metadata(self):
        result = {"review_id": str(UUID(int=99)), "classification": "ARCHITECTURE_PRESERVING",
                  "coherence_approved": True, "plan_sha256": "a" * 64}
        with patch.object(wrapper, "exchange", return_value=result) as exchange:
            self.architect_original({"execution_id": str(UUID(int=1)),
                                     "objective_id": str(UUID(int=2)), "agent": "codex_cli"},
                                    "Inspect only", {"kind": "objective"}, "a" * 40,
                                    "codex/task", "architecture")
        payload = exchange.call_args.args[0]
        self.assertNotIn("agent", payload)
        self.assertEqual(exchange.call_args.args[1], "/architect-review")

    def test_session_signature_and_expiration(self):
        key = b"x" * 32
        session = issue_session(key, 100)
        self.assertTrue(valid_session(session, key, 101))
        self.assertFalse(valid_session(session, key, 28900))
        self.assertFalse(valid_session(session + "x", key, 101))
        self.assertFalse(valid_session(session, b"y" * 32, 101))

    def test_password_digest_uses_salt(self):
        self.assertNotEqual(password_digest("sample", b"a" * 16), password_digest("sample", b"b" * 16))

    def test_repository_scope(self):
        self.assertTrue(REPOSITORY.fullmatch("owner/app.test"))
        for value in ("https://github.com/owner/repo", "owner/../repo", "owner/repo;cmd", "owner/-option"):
            with self.subTest(value=value):
                self.assertIsNone(REPOSITORY.fullmatch(value))

    def test_workspace_identity_and_memory_token(self):
        a, b = UUID(int=1), UUID(int=2)
        key = b"x" * 32
        self.assertEqual(workspace_name(a), "p-aaaaaaaaaaaaaaaaaaaaaaaaae")
        self.assertEqual(project_token(key, a), project_token(key, a))
        self.assertNotEqual(project_token(key, a), project_token(key, b))

    def test_coding_preflight_selects_environment_and_returns_inventory(self):
        receipt = {"id": str(UUID(int=9)), "sha256": "a" * 64}
        response = {"agent": "codex_cli", "bearer": "leased", "access_inventory": {
            "actual_executor": "codex_cli", "receipt": receipt,
        }}
        with (patch.object(wrapper, "memory_settings", return_value={
                  "url": "https://cc.qa.example/api/workspace-memory/project", "environment": "QA",
              }), patch.object(wrapper, "exchange", return_value=response) as exchange):
            result = wrapper.codex_preflight({"execution_id": str(UUID(int=1)), "agent": "codex_cli"})
        self.assertEqual(exchange.call_args.args[0]["environment"], "QA")
        self.assertEqual(result["access_inventory"]["receipt"], receipt)

    def test_coding_preflight_refuses_missing_environment(self):
        with (patch.object(wrapper, "memory_settings", return_value={"url": "https://cc.example/api"}),
              patch.object(wrapper, "exchange") as exchange,
              self.assertRaisesRegex(RuntimeError, "Explicit SMD environment selection")):
            wrapper.codex_preflight({"execution_id": str(UUID(int=1)), "agent": "codex_cli"})
        exchange.assert_not_called()

    def test_main_instruction_uses_write_lease_and_never_creates_objective(self):
        args = Namespace(conversation=UUID(int=1), conversation_execution=UUID(int=2),
                         terms="sidebar greeting", include_shared=False,
                         task="Add hello $username in the left sidebar under the logo.",
                         file=[], image=[], json=False, reply_question="Which sidebar should I modify?")
        calls = []
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            sidebar = directory / "Sidebar.tsx"
            sidebar.write_text("logo")
            def exchange(payload, suffix=""):
                calls.append((payload, suffix))
                if suffix == "/mcp-bindings":
                    return {"bindings": []}
                return {"mode":"workspace-write"} if payload.get("action")=="start_run" else {"notes": []}

            def invoke(command, cwd, environment, prompt, lease):
                self.assertIn(args.task, prompt)
                self.assertIn(args.reply_question, prompt)
                self.assertIn("No task or task selection is required", prompt)
                self.assertIn("slm_architect_review", prompt)
                self.assertEqual(command, ["codex", "--sandbox", "workspace-write"])
                self.assertNotIn("objective_id", lease)
                sidebar.write_text("logo\nhello username")
                return 0

            with (
                patch.object(wrapper, "exchange", side_effect=exchange),
                patch.object(wrapper, "git", side_effect=["main", "a"*40, "a"*40]),
                patch.object(wrapper, "dirty_files", side_effect=[{}, {"Sidebar.tsx":"new"}]),
                patch.object(wrapper, "checkpoint", return_value={}) as checkpoint,
                patch.object(wrapper, "codex_preflight", return_value=("https://proxy.example","key")),
                patch.object(wrapper, "cli", side_effect=lambda args, mode, *rest: (["codex","--sandbox",mode],{})),
                patch.object(wrapper, "invoke", side_effect=invoke),
            ):
                self.assertEqual(wrapper.run_conversation(args,directory),0)
            self.assertIn("hello username", sidebar.read_text())
            self.assertIsNone(checkpoint.call_args.kwargs["objective"])
        self.assertEqual([payload.get("action") for payload, suffix in calls if suffix=="/conversation"],["start_run","finish_run"])
        self.assertFalse(any(suffix=="/governance" for _,suffix in calls))
        self.assertTrue(any(payload.get("changed_files")==["Sidebar.tsx"] for payload,_ in calls))
        self.architect.assert_called()

    def test_main_fails_closed_before_model_auth_when_architect_is_unavailable(self):
        args = Namespace(conversation=UUID(int=1), conversation_execution=UUID(int=2),
                         terms="review", include_shared=False, task="Change navigation.",
                         file=[], image=[], json=False)
        self.architect.side_effect = RuntimeError("SLM Architect unavailable")
        with (
            TemporaryDirectory() as temporary,
            patch.object(wrapper, "git", side_effect=["main", "a" * 40]),
            patch.object(wrapper, "dirty_files", return_value={}),
            patch.object(wrapper, "exchange", side_effect=[{"mode": "workspace-write"}, {"ok": True}]) as exchange,
            patch.object(wrapper, "codex_preflight") as preflight,
        ):
            self.assertEqual(wrapper.run_conversation(args, Path(temporary)), 77)
        preflight.assert_not_called()
        self.assertEqual(exchange.call_args.args[0]["action"], "finish_run")
        self.assertEqual(exchange.call_args.args[0]["exit_code"], 77)

    def test_main_runner_refuses_api_without_workspace_write_lease(self):
        args = Namespace(conversation=UUID(int=1), conversation_execution=UUID(int=2))
        with (
            patch.object(wrapper, "git", side_effect=["main", "a"*40]),
            patch.object(wrapper, "exchange", side_effect=[{"ok":True}, {"ok":True}]) as exchange,
            patch.object(wrapper, "codex_preflight") as preflight,
            self.assertRaisesRegex(SystemExit, "matching SmartDev API release"),
        ):
            wrapper.run_conversation(args, Path("/project/repository"))
        self.assertEqual(exchange.call_args_list[0].args[0]["mode"],"workspace-write")
        self.assertEqual(exchange.call_args_list[1].args[0]["action"],"finish_run")
        preflight.assert_not_called()

    def test_main_records_the_same_exit_code_when_model_auth_is_missing(self):
        args = Namespace(conversation=UUID(int=1), conversation_execution=UUID(int=2),
                         terms="", include_shared=False, task="Continue the current task.",
                         file=[], image=[], json=False)
        with (
            patch.object(wrapper, "git", side_effect=["main", "a" * 40]),
            patch.object(wrapper, "dirty_files", return_value={}),
            patch.object(wrapper, "exchange", side_effect=[{"mode": "workspace-write"}, {"ok": True}]) as exchange,
            patch.object(wrapper, "codex_preflight", side_effect=RuntimeError("DEV credential missing")),
            patch.object(wrapper, "invoke") as invoke,
        ):
            self.assertEqual(wrapper.run_conversation(args, Path("/project/repository")), 78)
        self.assertEqual(exchange.call_args.args[0]["action"], "finish_run")
        self.assertEqual(exchange.call_args.args[0]["exit_code"], 78)
        invoke.assert_not_called()

    def test_main_reply_reopens_original_lease_and_uses_exact_thread(self):
        execution = UUID(int=2)
        thread = str(UUID(int=3))
        args = Namespace(conversation=UUID(int=1), conversation_execution=execution, strict_resume=True,
                         resume=thread, reply_question="Can you configure DATABASE_URL?",
                         terms="flat JSON", include_shared=False, task="no DB these are just flat file json",
                         file=[], image=[], json=False)
        payloads = []

        def exchange(payload, suffix=""):
            payloads.append((payload,suffix))
            if suffix == "/mcp-bindings":
                return {"bindings": []}
            return {"mode":"workspace-write","notes":[]}

        with (
            patch.object(wrapper,"git",side_effect=["main","a"*40,"a"*40]),
            patch.object(wrapper,"dirty_files",side_effect=[{},{}]),
            patch.object(wrapper,"exchange",side_effect=exchange),
            patch.object(wrapper,"codex_preflight",return_value=("https://proxy.example","key")),
            patch.object(wrapper,"invoke",return_value=0) as invoke,
        ):
            self.assertEqual(wrapper.run_conversation(args,Path("/project/repository")),0)
        self.assertEqual(payloads[0][0]["action"],"resume_run")
        self.assertEqual(payloads[0][0]["execution_id"],str(execution))
        self.assertEqual(payloads[0][0]["thread_id"],thread)
        self.assertEqual(payloads[1][1],"/mcp-bindings")
        self.assertEqual(payloads[1][0]["execution_id"],str(execution))
        self.assertNotEqual(payloads[2][0]["execution_id"],str(execution))
        command=invoke.call_args.args[0]
        self.assertEqual(command[-4:],["resume","--",thread,"-"])
        self.assertIn("previous_question",invoke.call_args.args[3])
        self.assertNotIn("objective_id",invoke.call_args.args[4])

    def test_resume_loads_notes_and_records_exit_without_prompt(self):
        args = Namespace(terms="release rules", include_shared=False, resume="session-id", objective=None, criterion=None,
                         agent="codex", new_objective=False, image=[], file=[], json=False,
                         task="continue task")
        environment = {"COMMAND_MEMORY_TOKEN": "secret"}
        response = {
            "decision": "execute", "criterion": "criterion-1",
            "contract": {"id": "objective-id", "original_request": "original task",
                         "expected_outcome": "A verified outcome", "criteria": [{"id": "criterion-1"}], "scope": [],
                         "constraints": [], "dependencies": [], "version": 1},
            "progress": {"completedCriteria": [], "outstandingCriteria": ["criterion-1"],
                         "blockedCriteria": [], "verifiedEvidence": [], "blockers": [], "nextAction": "review"},
        }
        with (
            patch.dict(os.environ, environment),
            patch.object(wrapper, "git", side_effect=["main", "a" * 40, "b" * 40, "main"]),
            patch.object(wrapper, "dirty_files", return_value={}),
            patch.object(wrapper, "checkpoint"),
            patch.object(wrapper, "git_paths", return_value=[]),
            patch.object(wrapper, "exchange", side_effect=[{"notes": ["previous release decision"]}, {"bindings": []}, {"ok": True}]) as exchange,
            patch.object(wrapper, "coordination", side_effect=[{"criteria": [], "version": 1}, response, {"status": "RUNNING", "nextAction": None}]),
            patch.object(wrapper, "codex_preflight", return_value=("https://project.example/v1", "leased-test-bearer")),
            patch.object(wrapper, "invoke", return_value=0) as invoke,
        ):
            self.assertEqual(wrapper.run(args, Path("/project/repository"), Path("/project")), 0)
        command, _, child_env, prompt, _ = invoke.call_args.args
        self.assertEqual(command[-4:], ["resume", "--", "session-id", "-"])
        self.assertIn("previous release decision", prompt)
        self.assertIn("original task", prompt)
        self.assertNotIn("COMMAND_MEMORY_TOKEN", child_env)
        result = exchange.call_args.args[0]
        self.assertEqual(result["head"], "b" * 40)
        self.assertEqual(result["exit_code"], 0)
        self.assertFalse(result["include_shared"])
        self.assertNotIn("continue task", str(result))

    def test_claude_code_uses_managed_gateway_without_raw_provider_keys(self):
        args = Namespace(resume=None, strict_resume=False, resume_agent="claude_code", image=[])
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY":"must-not-leak", "CLAUDE_CODE_OAUTH_TOKEN":"must-not-leak"}):
            command, environment = wrapper.cli(args, "workspace-write", {
                "agent":"claude_code", "url":"https://project.example/claude", "bearer":"leased-bearer",
            })
        self.assertEqual(command[:3], ["claude-task-adapter","--mode","workspace-write"])
        self.assertIn("--session-id", command)
        self.assertEqual(environment["ANTHROPIC_BASE_URL"], "https://project.example/claude")
        self.assertEqual(environment["ANTHROPIC_AUTH_TOKEN"], "leased-bearer")
        self.assertNotIn("ANTHROPIC_API_KEY", environment)
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", environment)

    def test_claude_mcp_uses_environment_capability_not_command_line_secret(self):
        command=["claude-task-adapter","--mode","workspace-write"]
        environment={}
        with patch.object(wrapper,"memory_settings",return_value={"url":"https://project.example/api"}):
            wrapper.apply_project_mcp(command,environment,{"bearer":"mcp-secret","bindings":[{"name":"bripe","ready":True}]},"claude_code")
        self.assertEqual(environment["SMARTDEV_MCP_LEASE"],"mcp-secret")
        config=json.loads(command[-1])
        self.assertEqual(config["mcpServers"]["bripe"]["headers"]["Authorization"],"Bearer ${SMARTDEV_MCP_LEASE}")
        self.assertNotIn("mcp-secret"," ".join(command))



if __name__ == "__main__":
    unittest.main()
