"""Command Center-only project-to-tenant administration."""
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from store import database

router = APIRouter()


class TenantAssignment(BaseModel, extra="forbid"):
    tenant_name: str = Field(max_length=80)


@router.get("/admin/project-tenants")
def project_tenants():
    with database() as db:
        rows = db.execute("SELECT project_id, tenant_name FROM command_center_project_tenant").fetchall()
    return {str(row["project_id"]): row["tenant_name"] for row in rows}


@router.put("/admin/project-tenants/{project_id}")
def assign_project_tenant(project_id: UUID, body: TenantAssignment):
    name = body.tenant_name.strip()
    with database() as db:
        row = db.execute("""
            SELECT id FROM projects WHERE id=%s AND NOT EXISTS (
                SELECT 1 FROM acceptance_fixture WHERE project_id=projects.id
            ) FOR UPDATE
        """, (project_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Project not available.")
        if name:
            db.execute("""
                INSERT INTO command_center_project_tenant (project_id, tenant_name)
                VALUES (%s, %s) ON CONFLICT (project_id)
                DO UPDATE SET tenant_name=EXCLUDED.tenant_name
            """, (project_id, name))
        else:
            db.execute("DELETE FROM command_center_project_tenant WHERE project_id=%s", (project_id,))
        db.execute("INSERT INTO audit (project_id, action) VALUES (%s, %s)",
                   (project_id, "command-center:tenant-assignment"))
    return {"tenant_name": name}
