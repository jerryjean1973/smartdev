"""Owner-approved environment bindings for the managed DEV application."""
import json
import re
from pathlib import Path
from uuid import UUID
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from psycopg.types.json import Jsonb

from store import database
from vault import call, list_secrets, owner_project, prefix

router = APIRouter()
ENV = Literal["DEV", "QA", "PREPROD", "PROD"]
SAFE = re.compile(r"[A-Z_][A-Z0-9_]{0,95}")
RESERVED = re.compile(r"^(?:CODER_|COMMAND_|GIT_|LD_|NODE_OPTIONS$|PATH$|HOME$)")


class Bindings(BaseModel):
    bindings: dict[str, str] = Field(default_factory=dict, max_length=32)


class RuntimeRequest(BaseModel):
    action: Literal["resolve", "report", "preview_policy", "deployment"]
    status: Literal["starting", "ready", "failed", "stopped"] | None = None
    phase: Literal["discovering", "preparing", "starting", "running", "degraded", "configuration_required", "failed", "not_applicable", "stopped"] | None = None
    executable: str | None = Field(None, max_length=150)
    directory: str | None = Field(None, max_length=300)
    port: int | None = Field(None, ge=1, le=65535)
    healthPath: str | None = Field(None, max_length=300)
    commit: str | None = Field(None, pattern=r"^[a-f0-9]{40,64}$")


def record(db, project_id, environment):
    return db.execute("SELECT secret_bindings,observed FROM project_runtime WHERE project_id=%s AND environment=%s",
                      (project_id, environment)).fetchone() or {"secret_bindings": {}, "observed": {}}


def deployment_observation(project_id):
    path = Path("/run/command-runtime-observations") / f"{project_id}.json"
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


@router.get("/projects/{project_id}/runtime/{environment}")
def runtime_public(project_id: UUID, environment: ENV):
    owner_project(project_id)
    with database() as db:
        row = record(db, project_id, environment)
    available = {entry["name"] for entry in list_secrets(project_id, environment)} if row["secret_bindings"] else set()
    result = {"environment":environment, "bindings":row["secret_bindings"], "configured":sorted(available),
              "observed":row["observed"]}
    if environment == "DEV":
        deployment = deployment_observation(project_id)
        if deployment is not None:
            result["deployment"] = deployment
    return result


@router.put("/projects/{project_id}/runtime/{environment}")
def save_runtime(project_id: UUID, environment: ENV, body: Bindings):
    owner_project(project_id)
    if any(not SAFE.fullmatch(target) or RESERVED.match(target) or not SAFE.fullmatch(name)
           for target, name in body.bindings.items()):
        raise HTTPException(422, "Enter valid application environment and project secret names.")
    with database() as db:
        db.execute("""INSERT INTO project_runtime(project_id,environment,secret_bindings)
                        VALUES (%s,%s,%s) ON CONFLICT(project_id,environment) DO UPDATE
                        SET secret_bindings=EXCLUDED.secret_bindings,updated_at=now()""",
                   (project_id, environment, Jsonb(body.bindings)))
        db.execute("INSERT INTO audit(project_id,action) VALUES (%s,%s)", (project_id, f"runtime:bind:{environment}"))
    return {"ok": True}


@router.post("/workspace-memory/{project_id}/preview-runtime")
def resolve_runtime(project_id: UUID, body: RuntimeRequest):
    owner_project(project_id)
    if body.action == "deployment":
        return {"deployment": deployment_observation(project_id)}
    if body.action == "preview_policy":
        with database() as db:
            policy = db.execute("SELECT mode,url FROM project_preview_url WHERE project_id=%s AND environment='DEV'",
                                (project_id,)).fetchone()
            control = db.execute("SELECT enabled,revision FROM project_dev_publication WHERE project_id=%s",
                                 (project_id,)).fetchone()
        return {"automatic": not policy or (policy["mode"] == "automatic" and not policy["url"]),
                "enabled": (control or {}).get("enabled", True), "revision": (control or {}).get("revision", 0)}
    if body.action == "report":
        observed = body.model_dump(exclude={"action"}, exclude_none=True)
        with database() as db:
            db.execute("""INSERT INTO project_runtime(project_id,environment,observed)
                   VALUES (%s,'DEV',%s) ON CONFLICT(project_id,environment) DO UPDATE
                   SET observed=(CASE WHEN EXCLUDED.observed->>'status'='ready' THEN project_runtime.observed
                                     ELSE project_runtime.observed - 'port' END) || EXCLUDED.observed,updated_at=now()""",(
                       project_id,Jsonb(observed)))
            if body.status=="ready":
                db.execute("""UPDATE project_runtime SET observed=observed || jsonb_build_object('lastSuccessfulStartup',now())
                              WHERE project_id=%s AND environment='DEV'""",(project_id,))
        return {"ok":True}
    with database() as db:
        bindings=record(db,project_id,"DEV")["secret_bindings"]
    generation=None
    import os
    if os.getenv("SMD_ASSIGNMENT_AUTHORITY"):
        from smd_owner_client import owner_json
        generation=owner_json(project_id,"portal-generation")["version"]
        incoming=owner_json(project_id,"tenant-bindings")["names"]
        if incoming or bindings:
            from store import queue
            queue().set(f"smd:runtime-values:{generation}:{project_id}","1")
        bindings={**{s["name"]:s["name"] for s in incoming if s["environment"]=="DEV" and SAFE.fullmatch(s["name"]) and not RESERVED.match(s["name"])},**bindings}
    values={}
    missing=[]
    for target,name in bindings.items():
        source=call("GET", "data/"+prefix(project_id,"DEV")+"/"+name, missing=True)
        value=(source or {}).get("data",{}).get("data",{}).get("value")
        if not isinstance(value,str) or not value.strip():
            missing.append(target)
        else:
            values[target]=value
    if bindings:
        with database() as db:
            db.execute("INSERT INTO audit(project_id,action) VALUES (%s,'runtime:resolve:DEV')",(project_id,))
    if generation is not None and owner_json(project_id,"portal-generation")["version"]!=generation:
        raise HTTPException(409,"The tenant assignment changed during runtime configuration.")
    return {"values":values if not missing else {}, "missing":missing}
