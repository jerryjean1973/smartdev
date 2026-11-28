"""Internal architecture review backed by local Qwen."""
import hashlib
import json
import os
from urllib.request import Request, urlopen

from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
OLLAMA = os.getenv("SMARTDEV_ARCHITECT_OLLAMA_URL", "http://ollama:11434/api/").rstrip("/") + "/"
MODEL = os.getenv("SMARTDEV_ARCHITECT_MODEL", "qwen2.5:1.5b")
CLASSES = {"ARCHITECTURE_PRESERVING", "ARCHITECTURE_IMPACTING", "CANONICAL_CHANGE"}


class ProposedChange(BaseModel):
    task: str = Field(min_length=1, max_length=3000)


class PlannedTask(BaseModel):
    task: str = Field(min_length=1, max_length=30000)
    plan: dict
    architecture_context: str = Field(default="", max_length=16000)


def model_request(path, body):
    request = Request(OLLAMA + path, data=json.dumps(body).encode(),
                      headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=120) as response:
        return json.load(response)


@app.get("/health")
def health():
    model_request("show", {"model": MODEL})
    return {"model": MODEL}


@app.post("/classify")
def classify(change: ProposedChange):
    prompt = f"""Classify the proposed change to SmartDev and BRIPe.
Return exactly one class name, from ARCHITECTURE_PRESERVING,
ARCHITECTURE_IMPACTING, CANONICAL_CHANGE.

The following are canonical invariants: DAP/VAP semantics, TTB,
authority-preserving evidence, tenant isolation and structural decisions.

Proposed change: {change.task}
"""
    response = model_request("generate", {
        "model": MODEL, "prompt": prompt, "stream": False,
        "options": {"temperature": 0, "num_ctx": 1024, "num_predict": 24, "num_thread": 4},
    })
    classification = response["response"].strip()
    return {
        "classification": classification if classification in CLASSES else "REVIEW_REQUIRED",
        "implementation_authorized": False,
    }


@app.post("/review")
def review(change: PlannedTask):
    binding = json.dumps({"task": change.task, "plan": change.plan,
                          "architecture_context": change.architecture_context},
                         sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    prompt = f"""You are the mandatory SLM Architect reviewing a planned coding task before execution.
Classify whether the complete plan preserves the existing project architecture.
Return exactly one class name: ARCHITECTURE_PRESERVING, ARCHITECTURE_IMPACTING, or CANONICAL_CHANGE.

ARCHITECTURE_PRESERVING: implementation stays within established boundaries and invariants.
ARCHITECTURE_IMPACTING: changes boundaries, components, data flow, dependencies, security, tenancy, or operations.
CANONICAL_CHANGE: changes authoritative architecture, DAP/VAP semantics, TTB, evidence authority, tenant isolation,
MIG authority, or other declared canonical decisions. Missing or ambiguous context is ARCHITECTURE_IMPACTING.

Planned task and authoritative context:
{binding}
"""
    response = model_request("generate", {
        "model": MODEL, "prompt": prompt, "stream": False,
        "options": {"temperature": 0, "num_ctx": 4096, "num_predict": 24, "num_thread": 4},
    })
    classification = response["response"].strip()
    if classification not in CLASSES:
        classification = "REVIEW_REQUIRED"
    preserving = classification == "ARCHITECTURE_PRESERVING"
    return {
        "model": MODEL,
        "classification": classification,
        "coherence_approved": preserving,
        "plan_sha256": hashlib.sha256(binding.encode()).hexdigest(),
        "constraints": [
            "Preserve existing architecture documents, decisions, tenant boundaries, and evidence authority.",
            "Keep changes within the reviewed task, worktree, project, and environment scope.",
            "Stop and request a new architecture review if the implementation plan or architectural context changes.",
        ],
    }
