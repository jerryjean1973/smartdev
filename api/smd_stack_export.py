"""Offer an owner-authorized, offline SmartDev platform export."""

import json
import os
from pathlib import Path
import re
import tarfile
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from smd_export_policy import file_identity, verified_layout, clean_proof, stream_range

from store import database

router = APIRouter()


def configured_export():
    path = os.getenv("SMARTDEV_SMD_EXPORT_BUNDLE", "")
    revision = os.getenv("SMARTDEV_SMD_EXPORT_REVISION", "")
    checksum = os.getenv("SMARTDEV_SMD_EXPORT_SHA256", "")
    if (not path or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", revision)
            or not re.fullmatch(r"[a-f0-9]{64}", checksum)):
        return None
    bundle = Path(path)
    if not bundle.is_file() or bundle.is_symlink():
        return None
    try:
        identity=file_identity(bundle.stat())
        zone=os.getenv("SMARTDEV_SMD_EXPORT_DNS_ZONE","smd.youkandoit.com")
        proof=clean_proof(bundle.with_name("clean-"+checksum+".json"),checksum)
        parts,digest,size=verified_layout(str(bundle),identity,checksum,zone,json.dumps(proof,sort_keys=True))
    except (OSError,ValueError,KeyError,TypeError,UnicodeError,tarfile.TarError):
        return None
    return bundle,revision,digest,parts,identity,size,proof


@router.get("/admin/docker-export")
def docker_export_metadata():
    export = configured_export()
    if not export:
        return {"available": False}
    bundle, revision, checksum, parts, identity, size, proof = export
    return {"available": True, "revision": revision, "size_bytes": size, "attestation":proof, "configuration":"clean_release", "managed_dns":True, "sha256": checksum}


@router.get("/admin/projects/{project_id}/smd-export")
def export_stack(project_id: UUID,request: Request):
    with database() as db:
        project = db.execute("""
            SELECT name FROM projects WHERE id=%s AND NOT archived AND NOT EXISTS (
                SELECT 1 FROM acceptance_fixture WHERE project_id=projects.id
            )
        """, (project_id,)).fetchone()
    if not project:
        raise HTTPException(404, "Project not available.")
    export = configured_export()
    if not export:
        raise HTTPException(503, "The offline SmartDev stack export is unavailable.")
    bundle, revision, checksum, parts, identity, size, proof = export
    slug = re.sub(r"[^A-Za-z0-9]+", "-", project["name"]).strip("-")[:48] or "project"
    start,end=0,size-1
    headers={"Cache-Control":"private, no-store","X-Checksum-SHA256":checksum,"Accept-Ranges":"bytes",
             "ETag":'"'+checksum+'"',"X-SmartDev-Export-Configuration":"clean-release",
             "Content-Disposition":f'attachment; filename="{slug}-smartdev-{revision}.tar"'}
    partial=request.headers.get("range")
    if partial and request.headers.get("if-range",headers["ETag"])==headers["ETag"]:
        m=re.fullmatch(r"bytes=(\d*)-(\d*)",partial)
        if not m or not any(m.groups()) or max(map(len,m.groups()))>20:
            raise HTTPException(416,headers={"Content-Range":f"bytes */{size}"})
        if m[1]:
            start=int(m[1])
            end=min(int(m[2]),end) if m[2] else end
        else:
            start=max(0,size-int(m[2]))
        if start>end:
            raise HTTPException(416,headers={"Content-Range":f"bytes */{size}"})
        headers["Content-Range"]=f"bytes {start}-{end}/{size}"
    headers["Content-Length"]=str(end-start+1)
    source=bundle.open("rb")
    if file_identity(os.fstat(source.fileno()))!=identity:
        source.close()
        raise HTTPException(503,"Offline release changed during validation.")
    def chunks():
        with source:
            yield from stream_range(parts,source,start,end)
    return StreamingResponse(chunks(),status_code=206 if "Content-Range" in headers else 200,
                             media_type="application/x-tar",headers=headers)
