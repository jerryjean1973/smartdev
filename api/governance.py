"""Persist project objectives, coordinate assignments and gate completion on evidence."""
import os
import re
from pathlib import PurePosixPath
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, StringConstraints, model_validator

from store import database

router = APIRouter()
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
Fingerprint = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
RETRY_LIMIT = max(1, int(os.getenv("GOVERNANCE_RETRY_LIMIT", "3")))
LEASE = "interval '90 seconds'"
BOARD_DELETION = "Removed from Taskboard by the owner."


class Criterion(BaseModel):
    description: Text
    mandatory: bool = True
    mode: Literal["workspace-write", "read-only"] = "workspace-write"
    dependency_files: list[Text] = Field(default_factory=list, max_length=50)
    depends_on: list[int] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def relative_dependencies(self):
        for name in self.dependency_files:
            path = PurePosixPath(name)
            if (path.is_absolute() or path.as_posix() != name or
                    any(part in (".git", "..") for part in path.parts) or not path.parts):
                raise ValueError("Use repository-relative dependency file paths.")
        return self


class Definition(BaseModel):
    expected_outcome: Text
    criteria: list[Criterion] = Field(min_length=1, max_length=30)
    scope: list[Text] = Field(default_factory=list, max_length=30)
    constraints: list[Text] = Field(default_factory=list, max_length=30)
    dependencies: list[Text] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def dependency_order(self):
        if not any(item.mandatory for item in self.criteria):
            raise ValueError("At least one acceptance criterion must be mandatory.")
        for index, criterion in enumerate(self.criteria, 1):
            if any(not 1 <= dependency < index for dependency in criterion.depends_on):
                raise ValueError("Criterion dependencies must refer to earlier criteria.")
        return self


class Create(Definition):
    original_request: str = Field(min_length=1, max_length=30000)


class Amendment(Definition):
    version: int = Field(ge=1)
    reason: Text


class Evidence(BaseModel):
    version: int = Field(ge=1)
    criterion_id: str = Field(pattern=r"^criterion-[1-9][0-9]?$")
    fingerprint: Fingerprint
    evidence: Text
    decision: Literal["verified", "blocked"] = "verified"


