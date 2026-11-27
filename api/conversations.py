"""Canonical conversation pointers for the authenticated SmartDev owner."""
from typing import Literal
import os
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from psycopg.types.json import Jsonb

from store import database
from acceptance import acceptance_projects, fixture_binding, allowed_submission
from core import workspace_name

router = APIRouter()


class Action(BaseModel):
    action: Literal["state", "authorize_diagnostics", "authorize_submission", "new_task_conversation", "new", "select", "rename", "focus", "activity", "claim", "accepted", "release", "start_run", "resume_run", "renew_run", "authorize_run", "finish_run", "record_question"]
    conversation_id: UUID | None = None
    fixture_conversation_id: UUID | None = None
    workspace_name: str | None = None
    task_id: UUID | None = None
    thread_id: UUID | None = None
    execution_id: UUID | None = None
    request_id: UUID | None = None
    task_payload_sha256: str | None = Field(None, pattern=r"^[a-f0-9]{64}$")
    version: int | None = Field(None, ge=1)
    name: str | None = Field(None, min_length=1, max_length=100)
    result: dict | None = None
    exit_code: int | None = Field(None, ge=-255, le=255)
    waiting_for_input: bool = False
    mode: Literal["read-only", "workspace-write"] = "read-only"
    original_request: str | None = Field(None, min_length=1, max_length=30000)


def owner(db):
    return db.execute("SELECT id FROM smartdev_owner WHERE singleton").fetchone()["id"]


@router.get("/account/project-selection")
def selected_project():
    with database() as db:
        row = db.execute("""SELECT p.id FROM smartdev_owner o JOIN projects p ON p.id=o.last_project_id
                            WHERE o.singleton AND NOT p.archived
                              AND NOT EXISTS (SELECT 1 FROM acceptance_fixture f WHERE f.project_id=p.id)""").fetchone()
        return {"id": row["id"] if row else None}


@router.put("/account/project-selection/{project_id}")
def save_selected_project(project_id: UUID):
    with database() as db:
        project = db.execute("SELECT id FROM projects WHERE id=%s AND NOT archived AND NOT EXISTS (SELECT 1 FROM acceptance_fixture f WHERE f.project_id=projects.id)", (project_id,)).fetchone()
        if not project:
            raise HTTPException(404, "Project not available.")
        db.execute("UPDATE smartdev_owner SET last_project_id=%s WHERE singleton", (project_id,))
    return {"ok": True}


