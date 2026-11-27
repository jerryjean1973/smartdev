"""Forward scoped MCP operations to the existing private Coder chat app."""
import os
import re
from urllib.parse import urlsplit
from uuid import UUID, uuid5

import httpx

import mcp_request_history as history
from core import workspace_name
from external_mcp_read import ScopedTaskReads
from store import database
from task_titles import valid_title


class WorkspaceUnavailable(Exception):
    pass


def workspace_chat_request(project_id, workspace_url, method, endpoint, *, params=None, payload=None, timeout=25):
    address = urlsplit(workspace_url or "")
    public = urlsplit(os.environ["CODER_PUBLIC_URL"])
    path = address.path.rstrip("/")
    expected = r"/@[A-Za-z0-9_-]+/" + re.escape(workspace_name(project_id))
    if ((address.scheme, address.netloc) != (public.scheme, public.netloc)
            or address.query or address.fragment or not re.fullmatch(expected, path)):
        raise WorkspaceUnavailable("The project workspace is unavailable.")
    try:
        with httpx.Client(base_url=os.environ["CODER_API_URL"], timeout=timeout,
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



class ScopedTasks(ScopedTaskReads):
    def __init__(self, grant, **sources):
        super().__init__(grant["project_ids"], **sources)
        self.grant_id = grant.get("id")
        self.owner_id = UUID(str(grant["owner_id"]))

    def release_dev(self, project_id, grant, ticket):
        from operator_task import Dispatch, RELEASE_OPERATION, SignedGrant, dispatch, release_project
        from fastapi import HTTPException
        from fastapi.encoders import jsonable_encoder

        project = self._require_project(project_id)
        approved = SignedGrant.model_validate(grant)
        if approved.operation != RELEASE_OPERATION or project != release_project() or approved.project_id != project:
            raise LookupError("Release unavailable")
        request = Dispatch(
            approval_id=approved.id, task_id=approved.task_id, execution_id=approved.execution_id,
            environment=approved.environment, operation=approved.operation,
            workspace=approved.workspace, revision=approved.revision, ticket=ticket, grant=approved,
        )
        try:
            return jsonable_encoder(dispatch(project, request))
        except HTTPException as error:
            if error.status_code != 403:
                raise
            return jsonable_encoder(error.detail)

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

    def get_receipt(self, receipt):
        from fastapi.encoders import jsonable_encoder
        return jsonable_encoder(history.lookup(receipt, self.project_ids))

    def _workspace(self, project_id, method, endpoint, *, params=None, payload=None):
        project = next((row for row in self.projects() if UUID(str(row["id"])) == project_id
                        and not row.get("archived")), None)
        return workspace_chat_request(project_id, (project or {}).get("workspace_url"), method, endpoint,
                                      params=params, payload=payload)

    def _submit(self, project_id, client_request_id, prompt, scope=None, **target):
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 30000:
            raise ValueError("Invalid instruction")
        request_id = uuid5(self.owner_id, "smartdev-mcp:" + str(UUID(str(client_request_id))))
        kind = "create" if target.get("newTask") else "reply" if target.get("waitingReply") else "message"
        history.received(project_id,request_id,kind,prompt,target.get("title"),target.get("objective"),scope)
        try:
            result = self._workspace(project_id, "POST", "run", payload={
                "tree": "repository", "files": [], "requestId": str(request_id), "prompt": prompt, **target,
            })
            reply = {"task_id": result["objective"], "conversation_id": result["conversationId"],
                    "message_id": result["messageId"], "execution_id": result["executionId"],
                    "request_id": str(request_id),
                    "status": result.get("status", "running"),
                    **({"instruction_document_id": result["instructionDocumentId"]}
                       if result.get("instructionDocumentId") else {})}
        except (KeyError, WorkspaceUnavailable) as error:
            history.delivered(project_id,request_id)
            detail = str(error) if isinstance(error, WorkspaceUnavailable) else "Workspace confirmation unavailable."
            raise WorkspaceUnavailable(f"{detail} Taskboard request {request_id}.") from None
        history.delivered(project_id,request_id,reply)
        return reply

    def create_task(self, project_id, instructions, client_request_id, title=None, scope=None):
        project_id = self._require_project(project_id)
        if title is not None and (not isinstance(title, str) or len(title.strip()) > 100 or not valid_title(title.strip())):
            raise ValueError("Invalid title")
        return self._submit(project_id, client_request_id, instructions, scope=scope, newTask=True,
                            title=title.strip() if title else None)

    def _send(self, task_id, message, client_request_id, waiting, scope):
        task = super().get_task(task_id)
        return self._submit(UUID(task["project_id"]), client_request_id, message, scope=scope,
                            objective=task["task_id"], conversationId=task["task_id"], waitingReply=waiting)

    def send_message(self, task_id, message, client_request_id, scope=None):
        return self._send(task_id, message, client_request_id, False, scope)

    def reply_to_waiting_task(self, task_id, message, client_request_id, scope=None):
        return self._send(task_id, message, client_request_id, True, scope)

    def get_latest_response(self, task_id):
        task = super().get_task(task_id)
        result = self._workspace(UUID(task["project_id"]), "GET", "task-response",
                                 params={"task": task["task_id"]})
        if not isinstance(result.get("text"), str):
            raise WorkspaceUnavailable("The workspace could not retrieve the latest response.")
        task = self._task_details(task)
        return {**task, "conversation_id": task["task_id"], "text": result["text"]}
