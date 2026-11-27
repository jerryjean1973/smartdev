"""Verify ownership of hostnames reserved by the deployment owner."""
import secrets
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from dns_inspection import PREFIX, txt_matches, verification_hostname, verification_txt_answer
from publishing import project
from store import database

router = APIRouter(prefix="/projects/{project_id}/domains")
FIELDS = "hostname, challenge, created_at, checked_at, verified_at"


class DomainRequest(BaseModel):
    hostname: str = Field(min_length=1, max_length=254)


def validate(value):
    try:
        return verification_hostname(value)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get("")
def list_domains(project_id: UUID):
    with database() as db:
        project(db, project_id)
        return db.execute(f"SELECT {FIELDS} FROM project_domain WHERE project_id=%s ORDER BY hostname",
                          (project_id,)).fetchall()


@router.post("", status_code=201)
def add_domain(project_id: UUID, body: DomainRequest):
    host = validate(body.hostname)
    with database() as db:
        project(db, project_id)
        from smd_preview_domains import managed_hosts
        from smd_owner_client import owner_json
        managed=str(project_id) in managed_hosts()
        challenge=owner_json(project_id,"domain-challenge",{"hostname":host})["challenge"] if managed else secrets.token_urlsafe(24)
        existing = db.execute(f"""
            INSERT INTO project_domain(hostname,project_id,challenge) VALUES (%s,%s,%s)
            ON CONFLICT(hostname) DO NOTHING RETURNING {FIELDS}
        """, (host, project_id, challenge)).fetchone()
        if existing:
            db.execute("INSERT INTO audit(project_id,action) VALUES (%s,%s)", (project_id, "domain:reserve:" + host))
            return existing
        existing = db.execute(f"SELECT {FIELDS} FROM project_domain WHERE hostname=%s AND project_id=%s",
                              (host, project_id)).fetchone()
        if not existing:
            raise HTTPException(409, "This hostname is already reserved.")
        if managed and existing["challenge"]!=challenge:
            existing=db.execute(f"""UPDATE project_domain SET challenge=%s,verified_at=NULL WHERE project_id=%s AND hostname=%s RETURNING {FIELDS}""",
                (challenge,project_id,host)).fetchone()
        return existing


@router.post("/{hostname}/verify")
def verify_domain(project_id: UUID, hostname: str):
    host = validate(hostname)
    with database() as db:
        project(db, project_id)
        row = db.execute("SELECT challenge FROM project_domain WHERE hostname=%s AND project_id=%s",
                         (host, project_id)).fetchone()
    if not row:
        raise HTTPException(404, "Domain not found.")
    name = PREFIX + host
    try:
        confirmed = txt_matches(verification_txt_answer(name), name, row["challenge"])
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from None
    with database() as db:
        project(db, project_id)
        updated = db.execute(f"""
            UPDATE project_domain SET checked_at=now(),
                verified_at=CASE WHEN %s THEN now() ELSE NULL END
            WHERE hostname=%s AND project_id=%s AND challenge=%s RETURNING {FIELDS}
        """, (confirmed, host, project_id, row["challenge"])).fetchone()
        if not updated:
            raise HTTPException(409, "The reservation changed during verification.")
        db.execute("INSERT INTO audit(project_id,action) VALUES (%s,%s)",
                   (project_id, ("domain:verified:" if confirmed else "domain:unverified:") + host))
        from smd_preview_domains import domain_candidate
        domain_candidate(db,project_id,host,row["challenge"],confirmed)
        return updated


@router.delete("/{hostname}", status_code=204)
def remove_domain(project_id: UUID, hostname: str):
    host = validate(hostname)
    with database() as db:
        project(db, project_id)
        result = db.execute("DELETE FROM project_domain WHERE project_id=%s AND hostname=%s",
                            (project_id, host))
        if result.rowcount:
            db.execute("INSERT INTO audit(project_id,action) VALUES (%s,%s)", (project_id, "domain:release:" + host))
