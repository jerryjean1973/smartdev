"""Authenticated Command Center access to the local Codex conversations."""
import json
from hashlib import sha256
import os
from pathlib import Path
from time import time
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from external_mcp_tasks import WorkspaceUnavailable, workspace_chat_request
from memory import SECRET_PATTERN
from store import database

router = APIRouter()
SOCKET = "/run/smartdev-cc-codex/chat.sock"


class Message(BaseModel, extra="forbid"):
    id: UUID
    prompt: str = Field(min_length=1, max_length=12000)
    images: list[str] = Field(default_factory=list, max_length=4)


class MemoryNote(BaseModel, extra="forbid"):
    note: str = Field(min_length=1, max_length=2000)


def task_context(task_id, include_context=False):
    if task_id is None:
        return None
    with database() as db:
        task = db.execute("""
            SELECT t.id,t.display_name,t.original_request,t.expected_outcome,t.status,t.version,
                   t.criteria,t.scope,t.constraints,t.dependencies,p.name AS project
            FROM objective_contract t JOIN projects p ON p.id=t.project_id AND NOT p.archived
            WHERE t.id=%s AND NOT EXISTS (
                SELECT 1 FROM objective_presentation x WHERE x.objective_id=t.id AND x.excluded_from_board)
        """, (task_id,)).fetchone()
        if not task:
            raise HTTPException(404, "Task not found.")
        if not include_context:
            return None
        task["original_request"] = task["original_request"][:12000]
        for field in ("scope", "constraints", "dependencies"):
            task[field] = [item[:500] for item in task[field][:12]]
        task["criteria"] = [
            {key: item.get(key) for key in ("id", "description", "mandatory", "depends_on")}
            for item in task["criteria"]
        ]
        task["evidence"] = db.execute("""
            SELECT criterion_id,evidence,decision FROM objective_checkpoint
            WHERE objective_id=%s AND version=%s AND invalidated_at IS NULL
            ORDER BY id DESC LIMIT 8
        """, (task_id, task["version"])).fetchall()
        task["latest_run"] = db.execute("""
            SELECT exit_code,started_at,finished_at,head FROM objective_run
            WHERE objective_id=%s ORDER BY started_at DESC,id DESC LIMIT 1
        """, (task_id,)).fetchone()
    return json.dumps(task, default=str, ensure_ascii=False)


def bridge(method, task_id=None, body=None, endpoint="chat", timeout=5):
    try:
        with httpx.Client(transport=httpx.HTTPTransport(uds=SOCKET), timeout=timeout) as client:
            response = client.request(method, "http://localhost/" + endpoint,
                                      params={"scope": str(task_id) if task_id else "main"}, json=body)
            data = response.json()
    except (httpx.HTTPError, ValueError):
        raise HTTPException(503, "The host Codex conversation is unavailable.") from None
    if not response.is_success:
        raise HTTPException(response.status_code, data.get("detail", "Host Codex request failed."))
    return data


def workspace_actions(transcript):
    actions = {}
    for line in transcript.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        item = event.get("item") or {}
        message = (kind in ("item.updated", "item.completed") and isinstance(item, dict)
                   and item.get("type") == "agent_message")
        if not message and kind not in ("smartdev.command", "smartdev.progress"):
            continue
        label = item.get("text") if message else event.get("text")
        if not isinstance(label, str) or not label.strip():
            continue
        identity = (item.get("id") or item.get("copyId")) if message else event.get("id")
        identity = identity or sha256(line.encode()).hexdigest()
        key = f"{event.get('execution') or ''}:{'message' if message else kind}:{identity}"
        actions[key] = {"id": key, "kind": "message" if message else "action", "label": label,
                        "status": ("running" if kind == "item.updated" else "completed") if message
                        else event.get("state", "completed"), "at": event.get("at")}
    return list(actions.values())


def host_history(project_id, task_id):
    root = os.getenv("SMARTDEV_TASK_WORK_HISTORY")
    if not root:
        return None
    actions = []
    running = False
    for path in sorted((Path(root) / str(project_id) / str(task_id)).glob("*.json")):
        turn = json.loads(path.read_bytes())
        actions.extend(turn["actions"])
        running |= bool(turn["running"] and time() - turn["updated"] < 300)
    return {"actions": actions, "running": running} if actions or running else None


@router.get("/projects/{project_id}/tasks/{task_id}/activity")
def task_activity(project_id: UUID, task_id: UUID, before: int | None = Query(default=None, gt=0)):
    with database() as db:
        task = db.execute("""
            SELECT p.workspace_url FROM objective_contract t
            JOIN projects p ON p.id=t.project_id AND NOT p.archived
            WHERE t.id=%s AND t.project_id=%s AND NOT EXISTS (
                SELECT 1 FROM objective_presentation x WHERE x.objective_id=t.id AND x.excluded_from_board)
        """, (task_id, project_id)).fetchone()
    if not task:
        raise HTTPException(404, "Task not found.")
    result = {}
    if task["workspace_url"]:
        try:
            workspace = workspace_chat_request(project_id, task["workspace_url"], "GET", "task", timeout=2,
                                               params={"tree": f"worktrees/task-{task_id}", "objective": str(task_id),
                                                       "activity": "1", **({"before": before} if before else {})})
            result["workspace"] = {
                "running": workspace.get("running") is True,
                "actions": workspace_actions(workspace.get("output") or ""),
                "start": workspace.get("start", 0), "end": workspace.get("end", 0),
            }
        except WorkspaceUnavailable:
            result["workspace"] = None
    if before:
        return result
    result["host"] = host_history(project_id, task_id)
    try:
        result["askCc"] = bridge("GET", task_id, endpoint="task-activity", timeout=2)
    except HTTPException:
        result["askCc"] = None
    return result


@router.get("/cc-codex/activity")
def activity():
    try:
        result = bridge("GET", endpoint="activity")
    except HTTPException:
        result = {"tasks": []}
    root = os.getenv("SMARTDEV_TASK_WORK_HISTORY")
    active = set()
    if root:
        for path in Path(root).glob("*/*/*.json"):
            if time() - path.stat().st_mtime < 300:
                turn = json.loads(path.read_bytes())
                if turn["running"] and time() - turn["updated"] < 300:
                    active.add(path.parent.name)
    return {**result, "host_tasks": sorted(active)}


@router.get("/cc-codex")
def history(task_id: UUID | None = None):
    task_context(task_id)
    return bridge("GET", task_id)


@router.post("/cc-codex", status_code=202)
def send(body: Message, task_id: UUID | None = None):
    if any(len(image) > 6_666_668 for image in body.images) or sum(map(len, body.images)) > 13_333_344:
        raise HTTPException(413, "Attach up to 5 MB per image and 10 MB total.")
    context = task_context(task_id, include_context=True)
    return bridge("POST", task_id, {**body.model_dump(mode="json"), "context": context})


@router.post("/cc-codex/memory", status_code=201)
def save_memory(body: MemoryNote, task_id: UUID | None = None):
    task_context(task_id)
    if not body.note.strip() or SECRET_PATTERN.search(body.note):
        raise HTTPException(422, "Save redacted memory without credentials.")
    return bridge("POST", task_id, body.model_dump(), endpoint="memory")
