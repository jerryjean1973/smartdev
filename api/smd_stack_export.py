"""Offer an owner-authorized, offline SmartDev platform export."""

import os
from pathlib import Path
import re
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from store import database

router = APIRouter()


@router.get("/admin/projects/{project_id}/smd-export")
def export_stack(project_id: UUID):
    with database() as db:
        project = db.execute("""
            SELECT name FROM projects WHERE id=%s AND NOT archived AND NOT EXISTS (
                SELECT 1 FROM acceptance_fixture WHERE project_id=projects.id
            )
        """, (project_id,)).fetchone()
    if not project:
        raise HTTPException(404, "Project not available.")
    path = os.getenv("SMARTDEV_SMD_EXPORT_BUNDLE", "")
    revision = os.getenv("SMARTDEV_SMD_EXPORT_REVISION", "")
    checksum = os.getenv("SMARTDEV_SMD_EXPORT_SHA256", "")
    if (not path or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", revision)
            or not re.fullmatch(r"[a-f0-9]{64}", checksum)):
        raise HTTPException(503, "The offline SmartDev stack export is unavailable.")
    bundle = Path(path)
    if not bundle.is_file() or bundle.is_symlink():
        raise HTTPException(503, "The offline SmartDev stack export is unavailable.")
    slug = re.sub(r"[^A-Za-z0-9]+", "-", project["name"]).strip("-")[:48] or "project"
    return FileResponse(bundle, filename=f"{slug}-smartdev-{revision}.tar",
                        media_type="application/x-tar", headers={
                            "Cache-Control": "private, no-store",
                            "X-Checksum-SHA256": checksum,
                        })
