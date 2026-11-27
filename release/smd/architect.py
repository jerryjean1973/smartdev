"""Internal preliminary architecture classification backed by local Qwen."""
import json
from urllib.request import Request, urlopen

from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
OLLAMA = "http://ollama:11434/api/"
MODEL = "qwen2.5:1.5b"
CLASSES = {"ARCHITECTURE_PRESERVING", "ARCHITECTURE_IMPACTING", "CANONICAL_CHANGE"}


class ProposedChange(BaseModel):
    task: str = Field(min_length=1, max_length=3000)


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
