"""Owner-selected installations dispatched through the existing machine channel."""
import json
import os
from ipaddress import ip_address
import re
from pathlib import Path
from hashlib import sha256
from typing import Annotated, Literal
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import Header, HTTPException, Response
from pydantic import BaseModel, Field
from psycopg.types.json import Jsonb

from installation_bundle import bundle
from installation_destination import DESTINATION_LABELS, Destination
from installation_release import payload, verified_installation_plan, verify_operation, verify_receipt
from operator_dispatch import machine_project, router
from store import database


class Selection(BaseModel, extra="forbid", strict=True):
    project_id: Annotated[UUID, Field(strict=False)]
    public_git_read: bool = False
    local_mig: bool = False
    local_bripe: bool = False


class DefaultImage(BaseModel, extra="forbid", strict=True):
    version: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")
    digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    vultr_snapshot_id: Annotated[UUID, Field(strict=False)]


class DefaultImageUnavailable(ValueError):
    pass


def approved_handoff(selection, config):
    project = str(selection.project_id)
    enrollment = config["projects"][project]
    request = enrollment["request"]
    grant = request["grant"]
    plan = verified_installation_plan(project, config["zone"], request["release"],
                                      request["release_signature"], config["release_public_key"])
    registry = config["release_registry"]
    if (not re.fullmatch(r"[a-z0-9.-]+(?::[0-9]+)?", registry)
            or not all(image.startswith(registry + "/") for image in plan["images"].values())):
        raise ValueError("Images must come from the Command Center release registry")
    bripe_ip = None
    if not selection.local_bripe:
        address = ip_address(config["public_bripe_ip"])
        if not address.is_global or address.is_multicast:
            raise ValueError("Public BRIPe requires a public unicast IP")
        bripe_ip = str(address)
    plan["policy"] = {
        "public_git_read": selection.public_git_read,
        "local_mig": selection.local_mig,
        "local_bripe": selection.local_bripe,
        "public_git_write": False,
        "source_authority": "command-center",
        "release_registry": registry,
        "mig_authority": "central",
        "public_bripe_ip": bripe_ip,
    }
    plan["destination"] = Destination.validate_python(
        enrollment["destination"]
    ).model_dump(exclude_unset=True)
    if plan["destination"]["kind"] == "vultr":
        try:
            image = DefaultImage.model_validate(request["release"].get("default_host_image"))
        except ValueError as exc:
            raise DefaultImageUnavailable from exc
        plan["default_host_image"] = image.model_dump(mode="json")
    if grant["operation"] != "install":
        raise ValueError("Expected an install grant")
    verify_operation(grant, request["grant_signature"], config["mig_public_key"], plan,
                     enrollment["id"], None)
    key = enrollment["host_public_key"]
    Ed25519PublicKey.from_public_bytes(bytes.fromhex(key))
    return {
        "release": request["release"],
        "release_signature": request["release_signature"],
        "grant": grant,
        "grant_signature": request["grant_signature"],
        "plan": plan,
    }, key


def configuration():
    return json.loads(os.getenv("SMARTDEV_HOST_INSTALLATION_CONFIG", "{}"))


@router.get("/host-installations")
def installations():
    projects = configuration().get("projects", {})
    with database() as db:
        rows = db.execute("""
            SELECT p.id, p.name, h.operation_id,
                CASE WHEN h.result IS NOT NULL THEN h.result->'receipt'->>'outcome'
                     WHEN h.expires_at<=now() AND h.claimed_at IS NOT NULL THEN 'unconfirmed'
                     WHEN h.expires_at<=now() THEN 'expired'
                     WHEN h.claimed_at IS NOT NULL THEN 'claimed'
                     WHEN h.operation_id IS NOT NULL THEN 'queued' END AS status,
                h.result, h.handoff->'plan'->'destination'->>'kind' AS destination_kind
            FROM projects p LEFT JOIN host_installation h ON h.project_id=p.id
            WHERE NOT p.archived AND (p.id=ANY(%s::uuid[]) OR h.project_id IS NOT NULL)
              AND NOT EXISTS (SELECT 1 FROM acceptance_fixture f WHERE f.project_id=p.id)
            ORDER BY p.name
        """, (list(projects),)).fetchall()

    for row in rows:
        kind = row.pop("destination_kind")
        if not kind:
            kind = ("vultr" if row["operation_id"] else
                    projects.get(str(row["id"]), {}).get("destination", {}).get("kind"))
        row["destination_label"] = DESTINATION_LABELS.get(kind)
    return rows


def require_main_project(db, project, *, lock=False):
    query = """
        SELECT id FROM projects p WHERE id=%s AND NOT archived
        AND NOT EXISTS (SELECT 1 FROM acceptance_fixture f WHERE f.project_id=p.id)
    """
    if not db.execute(query + (" FOR UPDATE" if lock else ""), (project,)).fetchone():
        raise HTTPException(409, "A selected main project is unavailable.")


