"""Project-owned browser preview URLs. SmartDev never fetches saved targets."""
import os
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, HttpUrl

from store import database

router = APIRouter()
ENV = Literal["DEV", "QA", "PREPROD", "PROD"]


class PreviewConfig(BaseModel):
    url: HttpUrl | None = None
    mode: Literal["url", "automatic"] = "automatic"


def project(db, project_id):
    if not db.execute("SELECT id FROM projects WHERE id=%s AND NOT archived", (project_id,)).fetchone():
        raise HTTPException(404, "Project not available.")


@router.get("/projects/{project_id}/preview")
def previews(project_id: UUID):
    with database() as db:
        project(db, project_id)
        rows = db.execute("SELECT environment,mode,url FROM project_preview_url WHERE project_id=%s",
                          (project_id,)).fetchall()
    return {row["environment"]:{"mode":row["mode"],"url":row["url"]} for row in rows}


@router.put("/projects/{project_id}/preview/{environment}")
def save_preview(project_id: UUID, environment: ENV, body: PreviewConfig):
    url = str(body.url) if body.url else None
    if body.url:
        target = urlsplit(url)
        portal = urlsplit(os.environ["PORTAL_ORIGIN"])
        if target.scheme != "https" or body.url.username is not None or body.url.password is not None:
            raise HTTPException(422,"Use an HTTPS application URL without embedded credentials.")
        if target.hostname == portal.hostname and (target.port or 443) == (portal.port or 443):
            raise HTTPException(422,"Use an application origin separate from the SmartDev portal.")
    with database() as db:
        project(db, project_id)
        db.execute("""INSERT INTO project_preview_url(project_id,environment,mode,url) VALUES (%s,%s,%s,%s)
            ON CONFLICT(project_id,environment) DO UPDATE SET mode=EXCLUDED.mode,url=EXCLUDED.url""",
                   (project_id,environment,body.mode,url))
        db.execute("INSERT INTO audit(project_id,action) VALUES (%s,%s)",(project_id,f"preview:configure:{environment}"))
    return {"mode":body.mode,"url":url}
