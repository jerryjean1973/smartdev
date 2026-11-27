"""Sign a bounded operation for the enrolled SMD instance. No provider key leaves its vault."""
import base64
import hashlib
import json
import os
from pathlib import Path
import time
from uuid import UUID, uuid4

from cryptography.hazmat.primitives.serialization import load_pem_private_key
from fastapi import HTTPException
import httpx

KEY = Path("/run/smd-install-identity.pem")


def signed_message(task, instance, project, operation, timestamp, nonce, payload):
    return "\n".join(("smd-config-v1", str(task), str(instance), str(project), operation,
                      str(timestamp), str(nonce), hashlib.sha256(payload).hexdigest())).encode("ascii")


def enrolled():
    return bool(os.getenv("SMD_PORTAL_TASK")) and KEY.is_file()


def keys():
    root=Path("/run/smd-generations")
    staged=sorted((p for p in root.glob("*.pem") if p.stem.isdecimal()),key=lambda p:int(p.stem),reverse=True) if root.is_dir() else []
    return [*staged[:2],KEY]


def signed_request(project, operation, payload=b"",key_file=None):
    task = str(UUID(os.environ["SMD_PORTAL_TASK"]))
    instance = str(UUID(os.environ["SMD_PORTAL_INSTANCE"]))
    nonce, now = str(uuid4()), str(int(time.time()))
    signature = load_pem_private_key((key_file or keys()[0]).read_bytes(), None).sign(
        signed_message(task, instance, project, operation, now, nonce, payload))
    url = os.environ["SMD_PORTAL_ISSUER"] + "/api/smd-deploy-tasks/" + task + "/configuration"
    return url, {"X-SMD-Project": str(project), "X-SMD-Instance": instance, "X-SMD-Operation": operation,
                 "X-SMD-Nonce": nonce, "X-SMD-Time": now, "X-SMD-Signature": base64.b64encode(signature).decode(),
                 "Content-Type": "application/json"}


def owner_json(project, operation, value=None):
    payload = b"" if value is None else json.dumps(value,separators=(",",":")).encode()
    try:
        with httpx.Client(timeout=110 if operation=="model" else 12, trust_env=False, follow_redirects=False) as client:
            for key_file in keys():
                url,headers=signed_request(project,operation,payload,key_file)
                response=client.post(url,headers=headers,content=payload)
                if response.status_code!=403 or response.headers.get("x-smd-identity")!="superseded":
                    break
        if response.is_success:
            return response.json()
        if response.status_code in (401, 403, 409, 422,429):
            raise HTTPException(response.status_code, "This operation is unavailable for the deployment assignment.")
    except (httpx.RequestError, ValueError):
        pass
    raise HTTPException(503, "Deployment configuration service unavailable.")