class Assignment(BaseModel):
    action: Literal["prepare", "authorize", "begin", "heartbeat", "finish"]
    execution_id: UUID
    objective_id: UUID
    original_request: str | None = Field(default=None, min_length=1, max_length=30000)
    explicit_task: bool = False
    criterion_id: str | None = Field(default=None, pattern=r"^(?:criterion-[1-9][0-9]?|implementation)$")
    agent: str = Field(default="codex", min_length=1, max_length=100)
    worktree: str = Field(default="repository", min_length=1, max_length=300)
    branch: str = Field(default="detached", min_length=1, max_length=250)
    head: str | None = Field(default=None, pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    version: int | None = Field(default=None, ge=1)
    fingerprint: Fingerprint | None = None
    fingerprints: dict[str, Fingerprint] = Field(default_factory=dict, max_length=30)
    exit_code: int | None = Field(default=None, ge=0, le=255)
    changed_files: int | None = Field(default=None, ge=0, le=1000000)


def lock_project(db, project_id):
    if not db.execute("SELECT 1 FROM projects WHERE id=%s AND NOT archived FOR UPDATE",
                      (project_id,)).fetchone():
        raise HTTPException(404, "Active project required.")
    db.execute("""
        WITH expired AS (
            UPDATE objective_run SET finished_at=now(), exit_code=124
            WHERE project_id=%s AND finished_at IS NULL AND lease_until<=now()
            RETURNING id
        )
        UPDATE workspace_execution SET finished_at=now(), exit_code=124
        WHERE id IN (SELECT id FROM expired) AND finished_at IS NULL
    """, (project_id,))


def contract(db, project_id, objective_id):
    row = db.execute("SELECT * FROM objective_contract WHERE project_id=%s AND id=%s",
                     (project_id, objective_id)).fetchone()
    if not row:
        raise HTTPException(404, "Objective not found.")
    return row


def save_definition(db, project_id, objective_id, definition, original_request=None):
    criteria = [{"id": f"criterion-{index}", **criterion.model_dump()}
                for index, criterion in enumerate(definition.criteria, 1)]
    if not criteria:
        criteria=[{"id":"implementation","description":"Implement the task scope and gather evidence.",
                   "mandatory":False,"mode":"workspace-write","depends_on":[],"dependency_files":[]}]
    values = (definition.expected_outcome, Jsonb(criteria), definition.scope,
              definition.constraints, definition.dependencies)
    if original_request is not None:
        row = db.execute("""
            INSERT INTO objective_contract
                (id,project_id,original_request,expected_outcome,criteria,scope,constraints,dependencies)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *
        """, (objective_id, project_id, original_request, *values)).fetchone()
    else:
        row = db.execute("""
            UPDATE objective_contract SET expected_outcome=%s,criteria=%s,scope=%s,constraints=%s,
                dependencies=%s,version=version+1,status='active' WHERE id=%s RETURNING *
        """, (*values, objective_id)).fetchone()
    db.execute("INSERT INTO objective_revision (objective_id,version,definition) VALUES (%s,%s,%s)",
               (objective_id, row["version"], Jsonb(definition.model_dump(mode="json"))))
    return row


def verified_screenshot_urls(evidence):
    urls = {}
    for note in evidence:
        for match in re.finditer(r"""https://[^\s<>"'()]+""", note, re.I):
            url = match.group().rstrip(".,;")
            try:
                parts = urlsplit(url)
                if (parts.hostname and not parts.username and not parts.password
                        and re.search(r"\.(?:png|jpe?g|webp|gif)$", parts.path, re.I)):
                    urls[url] = None
            except ValueError:
                continue
    return list(urls)


def progress(db, row, candidate=None):
    key = (row["id"], row["version"])
    recent = db.execute("""
        SELECT DISTINCT ON (criterion_id) criterion_id, id, branch, worktree, head,
            COALESCE(final_fingerprint, fingerprint) AS fingerprint, exit_code, started_at, finished_at
        FROM objective_run WHERE objective_id=%s AND version=%s
        ORDER BY criterion_id, started_at DESC, id DESC
    """, key).fetchall()
    attempts = {run["criterion_id"]: run for run in recent}
    checkpoints = db.execute("""
        SELECT DISTINCT ON (criterion_id) criterion_id,fingerprint,evidence,decision,recorded_at,invalidated_at
        FROM objective_checkpoint WHERE objective_id=%s AND version=%s
        ORDER BY criterion_id,id DESC
    """, key).fetchall()
    valid = {item["criterion_id"]: item for item in checkpoints
             if item["invalidated_at"] is None and
             attempts.get(item["criterion_id"], {}).get("fingerprint") == item["fingerprint"]}
    completed, outstanding, blocked = [], [], []
    ready = []
    active = db.execute("""
        SELECT criterion_id,agent,branch,worktree FROM objective_run
        WHERE objective_id=%s AND version=%s AND finished_at IS NULL
    """, key).fetchall()
    assigned = {run["criterion_id"] for run in active}
    for item in row["criteria"]:
        name = item["id"]
        decision = valid.get(name, {}).get("decision")
        prereqs_met = all(f"criterion-{dependency}" in completed for dependency in item["depends_on"])
        if decision == "verified" and prereqs_met:
            completed.append(name)
            continue
        outstanding.append(name)
        failure_count = 0
        if name in attempts:
            failure_count = db.execute("""
                SELECT count(*) AS count FROM objective_run
                WHERE objective_id=%s AND version=%s AND criterion_id=%s AND fingerprint=%s
                  AND exit_code NOT IN (0,78,130)
                  AND started_at > COALESCE((
                      SELECT max(started_at) FROM objective_run
                      WHERE objective_id=%s AND version=%s AND criterion_id=%s AND exit_code=0
                  ), '-infinity')
            """, (*key, name, candidate[1] if candidate and candidate[0] == name else attempts[name]["fingerprint"], *key, name)).fetchone()["count"]
        if decision == "blocked" or failure_count >= RETRY_LIMIT:
            blocked.append(name)
        elif name not in assigned and prereqs_met:
            ready.append(item)
    required = {item["id"] for item in row["criteria"] if item["mandatory"] and item["id"]!="implementation"}
    complete = bool(required) and required.issubset(completed) and not active
    if not required and not active and row["status"] == "done" and recent:
        latest = max(recent, key=lambda run: (run["started_at"], run["id"]))
        complete = latest["exit_code"] == 0 and bool(db.execute("""
            SELECT 1 FROM objective_owner_approval
            WHERE objective_id=%s AND version=%s AND run_id=%s
        """, (*key, latest["id"])).fetchone())
    status = ("done" if complete and row["status"]=="done" else "ready_for_review" if complete else
              "blocked" if required.intersection(blocked) or (blocked and not ready and not active) else "active")
    if status != row["status"] and candidate is None:
        db.execute("UPDATE objective_contract SET status=%s WHERE id=%s", (status, row["id"]))
    awaiting_review = [
        item["id"] for item in ready if attempts.get(item["id"], {}).get("exit_code") == 0
    ]
    next_action = "Review the verified task for integration." if complete and status=="ready_for_review" else None
    if not complete and not required and not active:
        next_action = "Define acceptance criteria before requesting review."
    elif not complete and ready:
        next_criterion = ready[0]
        if attempts.get(next_criterion["id"], {}).get("exit_code") == 0:
            next_action = f"Review evidence for {next_criterion['id']}: {next_criterion['description']}"
        else:
            next_action = next_criterion["description"]
    evidence = [valid[name]["evidence"] for name in completed]
    return {
        "objectiveId": row["id"], "status": status, "completedCriteria": completed,
        "executionState": "running" if active else "idle",
        "awaitingReview": awaiting_review,
        "outstandingCriteria": [name for name in outstanding if name!="implementation"], "blockedCriteria": blocked,
        "verifiedEvidence": evidence,
        "verifiedScreenshots": verified_screenshot_urls(evidence),
        "blockers": [valid[name]["evidence"] if name in valid else f"{name} reached the retry limit."
                     for name in blocked],
        "nextAction": next_action,
        "ready": [item["id"] for item in ready],
        "active": active, "attempts": recent, "checkpoints": checkpoints,
    }


@router.get("/projects/{project_id}/objectives")
def list_objectives(project_id: UUID, source: Literal["mcp"] | None = None, board: bool = False):
    with database() as db:
        lock_project(db, project_id)
        rows = db.execute("""
            SELECT c.*,taskboard_number(num.number) AS task_number,tenant.tenant_name FROM objective_contract c
            LEFT JOIN taskboard_item num ON (num.project_id,num.item_id)=(c.project_id,c.id)
            LEFT JOIN command_center_project_tenant tenant ON tenant.project_id=c.project_id
            LEFT JOIN objective_presentation placement ON placement.objective_id=c.id AND placement.excluded_from_board
            WHERE c.project_id=%s AND placement.objective_id IS NULL
        """ + (""" AND cc_board_destination(c.project_id,substring(c.business_objective from '^BO-[0-9]+'),c.business_objective)""" if board and not os.getenv('SMD_PORTAL_TASK') else "") + (""" AND EXISTS (SELECT 1 FROM mcp_task_request r WHERE r.project_id=c.project_id
              AND r.task_id=c.id AND r.kind='create' AND r.admission IS DISTINCT FROM 'duplicate')""" if source else "") +
            " ORDER BY c.created_at DESC,c.id DESC", (project_id,)).fetchall()
        results = []
        for row in rows:
            state = progress(db, row)
            state["executionSteps"] = db.execute("""
                SELECT * FROM (
                    SELECT id,criterion_id,agent,branch,worktree,started_at,finished_at,exit_code,changed_files,
                           COALESCE(finished_at, GREATEST(started_at,lease_until - interval '90 seconds')) AS last_contact
                    FROM objective_run WHERE project_id=%s AND objective_id=%s
                    ORDER BY started_at DESC,id DESC LIMIT 30
                ) recent ORDER BY started_at,id
            """,(project_id,row["id"])).fetchall()
            state["milestones"] = db.execute("""
                SELECT position,title,status,detail,updated_at,checks,
                       CASE WHEN jsonb_array_length(checks)>0 THEN (
                           SELECT count(*) FROM jsonb_array_elements(checks) check_item
                           WHERE check_item->>'status'='done'
                       ) ELSE progress_completed END AS progress_completed,
                       COALESCE(NULLIF(jsonb_array_length(checks),0),progress_total) AS progress_total
                FROM objective_milestone
                WHERE objective_id=%s ORDER BY position
            """, (row["id"],)).fetchall()
            results.append({**row, "criteria":[item for item in row["criteria"] if item["id"]!="implementation"], "status": state["status"], "progress": state})
        return results


class BoardTask(BaseModel, extra="forbid"):
    project_id: UUID
    task_id: UUID
    version: int = Field(ge=1)
    run_id: UUID | None = None


class BoardAction(BaseModel, extra="forbid"):
    action: Literal["approve", "delete"]
    tasks: list[BoardTask] = Field(min_length=1, max_length=1000)


@router.get("/taskboard/business-objectives")
def business_objectives():
    with database() as db:
        return db.execute("""
            SELECT id,title,expected_outcome,priority,current_focus FROM business_objective
            ORDER BY execution_order
        """).fetchall()


@router.post("/taskboard/actions")
def taskboard_action(body: BoardAction):
    ordered = sorted(body.tasks, key=lambda item: (item.project_id, item.task_id))
    if len({item.task_id for item in ordered}) != len(ordered):
        raise HTTPException(422, "List each task once.")
    with database() as db:
        for project_id in sorted({item.project_id for item in ordered}):
            lock_project(db, project_id)
        for item in ordered:
            row = contract(db, item.project_id, item.task_id)
            hidden = db.execute("""
                SELECT reason FROM objective_presentation
                WHERE objective_id=%s AND excluded_from_board
            """, (item.task_id,)).fetchone()
            if hidden:
                if body.action == "delete" and hidden["reason"] == BOARD_DELETION:
                    continue
                raise HTTPException(409, "A selected task is no longer on Taskboard.")
            if row["version"] != item.version:
                raise HTTPException(409, "A selected task changed. Reload Taskboard and try again.")
            state = progress(db, row)
            latest = max(state["attempts"], key=lambda run: (run["started_at"], run["id"]), default=None)
            if (latest and latest["id"] or None) != item.run_id or state["active"]:
                raise HTTPException(409, "A selected task started or changed. Reload Taskboard and try again.")
            if body.action == "approve":
                if state["status"] == "done":
                    continue
                if state["status"] != "ready_for_review" or not state["verifiedScreenshots"]:
                    raise HTTPException(409, "Verify the acceptance criteria and record verified screenshot evidence before approval.")
                db.execute("""
                    INSERT INTO objective_owner_approval (objective_id,version,run_id)
                    VALUES (%s,%s,%s)
                    ON CONFLICT (objective_id,version) DO UPDATE
                    SET run_id=EXCLUDED.run_id,approved_at=now()
                """, (item.task_id, item.version, item.run_id))
                db.execute("UPDATE objective_contract SET status='done' WHERE id=%s", (item.task_id,))
            else:
                db.execute("""
                    INSERT INTO objective_presentation (objective_id,placement,reason,excluded_from_board)
                    VALUES (%s,'conversation',%s,true)
                    ON CONFLICT (objective_id) DO UPDATE
                    SET reason=EXCLUDED.reason,excluded_from_board=true,classified_at=now()
                """, (item.task_id, BOARD_DELETION))
            db.execute("INSERT INTO audit (project_id,action) VALUES (%s,%s)",
                       (item.project_id, f"taskboard:{body.action}:{item.task_id}"))
    return {"updated": [item.task_id for item in ordered]}


@router.post("/projects/{project_id}/objectives", status_code=201)
def create_objective(project_id: UUID, body: Create):
    with database() as db:
        lock_project(db, project_id)
        row = save_definition(db, project_id, uuid4(), body, body.original_request)
        return {**row, "progress": progress(db, row)}


@router.put("/projects/{project_id}/objectives/{objective_id}")
def approve_amendment(project_id: UUID, objective_id: UUID, body: Amendment):
    with database() as db:
        lock_project(db, project_id)
        row = contract(db, project_id, objective_id)
        if row["version"] != body.version or progress(db, row)["active"]:
            raise HTTPException(409, "Reload the objective and finish active assignments before approving a new version.")
        row = save_definition(db, project_id, objective_id, body)
        return {**row, "progress": progress(db, row)}


@router.post("/projects/{project_id}/objectives/{objective_id}/evidence")
def verify(project_id: UUID, objective_id: UUID, body: Evidence):
    with database() as db:
        lock_project(db, project_id)
        row = contract(db, project_id, objective_id)
        state = progress(db, row)
        attempt = next((run for run in state["attempts"] if run["criterion_id"] == body.criterion_id), None)
        if row["version"] != body.version or not attempt or attempt["fingerprint"] != body.fingerprint:
            raise HTTPException(409, "Evidence is stale. Reload the latest assignment state.")
        if any(item["criterion_id"] == body.criterion_id for item in state["active"]):
            raise HTTPException(409, "Wait for the assignment to finish.")
        criterion = next(item for item in row["criteria"] if item["id"] == body.criterion_id)
        if body.decision == "verified" and any(
            f"criterion-{dependency}" not in state["completedCriteria"] for dependency in criterion["depends_on"]
        ):
            raise HTTPException(409, "Verify the prerequisite criteria first.")
        db.execute("""
            INSERT INTO objective_checkpoint (objective_id,version,criterion_id,fingerprint,evidence,decision)
            VALUES (%s,%s,%s,%s,%s,%s)
        """, (objective_id, body.version, body.criterion_id, body.fingerprint, body.evidence, body.decision))
        return progress(db, row)


@router.post("/workspace-memory/{project_id}/governance")
def assignment(project_id: UUID, body: Assignment):
    with database() as db:
        lock_project(db, project_id)
        if body.action == "prepare":
            row = db.execute("SELECT * FROM objective_contract WHERE project_id=%s AND id=%s",
                             (project_id, body.objective_id)).fetchone()
            if not row and not (body.original_request and body.explicit_task):
                raise HTTPException(404, "Objective not found.")
            return {"criteria": row["criteria"] if row else [], "version": row["version"] if row else 1}
        existing = db.execute("""
            SELECT * FROM objective_run WHERE id=%s AND project_id=%s AND objective_id=%s
        """, (body.execution_id, project_id, body.objective_id)).fetchone()
        if body.action == "authorize":
            if (not existing or existing["finished_at"] or existing["branch"] != body.branch
                    or existing["worktree"] != body.worktree or existing["criterion_id"] != body.criterion_id):
                raise HTTPException(403, "The operation requires an active assignment for this branch and worktree.")
            row = contract(db, project_id, body.objective_id)
            criterion = next(item for item in row["criteria"] if item["id"] == existing["criterion_id"])
            if criterion["depends_on"]:
                verified = progress(db, row)["completedCriteria"]
                if any(f"criterion-{dependency}" not in verified for dependency in criterion["depends_on"]):
                    raise HTTPException(409, "Required checkpoint evidence is no longer valid.")
            return {"ok": True, "mode": criterion.get("mode", "workspace-write")}
        if body.action == "heartbeat":
            if not existing or existing["finished_at"]:
                raise HTTPException(409, "Assignment lease expired.")
            db.execute(f"""
                UPDATE objective_run SET lease_until=now()+{LEASE},changed_files=COALESCE(%s,changed_files)
                WHERE id=%s
            """, (body.changed_files, body.execution_id))
            return {"ok": True}
        if body.action == "finish":
            if not existing or body.exit_code is None or not body.fingerprint:
                raise HTTPException(409, "Unknown or incomplete assignment result.")
            if existing["finished_at"] and existing["exit_code"] != body.exit_code:
                raise HTTPException(409, "Assignment already closed.")
            row = contract(db, project_id, body.objective_id)
            db.execute("""
                UPDATE objective_run SET finished_at=now(), exit_code=%s, final_fingerprint=%s
                WHERE id=%s AND finished_at IS NULL
            """, (body.exit_code, body.fingerprint, body.execution_id))
            state = progress(db, row)
            if body.branch == existing["branch"] and body.worktree == existing["worktree"]:
                state = observe(db, row, body, state)
            return state
        return begin(db, project_id, body, existing)


def observe(db, row, body, state):
    if body.fingerprints:
        changed = [
            attempt["criterion_id"] for attempt in state["attempts"]
            if attempt["worktree"] == body.worktree and attempt["branch"] == body.branch
            and attempt["criterion_id"] in body.fingerprints
            and attempt["fingerprint"] != body.fingerprints[attempt["criterion_id"]]
        ]
        if changed:
            db.execute("""
                UPDATE objective_checkpoint SET invalidated_at=now()
                WHERE objective_id=%s AND version=%s AND criterion_id=ANY(%s) AND invalidated_at IS NULL
            """, (row["id"], row["version"], changed))
            state = progress(db, row)
    return state


def begin(db, project_id, body, existing):
    if not body.fingerprint:
        raise HTTPException(422, "Workspace state fingerprint required.")
    row = db.execute("SELECT * FROM objective_contract WHERE id=%s AND project_id=%s",
                     (body.objective_id, project_id)).fetchone()
    if not row:
        if not (body.original_request and body.explicit_task):
            raise HTTPException(404, "Explicitly create or select a task first.")
        expected = body.original_request.strip()[:2000]
        if not expected:
            raise HTTPException(422, "Objective request is empty.")
        row = save_definition(db, project_id, body.objective_id,
                              Definition.model_construct(expected_outcome=expected, criteria=[], scope=[], constraints=[], dependencies=[]),
                              body.original_request)
    if db.execute("""
        SELECT 1 FROM objective_presentation
        WHERE objective_id=%s AND excluded_from_board AND reason=%s
    """, (row["id"], BOARD_DELETION)).fetchone():
        raise HTTPException(409, "This task was deleted from Taskboard.")
    if body.version is not None and row["version"] != body.version:
        raise HTTPException(409, "Objective scope changed. Reload the approved version.")
    if existing and existing["finished_at"]:
        raise HTTPException(409, "Assignment already closed.")
    if existing:
        return {"decision": "execute", "contract": row, "progress": progress(db, row),
                "criterion": existing["criterion_id"]}
    state = progress(db, row)
    state = observe(db, row, body, state)
    target = body.criterion_id or next(iter(state["ready"]), None)
    if target is None:
        required = [item["id"] for item in row["criteria"] if item["mandatory"]]
        target = next((name for name in required if name in state["outstandingCriteria"]),
                      required[0] if required else row["criteria"][0]["id"])
    if target not in {item["id"] for item in row["criteria"]}:
        raise HTTPException(409, "Select an acceptance criterion from the current objective.")
    fingerprint = body.fingerprints.get(target, body.fingerprint)
    attempt = next((run for run in state["attempts"] if run["criterion_id"] == target), None)
    if attempt and attempt["fingerprint"] != fingerprint:
        db.execute("""
            UPDATE objective_checkpoint SET invalidated_at=now()
            WHERE objective_id=%s AND version=%s AND criterion_id=%s AND invalidated_at IS NULL
        """, (row["id"], row["version"], target))
        state = progress(db, row, (target, fingerprint))
    if target in state["completedCriteria"]:
        return {"decision": "skip", "contract": row, "progress": state, "criterion": target}
    if db.execute("""
        SELECT 1 FROM objective_run WHERE project_id=%s AND finished_at IS NULL AND
        (worktree=%s OR (branch<>'detached' AND branch=%s) OR (objective_id=%s AND criterion_id=%s))
    """, (project_id, body.worktree, body.branch, body.objective_id, target)).fetchone():
        raise HTTPException(409, "The criterion, branch or worktree is already assigned.")
    if target not in state["ready"]:
        state = progress(db, row)
        return {"decision": "block", "contract": row, "progress": state, "criterion": target}
    db.execute("""
        INSERT INTO objective_run (id,project_id,objective_id,version,criterion_id,agent,worktree,branch,head,fingerprint)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, (body.execution_id, project_id, row["id"], row["version"], target,
          body.agent, body.worktree, body.branch, body.head, fingerprint))
    return {"decision": "execute", "contract": row, "progress": progress(db, row), "criterion": target}


class TaskDisplayName(BaseModel):
    name: str = Field(min_length=1, max_length=120)


@router.patch("/projects/{project_id}/objectives/{objective_id}/display-name")
def rename_task(project_id: UUID, objective_id: UUID, body: TaskDisplayName):
    name = body.name.strip()
    from task_titles import valid_title
    if not valid_title(name):
        raise HTTPException(422, "Enter a short label without JSON, links or credentials.")
    with database() as db:
        lock_project(db, project_id)
        contract(db, project_id, objective_id)
        db.execute("UPDATE objective_contract SET display_name=%s WHERE id=%s AND project_id=%s",
                   (name, objective_id, project_id))
    return {"display_name": name}


@router.get("/task-navigation")
def task_navigation():
    # Sidebar projection excludes legacy/ambiguous objectives without deleting their history.
    with database() as db:
        return db.execute("""
            SELECT task.id, task.project_id, task.display_name AS title,
                   CASE
                     WHEN running.active THEN 'Running'
                     WHEN task.status='done' THEN 'Completed'
                     WHEN latest.exit_code=130 THEN 'Paused'
                     WHEN latest.exit_code<>0 THEN 'Failed'
                     WHEN task.status='blocked' THEN 'Blocked'
                     WHEN task.status='ready_for_review' THEN 'Ready for review'
                     WHEN latest.finished_at IS NULL AND latest.started_at IS NULL THEN 'Queued'
                     ELSE 'Active'
                   END AS status
            FROM objective_contract task
            JOIN projects project ON project.id=task.project_id AND NOT project.archived
            LEFT JOIN LATERAL (
                SELECT EXISTS(SELECT 1 FROM objective_run r WHERE r.objective_id=task.id
                    AND r.finished_at IS NULL AND r.lease_until>now()) AS active
            ) running ON true
            LEFT JOIN LATERAL (
                SELECT r.exit_code,r.started_at,r.finished_at FROM objective_run r
                WHERE r.objective_id=task.id ORDER BY r.started_at DESC LIMIT 1
            ) latest ON true
            WHERE task.sidebar_task AND NOT EXISTS (
                SELECT 1 FROM objective_presentation hidden
                WHERE hidden.objective_id=task.id AND hidden.excluded_from_board
            )
            ORDER BY task.created_at DESC,task.id
        """).fetchall()
