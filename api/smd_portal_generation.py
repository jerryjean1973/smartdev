"""Tenant login and session generation enforced by the enrolled portal receiver."""
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import time
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey,Ed25519PublicKey
from fastapi import HTTPException
from fastapi.responses import JSONResponse

from core import issue_session,valid_session
from smd_owner_client import owner_json

MARK="__Host-smd-account"
KEY_DIR=Path("/run/smd-generations")
CONFIRMATIONS=Path("/run/smd-confirmations")


def account_cookie(session,scope):
    key=bytes.fromhex(os.environ["SESSION_KEY"])
    return scope+"."+hmac.digest(key,("smd-account\n"+scope+"\n"+session).encode(),"sha256").hex()


def set_account(response,session,scope,ttl):
    response.set_cookie(MARK,account_cookie(session,scope),secure=True,httponly=True,samesite="strict",max_age=ttl)


def valid_account(session,mark):
    key=bytes.fromhex(os.environ["SESSION_KEY"])
    if not valid_session(session,key):
        return False
    scope,sep,_=mark.partition(".")
    if not sep or not hmac.compare_digest(mark.encode(),account_cookie(session,scope).encode()):
        return False
    if scope=="owner":
        return True
    if not scope.isdecimal():
        return False
    project=os.environ["SMD_PORTAL_PROJECTS"].split(",")[0]
    return owner_json(project,"portal-generation")["version"]==int(scope)


def tenant_login(value):
    if not isinstance(value,str) or len(value)>512:
        raise HTTPException(401,"Invalid password.")
    project=os.environ["SMD_PORTAL_PROJECTS"].split(",")[0]
    scope=owner_json(project,"portal-auth",{"password":value})["version"]
    session=issue_session(bytes.fromhex(os.environ["SESSION_KEY"]))
    result=JSONResponse({"ok":True},headers={"Cache-Control":"no-store"})
    result.set_cookie("__Host-command",session,secure=True,httponly=True,samesite="strict",max_age=28800)
    from store import queue
    from coder_session import session_ref
    with queue().pipeline() as pipe:
        pipe.sadd("smd:tenant-coder:"+str(scope),session_ref(session)).expire("smd:tenant-coder:"+str(scope),86400).execute()
    set_account(result,session,str(scope),28800)
    return result


def stage(data):
    try:
        payload=data["payload"]
        if set(payload)!={"task","instance","version","nonce","time"} or payload["task"]!=os.environ["SMD_PORTAL_TASK"] or payload["instance"]!=os.environ["SMD_PORTAL_INSTANCE"]:
            raise ValueError
        version=payload["version"]
        if not isinstance(version,int) or not 0<version<2**31 or abs(time.time()-payload["time"])>75:
            raise ValueError
        UUID(payload["nonce"])
        message=json.dumps(payload,sort_keys=True,separators=(",",":")).encode("ascii")
        authority=base64.b64decode(os.environ["SMD_ASSIGNMENT_AUTHORITY"],validate=True)
        Ed25519PublicKey.from_public_bytes(authority).verify(base64.b64decode(data["signature"],validate=True),message)
    except (KeyError,TypeError,ValueError,InvalidSignature):
        raise HTTPException(403,"Invalid assignment authorization.") from None
    digest=hashlib.sha256(message).hexdigest()
    ack=CONFIRMATIONS/digest
    if not ack.is_file():
        tmp=KEY_DIR/("request-"+secrets.token_hex(12))
        tmp.write_text(json.dumps(data,separators=(",",":")))
        tmp.replace(KEY_DIR/"handoff.json")
        deadline=time.monotonic()+85
        while not ack.is_file() and time.monotonic()<deadline:
            time.sleep(.25)
        if not ack.is_file():
            raise HTTPException(503,"Previous tenant secret cleanup could not be verified.")
    target=KEY_DIR/(str(version)+".pem")
    if not target.exists():
        pending=KEY_DIR/("pending-"+secrets.token_hex(12))
        fd=os.open(pending,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o400)
        with os.fdopen(fd,"wb") as output:
            output.write(Ed25519PrivateKey.generate().private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        try:
            os.link(pending,target)
        except FileExistsError:
            pass
        finally:
            pending.unlink()
    key=serialization.load_pem_private_key(target.read_bytes(),None)
    return {"key":base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)).decode(),
            "proof":base64.b64encode(key.sign(b"smd-stage-v1\n"+message)).decode()}