@router.post("/workspace-memory/{project_id}/conversation")
def conversation(project_id: UUID, body: Action, request: Request):
    if body.action == "authorize_diagnostics":
        diagnostic = request.headers.get("x-smartdev-diagnostic")
        projects = {UUID(value.strip()) for value in os.getenv("SMARTDEV_DIAGNOSTIC_PROJECT_IDS","").split(",") if value.strip()}
        role = None
        if diagnostic is not None:
            if allowed_submission(project_id, diagnostic, projects, os.getenv("SMARTDEV_DIAGNOSTIC_TOKEN_HASH", "")):
                role = "administrator"
            elif allowed_submission(project_id, diagnostic, projects, os.getenv("SMARTDEV_DIAGNOSTIC_DEVELOPER_TOKEN_HASH", "")):
                role = "developer"
        if role is None:
            raise HTTPException(403, "Project diagnostics authorization required.")
        with database() as db:
            if not db.execute("SELECT id FROM projects WHERE id=%s AND NOT archived",(project_id,)).fetchone():
                raise HTTPException(404,"Project not available.")
        return {"ok":True,"role":role}
    credential = request.headers.get("x-smartdev-acceptance")
    with database() as db:
        project = db.execute("""SELECT p.id,p.status,p.workspace_url,f.conversation_id,f.token_digest,f.closed_at
            FROM projects p LEFT JOIN acceptance_fixture f ON f.project_id=p.id
            WHERE p.id=%s AND NOT p.archived FOR UPDATE OF p""", (project_id,)).fetchone()
        if not project:
            raise HTTPException(404, "Project not available.")
        fixture = project["conversation_id"]
        if body.action in {"authorize_submission", "claim"} or credential is not None:
            if fixture:
                if not fixture_binding(project_id, project, credential,
                                       str(body.fixture_conversation_id), body.workspace_name):
                    raise HTTPException(403, "Acceptance fixture binding is invalid.")
                if body.action == "claim" and not body.conversation_id:
                    raise HTTPException(422, "An explicit acceptance conversation is required.")
            elif credential is not None or project_id in acceptance_projects():
                raise HTTPException(403, "Submission identity is not authorized for this project.")
        if body.action == "authorize_submission":
            return {"ok": True, "project_id": project_id, "conversation_id": fixture,
                    "workspace_name": workspace_name(project_id),
                    "environment": "acceptance" if fixture else "user"}

        keys = (owner(db), project_id)
        pointer = db.execute("""SELECT * FROM project_conversation_pointer WHERE user_id=%s AND project_id=%s""",
                             keys).fetchone()
        if not pointer:
            first = db.execute("""INSERT INTO project_conversation(user_id,project_id,name,legacy)
                                  VALUES (%s,%s,'Project conversation',true) RETURNING id""", keys).fetchone()["id"]
            pointer = db.execute("""INSERT INTO project_conversation_pointer(user_id,project_id,conversation_id)
                                    VALUES (%s,%s,%s) RETURNING *""", (*keys, first)).fetchone()
        if body.action == "new_task_conversation":
            from governance import Definition, save_definition
            if not body.task_id or not body.original_request or not body.original_request.strip():
                raise HTTPException(422, "An explicit task ID and instruction are required.")
            original = body.original_request
            contract = db.execute("SELECT project_id,original_request FROM objective_contract WHERE id=%s",
                                  (body.task_id,)).fetchone()
            if contract:
                if contract["project_id"] != project_id or contract["original_request"] != original:
                    raise HTTPException(409, "Task ID already has a different owner or instruction.")
            else:
                save_definition(db, project_id, body.task_id,
                                Definition.model_construct(expected_outcome=original[:2000], criteria=[]), original)
            from task_titles import task_title, valid_title
            if body.name and not valid_title(body.name.strip()):
                raise HTTPException(422, "Invalid task title.")
            title = body.name.strip() if body.name else task_title(original)
            db.execute("""UPDATE objective_contract SET sidebar_task=true,display_name=COALESCE(display_name,%s)
                WHERE id=%s AND project_id=%s""", (title, body.task_id, project_id))
            db.execute("""INSERT INTO project_conversation(id,user_id,project_id,name,active_task_id)
                VALUES (%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING""",
                (body.task_id, *keys, title, body.task_id))
            task_chat = db.execute("""SELECT id,legacy,name,active_task_id FROM project_conversation
                WHERE user_id=%s AND project_id=%s AND id=%s AND active_task_id=%s""",
                (*keys,body.task_id,body.task_id)).fetchone()
            if not task_chat:
                raise HTTPException(409, "Task conversation ID is unavailable.")
            return task_chat
        target = body.conversation_id or pointer["conversation_id"]
        row = db.execute("""SELECT * FROM project_conversation WHERE user_id=%s AND project_id=%s AND id=%s""",
                         (*keys, target)).fetchone()
        if not row:
            raise HTTPException(404, "Conversation not available.")
        if fixture and body.action in {"claim", "start_run", "resume_run", "authorize_run"}:
            if not body.conversation_id or (target != fixture and row["active_task_id"] != target):
                raise HTTPException(403, "Execution does not belong to the acceptance conversation.")
        if body.action in {"new", "select", "rename", "focus"}:
            if body.version != pointer["version"]:
                raise HTTPException(409, "The conversation changed on another device. Reload and try again.")
            if body.action == "new":
                target = db.execute("""INSERT INTO project_conversation(user_id,project_id,name)
                                       VALUES (%s,%s,%s) RETURNING id""", (*keys, body.name or "New conversation")).fetchone()["id"]
            if body.action == "rename":
                if not body.name or not body.name.strip():
                    raise HTTPException(422, "Enter a conversation name.")
                db.execute("UPDATE project_conversation SET name=%s,updated_at=now() WHERE id=%s",
                           (body.name.strip(), target))
                target = pointer["conversation_id"]
            if body.action == "focus":
                focused = body.task_id
                if row["id"] == row["active_task_id"] and focused != row["id"]:
                    raise HTTPException(409, "This conversation belongs to a different task.")
            elif body.action == "select" and row["id"] == row["active_task_id"]:
                focused = row["id"]
            elif body.action == "rename":
                focused = pointer["focused_task_id"]
            else:
                focused = None
            if focused and body.action in {"focus", "select"} and not db.execute(
                "SELECT 1 FROM objective_contract WHERE id=%s AND project_id=%s", (focused, project_id)
            ).fetchone():
                raise HTTPException(404, "Task not available.")
            if pointer["conversation_id"] != target or pointer["focused_task_id"] != focused or body.action=="rename":
                pointer = db.execute("""UPDATE project_conversation_pointer
                    SET conversation_id=%s,focused_task_id=%s,version=version+1,updated_at=now()
                    WHERE user_id=%s AND project_id=%s RETURNING *""", (target, focused, *keys)).fetchone()
        if body.action == "activity" and body.thread_id and not body.task_id:
            db.execute("UPDATE project_conversation SET codex_session_id=%s,updated_at=now() WHERE id=%s",
                       (body.thread_id,target))
        elif body.action == "activity" and body.task_id:
            if not body.thread_id:
                db.execute("""UPDATE project_conversation SET active_task_id=%s,codex_session_id=NULL,updated_at=now()
                              WHERE id=%s AND active_task_id IS DISTINCT FROM %s""",
                           (body.task_id, target, body.task_id))
            else:
                db.execute("""UPDATE project_conversation SET codex_session_id=%s,updated_at=now()
                              WHERE id=%s AND active_task_id=%s""", (body.thread_id, target, body.task_id))
        if body.action in {"start_run", "resume_run", "renew_run", "authorize_run", "finish_run", "record_question"}:
            if not body.execution_id:
                raise HTTPException(422, "An execution ID is required.")
            execution = body.execution_id
            if body.action in {"start_run", "resume_run"}:
                if body.mode != "workspace-write":
                    raise HTTPException(409, "Upgrade this workspace to the matching SmartDev release before starting Main.")
                if row["id"] == row["active_task_id"]:
                    raise HTTPException(409, "Use the task execution context for this conversation.")
                if db.execute("""SELECT 1 FROM conversation_execution r JOIN project_conversation c ON c.id=r.conversation_id
                    WHERE c.project_id=%s AND r.finished_at IS NULL AND r.expires_at>now()""", (project_id,)).fetchone():
                    raise HTTPException(409, "The project checkout already has a Main execution.")
                if body.action == "resume_run":
                    if not body.thread_id or row["codex_session_id"] != body.thread_id:
                        raise HTTPException(409, "The original Codex thread could not be confirmed.")
                    resumed = db.execute("""UPDATE conversation_execution SET finished_at=NULL,
                        expires_at=now()+interval '90 seconds',exit_code=NULL
                        WHERE id=%s AND conversation_id=%s AND mode='workspace-write'
                          AND finished_at IS NOT NULL AND waiting_for_input RETURNING id""",
                        (execution,target)).fetchone()
                    if not resumed:
                        raise HTTPException(409, "This Main execution is not waiting for input.")
                    return {"ok":True,"mode":body.mode}
                db.execute("""INSERT INTO conversation_execution(id,conversation_id,expires_at,mode)
                    VALUES (%s,%s,now()+interval '90 seconds',%s)""", (execution,target,body.mode))
                return {"ok":True,"mode":body.mode}
            elif body.action == "finish_run":
                db.execute("""UPDATE conversation_execution SET finished_at=now(),exit_code=%s,waiting_for_input=%s
                    WHERE id=%s AND conversation_id=%s AND finished_at IS NULL""",
                    (body.exit_code,body.waiting_for_input,execution,target))
            elif body.action == "record_question":
                db.execute("""UPDATE conversation_execution SET waiting_for_input=%s
                    WHERE id=%s AND conversation_id=%s AND finished_at IS NOT NULL AND exit_code=0""",
                    (body.waiting_for_input,execution,target))
            elif body.action == "renew_run":
                if not db.execute("""UPDATE conversation_execution SET expires_at=now()+interval '90 seconds'
                    WHERE id=%s AND conversation_id=%s AND finished_at IS NULL AND expires_at>now() RETURNING id""",
                                  (execution,target)).fetchone():
                    raise HTTPException(409,"Conversation execution expired.")
            else:
                authorized = db.execute("""SELECT mode FROM conversation_execution WHERE id=%s AND conversation_id=%s
                    AND finished_at IS NULL AND expires_at>now()""", (execution,target)).fetchone()
                if not authorized:
                    raise HTTPException(403,"Conversation execution is unauthorized.")
                return {"ok":True,"mode":authorized["mode"]}
            return {"ok":True}
        if body.action in {"claim", "accepted", "release"}:
            if not body.request_id:
                raise HTTPException(422, "A client request ID is required.")
            if body.action == "claim":
                environment = "acceptance" if credential is not None else "user"
                task_project = project_id if body.task_payload_sha256 else None
                if task_project:
                    prior_task = db.execute("""SELECT conversation_id,task_payload_sha256 FROM conversation_submission
                        WHERE task_project_id=%s AND request_id=%s""", (project_id,body.request_id)).fetchone()
                    if prior_task:
                        if prior_task["task_payload_sha256"] != body.task_payload_sha256:
                            raise HTTPException(409, "Task submission ID already belongs to different instructions.")
                        target = prior_task["conversation_id"]
                claimed = db.execute("""INSERT INTO conversation_submission(conversation_id,request_id,environment,task_project_id,task_payload_sha256,message_id)
                                        VALUES (%s,%s,%s,%s,%s,COALESCE((SELECT task_id FROM mcp_task_request
                                            WHERE project_id=%s AND request_id=%s AND admission='created'
                                              AND payload_digest=%s AND %s='user'),gen_random_uuid())) ON CONFLICT (conversation_id,request_id) DO UPDATE SET released=false
                                        WHERE conversation_submission.released AND conversation_submission.result IS NULL
                                          AND conversation_submission.task_payload_sha256 IS NOT DISTINCT FROM EXCLUDED.task_payload_sha256
                                          AND (conversation_submission.environment=EXCLUDED.environment OR
                                            (conversation_submission.environment IS NULL AND EXCLUDED.environment='user'))
                                        RETURNING message_id""",
                                     (target, body.request_id, environment, task_project, body.task_payload_sha256,
                                      task_project,body.request_id,body.task_payload_sha256,environment)).fetchone()
                if claimed:
                    return {**claimed, "claimed": True, "conversation_id": target}
                prior = db.execute("""SELECT message_id,result,environment,task_payload_sha256 FROM conversation_submission
                                      WHERE conversation_id=%s AND request_id=%s""",
                                   (target, body.request_id)).fetchone()
                if not prior or (prior["environment"] or "user") != environment or prior["task_payload_sha256"] != body.task_payload_sha256:
                    raise HTTPException(409, "The submission belongs to another execution environment.")
                return {"message_id": prior["message_id"], "result": prior["result"], "claimed": False, "conversation_id": target}
            if body.action == "accepted":
                saved = db.execute("""UPDATE conversation_submission SET result=%s
                              WHERE conversation_id=%s AND request_id=%s AND result IS NULL
                              RETURNING task_project_id,environment,message_id""",
                           (Jsonb(body.result), target, body.request_id)).fetchone()
                if saved and saved["environment"] == "user" and saved["task_project_id"] == project_id:
                    from mcp_request_history import confirm_submission
                    confirm_submission(db, project_id, body.request_id, saved["message_id"], body.result)
            elif body.action == "release":
                db.execute("""UPDATE conversation_submission SET released=true
                              WHERE conversation_id=%s AND request_id=%s AND result IS NULL""",
                           (target, body.request_id))
            return {"ok": True}
        rows = db.execute("""SELECT id,name,legacy,active_task_id,codex_session_id,updated_at FROM project_conversation
                             WHERE user_id=%s AND project_id=%s ORDER BY updated_at DESC,id""", keys).fetchall()
        exclusions = db.execute("""SELECT x.execution_id FROM conversation_presentation_exclusion x
            JOIN conversation_execution e ON e.id=x.execution_id
            JOIN project_conversation c ON c.id=e.conversation_id
            WHERE c.user_id=%s AND c.project_id=%s""", keys).fetchall()
        return {"version":pointer["version"],"focusedTask":pointer["focused_task_id"],"active":next(item for item in rows if item["id"]==pointer["conversation_id"]),
                "conversations":rows, "presentationExclusions":[row["execution_id"] for row in exclusions]}
