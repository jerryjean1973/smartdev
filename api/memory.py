import hashlib
import json
import os
import re
from uuid import UUID, uuid4
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

from store import database

router = APIRouter()
REPO_SOURCE = re.compile(r"repo:([^#\x00-\x1f]+)#sha256=([a-f0-9]{64})")


def repo_source(provenance):
    if not provenance.startswith("repo:"):
        return None
    matched = REPO_SOURCE.fullmatch(provenance)
    if not matched:
        raise ValueError("Use repo:relative/path#sha256=<64 lowercase hex digits> for a repository source.")
    path, digest = matched.groups()
    if any(part in {"", ".", "..", ".git"} for part in path.split("/")) or "\\" in path:
        raise ValueError("Use a repository-relative source path.")
    return {"path": path, "sha256": digest}

SECRET_PATTERN = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(?:github_pat_|gh[pousr]_|sk-(?:proj-)?)[A-Za-z0-9_-]{12,}"
    r"|(?:password|api[_-]?key|secret|token)\s*[:=]\s*[\w+/=-]{8,}",
    re.IGNORECASE,
)

class Note(BaseModel):
    scope: Literal["project", "agent", "shared"] = "project"
    title: str = Field(min_length=1, max_length=160)
    body: str = Field(min_length=1, max_length=4000)
    provenance: str = Field(min_length=1, max_length=300)
    confidence: float = Field(default=0.5, ge=0, le=1)
    state: Literal["proposed", "implemented", "tested", "verified", "failed", "reverted", "invalidated"] = "proposed"
    agent_id: UUID | None = None
    supersedes: UUID | None = None

    @model_validator(mode="after")
    def validate_note(self):
        values = (self.title, self.body, self.provenance)
        if any(not value.strip() or SECRET_PATTERN.search(value) for value in values):
            raise ValueError("Use nonempty, redacted note text and evidence")
        repo_source(self.provenance)
        if self.scope == "agent" and self.agent_id is None:
            raise ValueError("Agent notes require an agent ID")
        return self

def embedding(text):
    key = os.getenv("MEMORY_EMBEDDING_API_KEY")
    if not key or not text or SECRET_PATTERN.search(text):
        return None
    try:
        response = httpx.post("https://api.openai.com/v1/embeddings", headers={"Authorization": f"Bearer {key}"},
                              json={"input": text, "model": "text-embedding-3-small"}, timeout=8)
        response.raise_for_status()
        return json.dumps(response.json()["data"][0]["embedding"], allow_nan=False)
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
        return None

@router.get("/projects/{project_id}/memory")
def search(project_id: UUID, q: str = Query("", max_length=300), shared: bool = False):
    return find_notes(project_id, q, shared)

def find_notes(project_id: UUID, q: str, shared: bool, *, context_only: bool = False):
    vector = embedding(q)
    with database() as db:
        return db.execute("""
            WITH query AS (SELECT %s::text AS term, %s::vector AS vec)
            SELECT m.id, m.project_id, m.scope, m.title, m.body, m.provenance, m.confidence,
                   m.state, m.agent_id, m.supersedes, m.created_at,
                   EXISTS (SELECT 1 FROM memory newer WHERE newer.supersedes=m.id) AS superseded
            FROM memory m JOIN projects source ON source.id=m.project_id CROSS JOIN query
            WHERE (m.project_id=%s OR (%s AND NOT source.archived AND m.scope='shared' AND m.state='verified'))
              AND (m.project_id=%s OR NOT EXISTS (SELECT 1 FROM memory newer WHERE newer.supersedes=m.id))
              AND (NOT %s OR (m.scope<>'agent' AND m.state NOT IN ('invalidated','reverted')
                   AND NOT EXISTS (SELECT 1 FROM memory newer WHERE newer.supersedes=m.id)))
              AND (query.term='' OR position(lower(query.term) in lower(m.title || ' ' || m.body || ' ' || m.provenance)) > 0
                   OR to_tsvector('simple', m.title || ' ' || m.body || ' ' || m.provenance) @@ websearch_to_tsquery('simple', query.term)
                   OR (query.vec IS NOT NULL AND m.embedding <=> query.vec < 0.6))
            ORDER BY
              (CASE WHEN query.term<>'' AND position(lower(query.term) in lower(m.title || ' ' || m.body || ' ' || m.provenance)) > 0 THEN 4 ELSE 0 END)
              + COALESCE(ts_rank(to_tsvector('simple', m.title || ' ' || m.body || ' ' || m.provenance),
                                 websearch_to_tsquery('simple', NULLIF(query.term,''))), 0)
              + COALESCE(1 - (m.embedding <=> query.vec), 0) + m.confidence DESC,
              m.created_at DESC
            LIMIT 50
        """, (q.strip(), vector, project_id, shared, project_id, context_only)).fetchall()

