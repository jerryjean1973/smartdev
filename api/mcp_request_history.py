"""Retain authorized MCP task writes independently of workspace availability."""
import os
import json
import re
from hashlib import sha256
from uuid import UUID

from fastapi import APIRouter, HTTPException
from store import database
from core import workspace_name
from work_messages import visible_text

router = APIRouter()


def execution_reference(project, execution):
    return sha256(("/projects/" + workspace_name(project) + "\0" + str(execution)).encode()).hexdigest()[:24]


def confirm_submission(db, project, request, message, result):
    if not result or not result.get("objective") or not result.get("executionId"):
        return
    db.execute("""UPDATE mcp_task_request SET task_id=c.id,message_id=%s,execution_ref=%s,delivery='accepted'
        FROM objective_contract c WHERE c.project_id=%s AND c.id::text=%s AND
        mcp_task_request.project_id=c.project_id AND mcp_task_request.request_id=%s""",
        (message, execution_reference(project, result["executionId"]), project, result["objective"], request))


def received(project, request, kind, prompt, title, task, scope=None):
    # Match the workspace claim so retries of historical requests get the same content check.
    payload = [prompt, title, []] if kind == "create" else [str(task), kind == "reply", prompt, []]
    fingerprint = sha256(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    with database() as db:
        if scope is not None:
            if not isinstance(scope, str):
                raise ValueError("Invalid request scope.")
            scope = scope.strip()
            if scope not in ("global", "unassigned") and not db.execute(
                "SELECT 1 FROM business_objective WHERE id=%s", (scope,)
            ).fetchone():
                raise ValueError("Unknown request scope.")
        row = db.execute("""INSERT INTO mcp_task_request
            (project_id,request_id,task_id,kind,received_at,instruction,title,payload_digest,delivery,request_scope)
            VALUES (%s,%s,%s,%s,now(),%s,%s,%s,'received',%s)
            ON CONFLICT(project_id,request_id) DO UPDATE SET
              instruction=COALESCE(mcp_task_request.instruction,EXCLUDED.instruction),
              title=COALESCE(NULLIF(mcp_task_request.title,''),EXCLUDED.title),
              kind=CASE WHEN mcp_task_request.kind='submission' THEN EXCLUDED.kind ELSE mcp_task_request.kind END,
              task_id=COALESCE(mcp_task_request.task_id,EXCLUDED.task_id)
            WHERE mcp_task_request.payload_digest=EXCLUDED.payload_digest
              AND (EXCLUDED.request_scope IS NULL OR mcp_task_request.request_scope IS NOT DISTINCT FROM EXCLUDED.request_scope)
            RETURNING request_id""", (project, request, task, kind, visible_text(prompt), visible_text(title), fingerprint, scope)).fetchone()
        if not row:
            raise ValueError("Request ID already belongs to another instruction.")


def delivered(project, request, result=None):
    with database() as db:
        if result is None:
            db.execute("""UPDATE mcp_task_request SET delivery='unconfirmed'
                WHERE project_id=%s AND request_id=%s AND delivery='received'""", (project, request))
        else:
            db.execute("""UPDATE mcp_task_request SET task_id=%s,message_id=%s,execution_ref=%s,delivery='accepted',dispatch_hold=NULL
                WHERE project_id=%s AND request_id=%s""", (UUID(result["task_id"]),UUID(result["message_id"]),
                result["execution_id"],project,request))
            db.execute("DELETE FROM mcp_task_outbox WHERE project_id=%s AND request_id=%s",(project,request))


# Scope every relation to the original project and honor explicit task removal and fixture isolation.
SOURCE = """
 FROM mcp_task_request r JOIN projects p ON p.id=r.project_id
 LEFT JOIN mcp_task_request covered ON r.admission='duplicate' AND (covered.project_id,covered.request_id)=(r.project_id,r.matched_request)
 LEFT JOIN objective_contract c ON (c.project_id,c.id)=(r.project_id,r.task_id)
 CROSS JOIN LATERAL (SELECT COALESCE(r.request_scope,mcp_inferred_scope(COALESCE(NULLIF(r.title,''),r.instruction))) AS value) assignment
 LEFT JOIN business_objective bo ON bo.id=assignment.value
 LEFT JOIN taskboard_item num ON (num.project_id,num.item_id)=(r.project_id,r.board_item)
 LEFT JOIN LATERAL (SELECT revision,summary,evidence,verified_at FROM taskboard_repair proof
   WHERE (proof.project_id,proof.request_id)=(r.project_id,r.request_id) ORDER BY verified_at DESC LIMIT 1) repair ON true
 WHERE NOT p.archived AND NOT EXISTS (SELECT 1 FROM acceptance_fixture f WHERE f.project_id=p.id)
 AND NOT EXISTS (SELECT 1 FROM objective_presentation hidden WHERE r.kind='create' AND hidden.objective_id=c.id
                 AND hidden.excluded_from_board AND hidden.reason='Removed from Taskboard by the owner.')
"""
COLUMNS = """r.request_id,r.project_id,p.name AS project_name,r.task_id,r.task_id AS codex_task_id,r.admission,r.matched_request,r.message_id,
 taskboard_number(num.number) AS task_number,r.ingress_tenant AS tenant_name,r.ingress_actor::text AS actor,
 COALESCE(NULLIF(r.ingress_client,''),CASE WHEN r.kind<>'submission' THEN 'MCP' END) AS connector,
 CASE WHEN cc_board_destination(r.project_id,r.request_scope,c.business_objective) THEN 'Command Center' ELSE 'Workspace' END AS destination,
 COALESCE(r.failure_reason,CASE WHEN r.delivery<>'accepted' THEN r.dispatch_hold END) AS failure_reason,CASE WHEN repair.revision IS NOT NULL THEN to_jsonb(repair) END AS repair,r.execution_ref AS receipt,r.kind,r.delivery,r.received_at,bo.id AS business_objective,
 CASE WHEN COALESCE(covered.owner_done_at,r.owner_done_at) IS NOT NULL OR (r.kind='create' AND c.status='done') THEN 'Done'
      WHEN COALESCE(covered.execution_phase,r.execution_phase) IS NOT NULL THEN COALESCE(covered.execution_phase,r.execution_phase)
      WHEN COALESCE(covered.delivery,r.delivery)='accepted' OR COALESCE(covered.dispatch_hold,r.dispatch_hold) IS NOT NULL THEN 'Queued' ELSE 'Pending' END AS work_status,
 r.execution_code,r.waiting_for_input,r.execution_updated_at,
 CASE WHEN bo.id IS NOT NULL THEN 'objective' WHEN assignment.value='global' THEN 'global' ELSE 'unassigned' END AS scope_kind,
 c.business_objective AS origin_business_objective,c.display_name AS task_title,
 COALESCE(num.description,NULLIF(r.title,''),NULLIF(left(regexp_replace(r.instruction,'[[:space:]]+',' ','g'),175),''),'Description not retained') AS label"""


def central_scope():
    return " AND r.kind<>'submission' AND cc_board_destination(r.project_id,r.request_scope,c.business_objective)"


def task_number(project, item):
    with database() as db:
        row=db.execute("SELECT taskboard_number(number) AS label FROM taskboard_item WHERE project_id=%s AND item_id=%s",
                       (project,item)).fetchone()
        return row["label"] if row else None


@router.get("/taskboard/mcp-requests")
def board_requests(project_id: UUID | None = None):
    with database() as db:
        return db.execute("SELECT " + COLUMNS + SOURCE + central_scope() + (" AND r.project_id=%s" if project_id else "") +
                          " ORDER BY r.received_at DESC NULLS LAST,r.request_id", (project_id,) if project_id else ()).fetchall()


@router.get("/projects/{project_id}/mcp-requests")
def workspace_requests(project_id: UUID):
    with database() as db:
        return db.execute("SELECT "+COLUMNS+SOURCE+" AND r.project_id=%s AND r.kind<>'submission' ORDER BY r.received_at DESC NULLS LAST,r.request_id",
                          (project_id,)).fetchall()


def lookup(reference, projects=None, *, include_instruction=False,central=False):
    key = str(reference).lower()
    if not re.fullmatch(r"(?:[a-f0-9]{24}|[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12})",key):
        raise LookupError("Receipt unavailable")
    condition = " AND (r.execution_ref=%s OR r.request_id::text=%s)"
    args = [key,key]
    if projects is not None:
        condition += " AND r.project_id=ANY(%s)"
        args.append(list(projects))
    with database() as db:
        fields = COLUMNS + ",r.instruction" if include_instruction else COLUMNS
        rows = db.execute("SELECT " + fields + SOURCE + (central_scope() if central else "") + condition, args).fetchall()
    if len(rows) != 1:
        raise LookupError("Receipt unavailable")
    return rows[0]


@router.get("/taskboard/mcp-requests/{reference}")
def board_request(reference: str, project_id: UUID | None = None):
    try:
        return lookup(reference, [project_id] if project_id else None, include_instruction=True, central=not bool(os.getenv("SMD_PORTAL_TASK")))
    except LookupError:
        raise HTTPException(404,"MCP request unavailable.") from None

@router.post("/projects/{project_id}/mcp-tasks/{request_id}/complete")
def complete_request(project_id: UUID, request_id: UUID):
    with database() as db:
        try:
            lookup(str(request_id), [project_id])
        except LookupError:
            raise HTTPException(404, "MCP task unavailable.") from None
        row = db.execute("""UPDATE mcp_task_request SET owner_done_at=COALESCE(owner_done_at,now())
            WHERE project_id=%s AND request_id=%s AND execution_phase='Processed'
              AND execution_code=0 AND NOT waiting_for_input AND kind<>'create' RETURNING request_id""",
            (project_id, request_id)).fetchone()
        if not row:
            raise HTTPException(409, "The request needs a successful finished execution before completion.")
    return {"ok": True}


@router.get("/projects/{project_id}/mcp-tasks/{request_id}/activity")
def request_activity(project_id: UUID, request_id: UUID, before: int | None = None):
    from cc_codex import workspace_actions
    from external_mcp_tasks import WorkspaceUnavailable
    from cc_codex import workspace_chat_request
    try:
        request=lookup(str(request_id),[project_id])
    except LookupError:
        raise HTTPException(404,"MCP request unavailable.") from None
    if before is not None and before < 1:
        raise HTTPException(422, "Invalid activity cursor.")
    receipt, task = request["receipt"], request["task_id"]
    if not receipt or not task:
        return {"workspace": {"running": False, "actions": [], "start": 0, "end": 0}}
    with database() as db:
        row = db.execute("SELECT workspace_url FROM projects WHERE id=%s AND NOT archived", (project_id,)).fetchone()
    if not row or not row["workspace_url"]:
        return {"workspace": None}
    try:
        page = workspace_chat_request(project_id, row["workspace_url"], "GET", "task", timeout=4,
                                     params={"tree":f"worktrees/task-{task}", "objective":str(task), "activity":"1",
                                             **({"before":before} if before is not None else {})})
    except WorkspaceUnavailable:
        return {"workspace": None}
    actions = [entry for entry in workspace_actions(page.get("output") or "") if entry["id"].startswith(receipt + ":")]
    return {"workspace":{"actions":actions, "running":request["work_status"]=="Processing",
                         "start":page.get("start",0), "end":page.get("end",0)}}

def pending_task(task, projects):
    with database() as db:
        row = db.execute("SELECT "+COLUMNS+SOURCE+" AND r.task_id=%s AND r.project_id=ANY(%s) AND r.admission IS DISTINCT FROM 'duplicate' ORDER BY r.received_at DESC LIMIT 1",
                         (UUID(str(task)), [UUID(str(p)) for p in projects])).fetchone()
    if not row:
        raise LookupError("Task not available")
    return {"task_id":str(row["task_id"]), "codex_task_id":str(row["task_id"]), "task_number":row["task_number"],
            "title":row["task_title"] or row["label"], "project_id":str(row["project_id"]), "status":row["work_status"]}

def pending_tasks(project):
    with database() as db:
        rows=db.execute("SELECT DISTINCT ON(r.task_id) "+COLUMNS+SOURCE+
            " AND r.project_id=%s AND r.admission='created' AND c.id IS NULL ORDER BY r.task_id,r.received_at",
            (project,)).fetchall()
    return [{"project_id":str(project),"task_id":str(r["task_id"]),"codex_task_id":str(r["task_id"]),"task_number":r["task_number"],
             "title":r["label"],"status":r["work_status"]} for r in rows]
