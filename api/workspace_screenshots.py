"""Owner-readable PNG evidence captured by a project's authenticated workspace."""
from hashlib import sha256
from uuid import UUID
import re
from struct import unpack
from fastapi import APIRouter, HTTPException, Request, Response
from store import database
import os

router = APIRouter()
LIMIT = 8 * 1024 * 1024

@router.post("/workspace-memory/{project_id}/screenshots")
async def publish(project_id: UUID, request: Request):
    if request.headers.get("content-type") != "image/png":
        raise HTTPException(415, "Expected a PNG capture.")
    data = bytearray()
    async for chunk in request.stream():
        if len(chunk) + len(data) > LIMIT:
            raise HTTPException(413, "Capture exceeds 8 MiB.")
        data.extend(chunk)
    digest = sha256(data).hexdigest()
    if (len(data)<45 or not data.startswith(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
            or not data.endswith(b"\x00\x00\x00\x00IEND\xaeB\x60\x82")
            or any(n<1 or n>8192 for n in unpack(">II",data[16:24]))
            or digest!=request.headers.get("x-image-sha256")):
        raise HTTPException(422, "Invalid PNG capture or checksum.")
    with database() as db:
        db.execute("INSERT INTO workspace_screenshot(project_id,digest,content) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                   (project_id,digest,bytes(data)))
    return {"url":os.environ["PORTAL_ORIGIN"].rstrip("/")+f"/api/projects/{project_id}/screenshots/{digest}.png"}

@router.get("/projects/{project_id}/screenshots/{digest}.png")
def screenshot(project_id: UUID, digest: str):
    if not re.fullmatch("[0-9a-f]{64}",digest):
        raise HTTPException(404)
    with database() as db:
        row=db.execute("SELECT content FROM workspace_screenshot WHERE project_id=%s AND digest=%s",
                       (project_id,digest)).fetchone()
    if not row: raise HTTPException(404)
    return Response(bytes(row["content"]),media_type="image/png",
                    headers={"X-Content-Type-Options":"nosniff","Cache-Control":"private, no-store"})
