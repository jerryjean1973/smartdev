"""Serve the configured SMD release files to an authorized bootstrap."""

import os
import re
from hashlib import file_digest, sha256
from pathlib import Path
from secrets import compare_digest

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter()
ASSET_ROUTE = r"/smd-release/[a-f0-9]{64}/(?:installer\.sh|bundle\.tar|workbench-[a-f0-9]{64}\.tar\.gz)"


@router.get("/smd-release/{capability}/{filename}")
def release_asset(capability: str, filename: str):
    token = os.getenv("SMARTDEV_SMD_RELEASE_DOWNLOAD_TOKEN", "")
    directory = os.getenv("SMARTDEV_SMD_RELEASE_DIRECTORY", "")
    workspace = re.fullmatch(r"workbench-([a-f0-9]{64})\.tar\.gz",filename)
    if (not re.fullmatch(r"[a-f0-9]{64}", capability) or (filename not in {"installer.sh", "bundle.tar"} and not workspace)):
        raise HTTPException(404, "Release asset unavailable.")
    if directory and re.fullmatch(r"[a-f0-9]{64}", token) and compare_digest(capability, token):
        file = Path(directory) / filename
    else:
        archive = os.getenv("SMARTDEV_SMD_VERSIONED_INSTALLERS")
        if not archive or (filename != "installer.sh" and not workspace):
            raise HTTPException(404, "Release asset unavailable.")
        version = Path(archive) / sha256(capability.encode("ascii")).hexdigest()
        if version.is_symlink() or not (version/"installer.sh").is_file():
            raise HTTPException(404, "Release asset unavailable.")
        file = version / filename
    if workspace:
        file = Path(__file__).with_name("smd_workbench.tar.gz")
        history=os.getenv("SMARTDEV_SMD_VERSIONED_INSTALLERS")
        if history:
            pinned=Path(history)/"workbenches"/(workspace[1]+".tar.gz")
            if not pinned.is_symlink() and pinned.is_file():
                file=pinned
        if file.is_file():
            with file.open("rb") as source:
                if not compare_digest(workspace[1],file_digest(source,"sha256").hexdigest()):
                    raise HTTPException(404,"Release asset unavailable.")
    if not file.is_file() or file.is_symlink():
        raise HTTPException(404, "Release asset unavailable.")
    return FileResponse(file, media_type="application/octet-stream",
                        headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"})