@router.post("/projects/{project_id}/memory", status_code=201)
def capture(project_id: UUID, note: Note):
    from psycopg.errors import UniqueViolation
    fingerprint = hashlib.sha256(json.dumps(
        [str(project_id), note.model_dump(mode="json")], sort_keys=True).encode()).hexdigest()
    vector = embedding(f"{note.title}\n{note.body}")
    try:
        with database() as db:
            if not db.execute("SELECT id FROM projects WHERE id=%s", (project_id,)).fetchone():
                raise HTTPException(404, "Project not found")
            if note.supersedes and not db.execute(
                "SELECT id FROM memory WHERE id=%s AND project_id=%s", (note.supersedes, project_id)
            ).fetchone():
                raise HTTPException(404, "Previous note not found in this project")
            saved = db.execute("""
                INSERT INTO memory (id,project_id,scope,title,body,provenance,confidence,state,
                                    agent_id,supersedes,fingerprint,embedding)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::vector)
                ON CONFLICT (fingerprint) DO UPDATE SET fingerprint=EXCLUDED.fingerprint RETURNING id
            """, (uuid4(), project_id, note.scope, note.title, note.body, note.provenance, note.confidence,
                  note.state, note.agent_id, note.supersedes, fingerprint, vector)).fetchone()
            return saved
    except UniqueViolation:
        raise HTTPException(409, "This note already has a newer version. Review its evidence before editing.")

class WorkspaceQuery(BaseModel):
    terms: str = Field(min_length=1, max_length=300)
    include_shared: bool = False
    execution_id: UUID
    branch: str = Field(min_length=1, max_length=250)
    head: str | None = Field(default=None, pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    changed_files: list[str] = Field(default_factory=list, max_length=100)
    exit_code: int | None = Field(default=None, ge=-255, le=255)

@router.post("/workspace-memory/{project_id}")
def workspace_context(project_id: UUID, body: WorkspaceQuery):
    if any(not name or len(name) > 1024 or "\x00" in name for name in body.changed_files):
        raise HTTPException(422, "Use valid project-relative file names.")
    # Workspace credentials cannot publish or approve notes.
    with database() as db:
        active = db.execute("SELECT id FROM projects WHERE id=%s AND NOT archived", (project_id,)).fetchone()
    if not active:
        raise HTTPException(404, "Active project required")
    if body.exit_code is not None:
        with database() as db:
            updated = db.execute("""
                UPDATE workspace_execution SET head_after=%s, exit_code=%s, changed_files=%s, finished_at=now()
                WHERE id=%s AND project_id=%s AND finished_at IS NULL RETURNING id
            """, (body.head, body.exit_code, body.changed_files, body.execution_id, project_id)).fetchone()
        if not updated:
            raise HTTPException(409, "Execution already closed or unknown")
        return {"ok": True}
    if not body.terms.strip() or SECRET_PATTERN.search(body.terms + "\n" + body.branch):
        raise HTTPException(422, "Use nonempty, redacted search terms")
    notes = find_notes(project_id, body.terms, body.include_shared, context_only=True)
    with database() as db:
        recent = db.execute("""
            SELECT m.*, false AS superseded FROM memory m
            WHERE m.project_id=%s AND m.scope<>'agent'
              AND m.state=ANY(%s)
              AND NOT EXISTS (SELECT 1 FROM memory newer WHERE newer.supersedes=m.id)
            ORDER BY m.created_at DESC LIMIT 3
        """, (project_id, ["verified", "proposed"])).fetchall()
    notes.extend(recent)
    context = []
    note_ids = []
    sources = {}
    remaining = 6000
    for note in notes:
        if note["id"] in note_ids or note["scope"] == "agent" or note["superseded"] or note["state"] in ("invalidated", "reverted"):
            continue
        try:
            source = repo_source(note["provenance"])
        except ValueError:
            continue
        if source and note["project_id"] != project_id:
            continue
        kind = "Source-linked summary" if source else "Historical note, unverified against the current checkout"
        excerpt = f'{kind}: {note["title"]} [{note["state"]}; {note["created_at"]}; {note["provenance"]}]\n{note["body"]}'
        if len(excerpt) <= remaining:
            if source:
                sources[str(len(context))] = source
            context.append(excerpt)
            note_ids.append(note["id"])
            remaining -= len(excerpt)
        if len(context) >= 8:
            break
    with database() as db:
        row = db.execute("""
            INSERT INTO workspace_execution (id,project_id,branch,head_before,context_note_ids)
            VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id
        """, (body.execution_id, project_id, body.branch, body.head, note_ids)).fetchone()
    if not row:
        raise HTTPException(409, "Execution already registered")
    return {"notes": context, "note_sources": sources}

@router.get("/projects/{project_id}/executions")
def executions(project_id: UUID):
    with database() as db:
        return db.execute("""
            SELECT * FROM workspace_execution WHERE project_id=%s ORDER BY started_at DESC LIMIT 30
        """, (project_id,)).fetchall()
