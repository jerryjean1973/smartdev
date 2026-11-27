"""Keep the existing Command Center OpenBao service lease usable across restarts."""
import json
import os
from pathlib import Path
import sys
import tempfile
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import build_opener,HTTPRedirectHandler,ProxyHandler,Request

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args):
        return None


def maintain(address,token_file,role_file=None,secret_file=None):
    url=urlsplit(address)
    if url.username or url.password or url.path not in ("","/") or url.query or url.fragment:
        raise ValueError("Invalid operator Vault origin")
    if url.scheme!="https" and not (url.scheme=="http" and url.hostname in ("vault","127.0.0.1","localhost")):
        raise ValueError("Use the dedicated internal Vault network or a verified TLS endpoint")
    client=build_opener(ProxyHandler({}),NoRedirect())
    def call(route,method="GET",token=None,payload=None):
        headers={"Content-Type":"application/json"}
        if token:
            headers["X-Vault-Token"]=token
        req=Request(address.rstrip("/")+"/v1/"+route,method=method,headers=headers,
                    data=json.dumps(payload).encode() if payload is not None else None)
        with client.open(req,timeout=7) as response:
            return json.load(response)
    def lookup(token):
        data=call("auth/token/lookup-self",token=token)["data"]
        if "smartdev-portal" not in data["policies"] or set(data["policies"])-{"smartdev-portal","default"}:
            raise RuntimeError("Vault identity is outside the portal service policy")
        return data
    token=token_file.read_text().strip() if token_file.is_file() else ""
    try:
        data=lookup(token) if token else {}
        if data.get("ttl",0)>86400:
            return "valid"
        if data.get("renewable"):
            call("auth/token/renew-self","POST",token,{})
            if lookup(token).get("ttl",0)>3600:
                return "renewed"
    except HTTPError as exc:
        if exc.code not in (400,401,403):
            raise
    if not (role_file and secret_file and role_file.is_file() and secret_file.is_file()):
        raise RuntimeError("Portal service lease needs an operator-issued replacement or configured portal AppRole")
    result=call("auth/approle/login","POST",payload={"role_id":role_file.read_text().strip(),"secret_id":secret_file.read_text().strip()})
    new=result["auth"]["client_token"]
    lookup(new)
    with tempfile.NamedTemporaryFile(mode="w",dir=token_file.parent,delete=False) as out:
        out.write(new+"\n")
        out.flush()
        os.fsync(out.fileno())
        os.fchmod(out.fileno(),0o400)
        if os.fstat(out.fileno()).st_uid!=65532:
            os.fchown(out.fileno(),65532,65532)
    os.replace(out.name,token_file)
    return "reissued"


if __name__=="__main__":
    try:
        base=Path("/run/command-vault")
        enrollment=Path("/run/vault-enrollment")
        status=maintain(os.environ["OPENBAO_ADDR"],base/"token",enrollment/"role-id",enrollment/"secret-id")
        print("Portal Vault lease "+status)
    except Exception as exc:
        # HTTP libraries can include request metadata. Log only fixed operator status.
        print("Portal Vault lease could not be renewed. Check service enrollment and Vault health.",file=sys.stderr)
        raise SystemExit(1) from None