@router.post("/host-installations/preflight")
def preflight(selection: Selection):
    with database() as db:
        require_main_project(db, selection.project_id)
    config = configuration()
    try:
        handoff, _ = approved_handoff(selection, config)
    except DefaultImageUnavailable:
        raise HTTPException(409, "The signed release lacks a canonical SMD host image identity.")
    except (ValueError, KeyError, TypeError, InvalidSignature):
        raise HTTPException(409, "No valid signed installation plan covers this project and these options.")
    plan = handoff["plan"]
    return {
        "plan_sha256": sha256(payload("plan", plan)).hexdigest(),
        "destination_label": DESTINATION_LABELS[plan["destination"]["kind"]],
        "downloadable": plan["destination"]["kind"] == "docker",
        "destination": {key: value for key, value in plan["destination"].items()
                        if key not in {"kind", "delegation_ref", "access_ref", "identity_ref"}},
        "can_dispatch": False,
        "portal_origin": plan["portal_origin"],
        "default_host_image": plan.get("default_host_image"),
        "expires_at": handoff["grant"]["expires_at"],
    }


@router.post("/host-installations/download")
def download(selection: Selection):
    with database() as db:
        require_main_project(db, selection.project_id)
    try:
        config = configuration()
        handoff, _ = approved_handoff(selection, config)
        if handoff["plan"]["destination"]["kind"] != "docker":
            raise ValueError("Expected Docker destination")
        data = bundle(handoff, Path(config["artifact_directory"]))
    except (OSError, KeyError, TypeError, ValueError, InvalidSignature):
        raise HTTPException(409, "No verified Docker package is available for these options.")
    return Response(data, media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="smartdev-{selection.project_id}.zip"',
        "Cache-Control": "no-store",
    })


@router.post("/host-installations")
def enqueue():
    raise HTTPException(503, "Host provisioning is disabled. Read-only preflight is available.")


def installation_operator(authorization):
    project = machine_project(authorization)
    if str(project) != configuration().get("operator_project_id"):
        raise HTTPException(403, "Operator is unavailable for host installations.")


@router.post("/operator-machine/install-poll")
def poll_installation(authorization: str = Header(default="")):
    installation_operator(authorization)
    # Hold all queued installs while the provider executor lacks a verified spend gate.
    return {}


class Receipt(BaseModel, extra="forbid"):
    operation_id: UUID
    operation_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    outcome: Literal["installed", "failed", "unconfirmed"]
    vultr_instance_id: UUID | None = None
    resource_id: str | None = Field(default=None, min_length=1, max_length=512)
    observed_release_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    portal_ready: bool = False
    vault_ready: bool = False
    observed_host_image: DefaultImage | None = None


class Result(BaseModel, extra="forbid"):
    receipt: Receipt
    signature: str = Field(pattern=r"^[0-9a-f]{128}$")


@router.post("/operator-machine/install-result")
def installation_result(body: Result, authorization: str = Header(default="")):
    installation_operator(authorization)
    result = body.model_dump(mode="json", exclude_unset=True)
    with database() as db:
        row = db.execute("""
            SELECT * FROM host_installation WHERE operation_id=%s AND claimed_at IS NOT NULL FOR UPDATE
        """, (body.receipt.operation_id,)).fetchone()
        if not row:
            raise HTTPException(409, "Installation claim unavailable.")
        try:
            verify_receipt(result["receipt"], body.signature, row["host_public_key"],
                           row["handoff"]["grant"])
        except (ValueError, InvalidSignature):
            raise HTTPException(403, "Invalid enrolled host signature.")
        receipt = body.receipt
        kind = row["handoff"]["plan"].get("destination", {}).get("kind", "vultr")
        resource = receipt.vultr_instance_id if kind == "vultr" else receipt.resource_id
        if receipt.outcome == "installed" and kind == "vultr":
            expected = row["handoff"]["plan"].get("default_host_image")
            if not expected or result["receipt"].get("observed_host_image") != expected:
                raise HTTPException(422, "Observed host image does not match the approved canonical image.")
        if receipt.outcome == "installed" and (
            not resource or not receipt.portal_ready or not receipt.vault_ready
            or receipt.observed_release_sha256 != row["handoff"]["plan"]["release_sha256"]
        ):
            raise HTTPException(422, "Successful installation requires the resource, release, portal and vault observations.")
        if row["result"] is not None and row["result"] != result:
            raise HTTPException(409, "Installation already has a signed receipt.")
        db.execute("UPDATE host_installation SET result=%s WHERE operation_id=%s",
                   (Jsonb(result), receipt.operation_id))
    return {"operation_id": receipt.operation_id, "status": receipt.outcome}
