"""Mandatory SLM Architect review for every managed coding-agent execution."""
import hashlib
import json
import os
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator
from psycopg.types.json import Jsonb

from codex_access import RunAccess, active_run
from store import database

router = APIRouter()
CLASSES = {"ARCHITECTURE_PRESERVING", "ARCHITECTURE_IMPACTING", "CANONICAL_CHANGE", "REVIEW_REQUIRED"}


class ArchitectReviewRequest(BaseModel, extra="forbid"):
    execution_id: UUID
    objective_id: UUID | None = None
    conversation_id: UUID | None = None
    instruction: str = Field(min_length=1, max_length=30000)
    plan: dict
    git_head: str | None = Field(default=None, pattern=r"^[a-f0-9]{40,64}$")
    branch: str = Field(min_length=1, max_length=255)
    architecture_context: str = Field(default="", max_length=16000)

    @model_validator(mode="after")
    def one_execution_scope(self):
        if (self.objective_id is None) == (self.conversation_id is None):
            raise ValueError("Choose one task or Main conversation scope.")
        if len(json.dumps(self.plan, ensure_ascii=False)) > 30000:
            raise ValueError("Architecture plan is too large.")
        return self


def response(row):
    result = {
        "review_id": str(row["id"]), "execution_id": str(row["execution_id"]),
        "model": row["model"], "classification": row["classification"],
        "coherence_approved": row["coherence_approved"],
        "constraints": row["constraints"], "plan_sha256": row["plan_sha256"],
        "reviewed_at": row["reviewed_at"].isoformat(),
    }
    if not row["coherence_approved"]:
        raise HTTPException(409, "SLM Architect blocked execution as " + row["classification"] +
                            ". Review receipt: " + str(row["id"]) +
                            ". Revise the plan or record an owner-approved architecture decision before retrying.")
    return result


@router.post("/workspace-memory/{project_id}/architect-review")
def review(project_id: UUID, body: ArchitectReviewRequest):
    active_run(project_id, RunAccess(execution_id=body.execution_id,
                                     objective_id=body.objective_id,
                                     conversation_id=body.conversation_id,
                                     environment="DEV"))
    binding = {"instruction": body.instruction, "plan": body.plan,
               "git_head": body.git_head, "branch": body.branch,
               "architecture_context": body.architecture_context}
    digest = hashlib.sha256(json.dumps(binding, sort_keys=True, separators=(",", ":"),
                                       ensure_ascii=False).encode()).hexdigest()
    with database() as db:
        saved = db.execute("""SELECT * FROM architect_task_review
            WHERE project_id=%s AND execution_id=%s""", (project_id, body.execution_id)).fetchone()
    if saved:
        if saved["plan_sha256"] != digest:
            raise HTTPException(409, "The execution already has an Architect review for a different plan.")
        return response(saved)
    url = os.getenv("SMARTDEV_ARCHITECT_URL", "").rstrip("/")
    if not url:
        raise HTTPException(503, "SLM Architect review is not configured; coding-agent execution is blocked.")
    try:
        with httpx.Client(timeout=125, trust_env=False) as client:
            reviewed = client.post(url + "/review", json={
                "task": body.instruction, "plan": body.plan,
                "architecture_context": body.architecture_context,
            })
            reviewed.raise_for_status()
            decision = reviewed.json()
    except (httpx.HTTPError, ValueError):
        raise HTTPException(503, "SLM Architect review is unavailable; coding-agent execution is blocked.") from None
    classification = decision.get("classification")
    constraints = decision.get("constraints")
    if (classification not in CLASSES or not isinstance(constraints, list)
            or not constraints or any(not isinstance(item, str) or not item for item in constraints)
            or decision.get("coherence_approved") is not (classification == "ARCHITECTURE_PRESERVING")):
        raise HTTPException(503, "SLM Architect returned an invalid review; coding-agent execution is blocked.")
    with database() as db:
        saved = db.execute("""INSERT INTO architect_task_review
            (id,project_id,execution_id,objective_id,conversation_id,plan_sha256,git_head,
             model,classification,coherence_approved,constraints)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
            (uuid4(), project_id, body.execution_id, body.objective_id, body.conversation_id,
             digest, body.git_head, str(decision.get("model") or "unknown")[:200], classification,
             classification == "ARCHITECTURE_PRESERVING", Jsonb(constraints))).fetchone()
    return response(saved)
