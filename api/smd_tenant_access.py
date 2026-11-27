"""Assignment-specific tenant credentials and an independent owner handoff authority."""
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import time
from uuid import UUID,uuid4

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey,Ed25519PublicKey
from fastapi import APIRouter,HTTPException,Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel,Field,SecretStr

from store import database,queue
from vault import call,NAME

router=APIRouter()


def secret_for(purpose):
    return hmac.digest(bytes.fromhex(os.environ["SESSION_KEY"]),b"smartdev/smd/v1/"+purpose,"sha256")


def password(task,tenant,version):
    material=f"{task}/{tenant}/{version}".encode()
    return base64.urlsafe_b64encode(hmac.digest(secret_for(b"tenant-login"),material,"sha256")).decode().rstrip("=")


def public(key):
    return base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)).decode()


def authority():
    return Ed25519PrivateKey.from_private_bytes(secret_for(b"assignment-authority"))


@router.get("/smd-configuration-authority")
def public_authority():
    return {"public_key":public(authority())}


def stage_generation(row,task,version):
    payload={"task":str(task),"instance":str(row["instance_id"]),"version":version,"nonce":str(uuid4()),"time":int(time.time())}
    message=json.dumps(payload,sort_keys=True,separators=(",",":")).encode("ascii")
    proof=base64.b64encode(authority().sign(message)).decode()
    try:
        with httpx.Client(trust_env=False,follow_redirects=False,timeout=100) as client:
            reply=client.post(row["portal"].rstrip("/")+"/api/deployment-portal/assignment",json={"payload":payload,"signature":proof})
        reply.raise_for_status()
        data=reply.json()
        public_key=base64.b64decode(data["key"],validate=True)
        Ed25519PublicKey.from_public_bytes(public_key).verify(base64.b64decode(data["proof"],validate=True),b"smd-stage-v1\n"+message)
        return public_key
    except (httpx.HTTPError,InvalidSignature,ValueError,KeyError,TypeError):
        raise HTTPException(503,"Tenant assignment is pending. The installation could not confirm its new login and secret binding.") from None


@router.post("/admin/compute/deployments/{task}/tenant-login")
def reveal_login(task:UUID):
    with database() as db:
        row=db.execute("""SELECT a.version,a.tenant_id,c.name,c.id=smd_admin_tenant_id() AS owner
         FROM smd_deployment_assignment a JOIN command_center_tenant c ON c.id=a.tenant_id
         JOIN smd_install_identity i ON i.task_id=a.task_id JOIN smd_deploy_task t ON t.id=a.task_id
         WHERE a.task_id=%s AND t.status='deployed' AND t.instance_id=i.instance_id AND t.reconciled_at IS NULL""",(task,)).fetchone()
        if not row:
            raise HTTPException(409,"Portal login is awaiting enrollment.")
        db.execute("INSERT INTO smd_deployment_event(task_id,kind,label) VALUES(%s,'portal_login','Owner retrieved the current assigned portal login')",(task,))
    return JSONResponse({"account":row["name"],"password":password(task,row["tenant_id"],row["version"])},
                        headers={"Cache-Control":"no-store","Pragma":"no-cache"})


class TenantSecret(BaseModel,extra="forbid"):
    value:SecretStr=Field(min_length=1,max_length=32768)
    version:int=Field(ge=0)


def default_path(tenant,env,name):
    if env not in {"DEV","QA","PREPROD","PROD"} or not NAME.fullmatch(name):
        raise HTTPException(422,"Invalid environment or secret name.")
    return f"projects/tenant-defaults/{tenant}/{env}/{name}"


def tenant_exists(db,tenant):
    if not db.execute("SELECT 1 FROM command_center_tenant WHERE id=%s",(tenant,)).fetchone():
        raise HTTPException(404,"Tenant unavailable.")


def tenant_defaults(db,tenant):
    return db.execute("""SELECT environment,name,version FROM smd_tenant_default_secret
         WHERE tenant_id=%s ORDER BY environment,name""",(tenant,)).fetchall()


@router.get("/admin/tenants/{tenant}/defaults")
def list_defaults(tenant:UUID):
    with database() as db:
        tenant_exists(db,tenant)
        return tenant_defaults(db,tenant)


@router.put("/admin/tenants/{tenant}/defaults/{env}/{name}")
def set_default(tenant:UUID,env:str,name:str,body:TenantSecret):
    path=default_path(tenant,env,name)
    with database() as db:
        tenant_exists(db,tenant)
        db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",(path,))
        row=db.execute("SELECT version FROM smd_tenant_default_secret WHERE tenant_id=%s AND environment=%s AND name=%s",
                       (tenant,env,name)).fetchone()
        if (row["version"] if row else 0)!=body.version:
            raise HTTPException(409,"Tenant default changed. Refresh and retry.")
        # A fresh random Vault reference avoids a partial write replacing a committed value.
        ref=path+"/"+str(uuid4())
        call("POST","data/"+ref,{"data":{"value":body.value.get_secret_value()},"options":{"cas":0}})
        db.execute("""INSERT INTO smd_tenant_default_secret(tenant_id,environment,name,reference,version) VALUES(%s,%s,%s,%s,%s)
              ON CONFLICT(tenant_id,environment,name) DO UPDATE SET reference=excluded.reference,version=excluded.version""",
              (tenant,env,name,ref,body.version+1))
    return {"version":body.version+1}


@router.delete("/admin/tenants/{tenant}/defaults/{env}/{name}")
def remove_default(tenant:UUID,env:str,name:str,version:int):
    path=default_path(tenant,env,name)
    with database() as db:
        db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",(path,))
        row=db.execute("""DELETE FROM smd_tenant_default_secret WHERE tenant_id=%s AND environment=%s AND name=%s
                       AND version=%s RETURNING reference""",(tenant,env,name,version)).fetchone()
        if not row:
            raise HTTPException(409,"Tenant default changed.")
        call("POST","delete/"+row["reference"],{"versions":[1]})
    return {"removed":True}


def tenant_value(db,tenant,env,name):
    row=db.execute("SELECT reference FROM smd_tenant_default_secret WHERE tenant_id=%s AND environment=%s AND name=%s",
                   (tenant,env,name)).fetchone()
    if not row:
        return None
    value=call("GET","data/"+row["reference"],missing=True)
    return ((value or {}).get("data",{}).get("data") or {}).get("value")


def confirm_defaults(db,tenant):
    for item in db.execute("SELECT reference FROM smd_tenant_default_secret WHERE tenant_id=%s FOR SHARE",(tenant,)):
        stored=call("GET","data/"+item["reference"],missing=True)
        value=((stored or {}).get("data",{}).get("data") or {}).get("value")
        if not isinstance(value,str) or not value:
            raise HTTPException(409,"The destination tenant has an unavailable default secret.")


def login_check(task,state,body):
    try:
        supplied=json.loads(body)["password"]
        if not isinstance(supplied,str) or len(supplied)>512: raise ValueError
    except (ValueError,TypeError,KeyError):
        raise HTTPException(401,"Invalid portal credential.") from None
    with queue().pipeline() as pipe:
        key=f"smd:portal:attempts:{task}:{state['version']}"
        count,_=pipe.incr(key).expire(key,60,nx=True).execute()
    if count>12:
        raise HTTPException(429,"Too many portal sign-in attempts.")
    if not secrets.compare_digest(supplied.encode(),password(task,state["tenant_id"],state["version"]).encode()):
        raise HTTPException(401,"Invalid portal credential.")
    return {"version":state["version"]}
