"""Forward scoped MCP operations to the existing private Coder chat app."""
import os
import re
from urllib.parse import urlsplit
from uuid import UUID, uuid5

import httpx

from core import workspace_name
from external_mcp_read import ScopedTaskReads
from store import database
from task_titles import valid_title


class WorkspaceUnavailable(Exception):
    pass


class ScopedTasks(ScopedTaskReads):
    def __init__(self, grant, **sources):
        super().__init__(grant["project_ids"], **sources)
        self.grant_id = grant.get("id")
        self.owner_id = UUID(str(grant["owner_id"]))

    def inspect_dev_portal(self, project_id):
        from operator_dispatch import inspect_portal

        project = self._require_project(project_id)
        return inspect_portal(project, self.grant_id)

    @staticmethod
    def _receipt(run):
        code = run["exit_code"]
        return {
            "execution_id": str(run["id"]),
            "finished_at": run["finished_at"].isoformat(),
            "status": "Completed" if code == 0 else "Paused" if code == 130 else "Failed",
            "exit_code": code,
            "checkout_sha": run["head"],
        }

    def _task_details(self, task):
        project = UUID(task["project_id"])
        with database() as db:
            runs = db.execute("""
                SELECT id, finished_at, exit_code, head
                FROM objective_run
                WHERE project_id=%s AND objective_id=%s
                  AND finished_at IS NOT NULL AND exit_code IS NOT NULL
                ORDER BY started_at DESC,id DESC LIMIT 30
            """, (project, UUID(task["task_id"]))).fetchall()
        [task] = self._execution_status(project, [task])
        task["execution_history"] = [self._receipt(run) for run in runs]
        if task["execution_history"]:
            task.setdefault("execution_receipt", task["execution_history"][0])
        return task

    def _execution_status(self, project_id, tasks):
        candidates = [UUID(task["task_id"]) for task in tasks if task["status"] == "Active"]
        if not candidates:
            return tasks
        with database() as db:
            runs = db.execute("""
                SELECT DISTINCT ON (r.objective_id) r.objective_id, r.id,
                    r.finished_at, r.exit_code, r.head
                FROM objective_run r
                JOIN objective_contract task ON task.id=r.objective_id
                    AND task.project_id=r.project_id AND task.version=r.version
                WHERE r.project_id=%s AND r.objective_id=ANY(%s)
                  AND NOT task.criteria @> '[{"mandatory":true}]'::jsonb
                ORDER BY r.objective_id,r.started_at DESC,r.id DESC
            """, (project_id, candidates)).fetchall()
        completed = {str(run["objective_id"]): run for run in runs
                     if run["finished_at"] and run["exit_code"] == 0}
        if not completed:
            return tasks
        try:
            workflow = self._workspace(project_id, "GET", "workflow")
        except WorkspaceUnavailable:
            return tasks
        if not isinstance(workflow.get("decisions"), list):
            return tasks
        waiting = {row.get("objective") for row in workflow["decisions"]
                   if row.get("status") in ("pending", "answering")}
        for task in tasks:
            run = completed.get(task["task_id"])
            if not run:
                continue
            task["status"] = "Awaiting input" if task["task_id"] in waiting else "Completed"
            task["execution_receipt"] = self._receipt(run)
        return tasks

    def list_tasks(self, project_id, status=None):
        project = self._require_project(project_id)
        tasks = self._execution_status(project, super().list_tasks(project))
        return [task for task in tasks if status is None or task["status"] == status]

    def get_task(self, task_id):
        return self._task_details(super().get_task(task_id))

    def _workspace(self, project_id, method, endpoint, *, params=None, payload=None):
        project = next((row for row in self.projects() if UUID(str(row["id"])) == project_id
                        and not row.get("archived")), None)
        address = urlsplit((project or {}).get("workspace_url") or "")
        public = urlsplit(os.environ["CODER_PUBLIC_URL"])
        path = address.path.rstrip("/")
        expected = r"/@[A-Za-z0-9_-]+/" + re.escape(workspace_name(project_id))
        if ((address.scheme, address.netloc) != (public.scheme, public.netloc)
                or address.query or address.fragment or not re.fullmatch(expected, path)):
            raise WorkspaceUnavailable("The project workspace is unavailable.")
        try:
            with httpx.Client(base_url=os.environ["CODER_API_URL"], timeout=25,
                              trust_env=False, follow_redirects=False) as client:
                response = client.request(method, path + "/apps/codex-chat/" + endpoint,
                                          params=params, json=payload,
                                          headers={"Coder-Session-Token": os.environ["CODER_TOKEN"],
                                                   "Sec-Fetch-Site": "same-origin"})
            if response.status_code in (409, 422):
                detail = response.json().get("error")
                raise WorkspaceUnavailable(detail if isinstance(detail, str) else "The task could not accept this request.")
            if response.status_code not in (200, 202):
                raise WorkspaceUnavailable("The workspace could not complete this request.")
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError("Invalid workspace response")
            return result
        except (httpx.HTTPError, ValueError, KeyError):
            raise WorkspaceUnavailable("The workspace could not confirm the request. Retry with the same request ID.") from None

    def _submit(self, project_id, client_request_id, prompt, **target):
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 30000:
            raise ValueError("Invalid instruction")
        request_id = uuid5(self.owner_id, "smartdev-mcp:" + str(UUID(str(client_request_id))))
        result = self._workspace(project_id, "POST", "run", payload={
            "tree": "repository", "files": [], "requestId": str(request_id), "prompt": prompt, **target,
        })
        try:
            return {"task_id": result["objective"], "conversation_id": result["conversationId"],
                    "message_id": result["messageId"], "execution_id": result["executionId"],
                    "status": result.get("status", "running"),
                    **({"instruction_document_id": result["instructionDocumentId"]}
                       if result.get("instructionDocumentId") else {})}
        except KeyError:
            raise WorkspaceUnavailable("The workspace could not confirm execution. Retry with the same request ID.") from None

    def create_task(self, project_id, instructions, client_request_id, title=None):
        project_id = self._require_project(project_id)
        if title is not None and (not isinstance(title, str) or len(title.strip()) > 100 or not valid_title(title.strip())):
            raise ValueError("Invalid title")
        return self._submit(project_id, client_request_id, instructions, newTask=True,
                            title=title.strip() if title else None)

    def _send(self, task_id, message, client_request_id, waiting):
        task = super().get_task(task_id)
        return self._submit(UUID(task["project_id"]), client_request_id, message,
                            objective=task["task_id"], conversationId=task["task_id"], waitingReply=waiting)

    def send_message(self, task_id, message, client_request_id):
        return self._send(task_id, message, client_request_id, False)

    def reply_to_waiting_task(self, task_id, message, client_request_id):
        return self._send(task_id, message, client_request_id, True)

    def get_latest_response(self, task_id):
        task = super().get_task(task_id)
        result = self._workspace(UUID(task["project_id"]), "GET", "task-response",
                                 params={"task": task["task_id"]})
        if not isinstance(result.get("text"), str):
            raise WorkspaceUnavailable("The workspace could not retrieve the latest response.")
        task = self._task_details(task)
        return {**task, "conversation_id": task["task_id"], "text": result["text"]}
