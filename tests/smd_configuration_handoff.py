"""Exercise assignment, isolated tenant defaults and owner SSO with a disposable PostgreSQL schema."""
import base64
import hashlib
import tempfile
import json
import os
from pathlib import Path
import time
from unittest.mock import patch
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import HTTPException
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
from core import issue_session
from compute_console import Assignment,assign
from main import app
from smd_owner_client import signed_message
from smd_owner_config import bind
from smd_portal_generation import account_cookie,valid_account
import smd_tenant_access as tenant
from store import database


def run():
    schema="smd_test_"+uuid4().hex
    original=os.environ.get("PGOPTIONS","")
    with database() as db:
        db.execute("CREATE SCHEMA "+schema)
    os.environ["PGOPTIONS"]="-c search_path="+schema
    owner,one,two,project,task,instance=[uuid4() for _ in range(6)]
    key=Ed25519PrivateKey.generate()
    root=Path(__file__).resolve().parents[1]
    migration=root/"api/060_smd_configuration.sql"
    if not migration.exists(): migration=Path("/app/060_smd_configuration.sql")
    def raw(key):
        return base64.b64decode(tenant.public(key))
    def api(client,key,op,body=b"",target=project,nonce=None):
        n=nonce or uuid4()
        now=int(time.time())
        sig=key.sign(signed_message(task,instance,target,op,now,n,body))
        headers={"X-SMD-Project":str(target),"X-SMD-Instance":str(instance),"X-SMD-Nonce":str(n),"X-SMD-Time":str(now),
            "X-SMD-Operation":op,"X-SMD-Signature":base64.b64encode(sig).decode()}
        return client.post(f"/smd-deploy-tasks/{task}/configuration",headers=headers,content=body)
    try:
        with database() as db:
            db.execute("""CREATE TABLE projects(id uuid PRIMARY KEY,archived boolean DEFAULT false);
                CREATE TABLE command_center_tenant(id uuid PRIMARY KEY,name text);
                CREATE TABLE smd_deploy_task(id uuid UNIQUE,project_id uuid,instance_id uuid,status text,reconciled_at timestamptz,dns_zone text,project_manifest jsonb);
                CREATE TABLE smd_deployment_assignment(task_id uuid PRIMARY KEY,tenant_id uuid,version int);
                CREATE TABLE smd_deploy_receipt(task_id uuid,instance_id uuid,verified_at timestamptz,connections jsonb);
                CREATE TABLE smd_deployment_event(task_id uuid,kind text,label text,previous_tenant uuid,next_tenant uuid);
                CREATE TABLE smd_portal_launch(task_id uuid);
                CREATE TABLE audit(project_id uuid,action text);
                CREATE TABLE integration_connections(provider text,status text);
                CREATE TABLE integration_ai_role(scope text,role text,provider text,model text);
                CREATE TABLE project_runtime(project_id uuid,environment text,secret_bindings jsonb);""")
            db.execute("CREATE FUNCTION smd_admin_tenant_id() RETURNS uuid LANGUAGE SQL AS $$SELECT '"+str(owner)+"'::uuid$$")
            db.execute(migration.read_text())
            for tid,name in ((owner,"Owner"),(one,"One"),(two,"Two")):
                db.execute("INSERT INTO command_center_tenant VALUES (%s,%s)",(tid,name))
            db.execute("INSERT INTO projects(id) VALUES (%s)",(project,))
            db.execute("INSERT INTO smd_deploy_task VALUES (%s,%s,%s,'deployed',NULL,'qa.example.test',%s)",
                       (task,project,instance,Jsonb([{"id":str(project),"revision":"a"*40}])))
            db.execute("INSERT INTO smd_deploy_receipt VALUES (%s,%s,now(),%s)",(task,instance,Jsonb({"portal_url":f"https://smd-{task.hex}.qa.example.test","coder_url":"https://coder.qa.example.test","apps":[{"project_id":str(project)},{"project_id":str(uuid4())}]})))
            db.execute("INSERT INTO smd_deployment_assignment VALUES (%s,%s,0)",(task,owner))
            bind(db,task,instance,raw(key))
        # The remote end produces an independent signing key only after confirming clean local secrets.
        client=TestClient(app,base_url="https://owner.example.test")
        vault={}
        def backend(method,path,payload=None,missing=False):
            if method=="POST":
                vault[path]=payload["data"]
                return {"data":{"version":1}}
            item=vault.get(path)
            return {"data":{"data":item}} if item else None
        keys=[]
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
        import smd_portal_generation as receiver
        handshake=tenant.stage_generation
        def staged(row,tid,version):
            with tempfile.TemporaryDirectory() as folder:
                directory=Path(folder)
                class Transport:
                    def __init__(self,**kwargs): pass
                    def __enter__(self): return self
                    def __exit__(self,*args): pass
                    def post(self,url,json):
                        assert url==row["portal"]+"/api/deployment-portal/assignment"
                        message=__import__("json").dumps(json["payload"],sort_keys=True,separators=(",",":")).encode()
                        (directory/hashlib.sha256(message).hexdigest()).write_text("verified")
                        result=receiver.stage(json)
                        from httpx import Response,Request
                        return Response(200,json=result,request=Request("POST",url))
                with patch.object(tenant.httpx,"Client",Transport),patch.object(receiver,"KEY_DIR",directory),patch.object(receiver,"CONFIRMATIONS",directory),patch.dict(os.environ,{
                    "SMD_PORTAL_TASK":str(task),"SMD_PORTAL_INSTANCE":str(instance),
                    "SMD_ASSIGNMENT_AUTHORITY":tenant.public(tenant.authority())}):
                    new=handshake(row,tid,version)
                    keys.append(load_pem_private_key((directory/(str(version)+".pem")).read_bytes(),None))
                    assert new==raw(keys[-1])
                    return new
        with patch.object(tenant,"call",backend),patch("smd_owner_config.call",backend),patch.object(tenant,"stage_generation",staged):
            tenant.set_default(one,"DEV","CANARY",tenant.TenantSecret(value="fixture-red",version=0))
            tenant.set_default(two,"DEV","CANARY",tenant.TenantSecret(value="fixture-blue",version=0))
            connections=api(client,key,"deployment-access").json()
            assert connections["tenant"]=="Owner" and connections["apps"]==[{"project_id":str(project)}]
            assert api(client,key,"deployment-access",target=uuid4()).status_code==403
            nonce=uuid4()
            assert api(client,key,"catalog",nonce=nonce).status_code==200
            assert api(client,key,"catalog",nonce=nonce).status_code==409
            assert api(client,key,"catalog",target=uuid4()).status_code==403
            spec=json.dumps({"name":"CANARY","environment":"DEV"}).encode()
            assert api(client,key,"tenant-secret",spec).json()=={"present":False}
            tenant.set_default(owner,"DEV","CANARY",tenant.TenantSecret(value="fixture-owner",version=0))
            tenant.set_default(owner,"DEV","OPENAI_API_KEY",tenant.TenantSecret(value="fixture-owner-codex",version=0))
            tenant.set_default(one,"DEV","OPENAI_API_KEY",tenant.TenantSecret(value="fixture-first-tenant-codex",version=0))
            with patch("codex_access.verify_provider_key") as verify:
                assert api(client,key,"codex-models").json()=={"ready":True}
                verify.assert_called_once_with("fixture-owner-codex")
            with database() as db:
                db.execute("INSERT INTO project_runtime VALUES (%s,'DEV',%s)",(project,Jsonb({"APP_KEY":"APP_SOURCE","CANARY":"APP_SOURCE"})))
            vault["data/projects/"+str(project)+"/DEV/APP_SOURCE"]={"value":"fixture-project"}
            names=api(client,key,"tenant-bindings").json()["names"]
            assert sorted(item["name"] for item in names)==["APP_KEY","CANARY","OPENAI_API_KEY"]
            assert api(client,key,"tenant-secret",spec).json()["value"]=="fixture-owner"
            query=lambda name: json.dumps({"name":name,"environment":"DEV"}).encode()
            assert api(client,key,"tenant-secret",query("APP_KEY")).json()["value"]=="fixture-project"
            assert api(client,key,"tenant-secret",query("APP_SOURCE")).json()=={"present":False}
            domain="dev.customer.example"
            enroll=api(client,key,"domain-challenge",json.dumps({"hostname":domain}).encode())
            assert enroll.status_code==200
            change=json.dumps({"hostname":domain,"environment":"DEV","version":0}).encode()
            with patch("dns_inspection.verification_txt_answer",return_value=[]),patch("dns_inspection.txt_matches",return_value=False):
                assert api(client,key,"domain-report",change).status_code==422
            with patch("dns_inspection.verification_txt_answer",return_value=[]),patch("dns_inspection.txt_matches",return_value=True),patch("smd_preview_domains.assert_public_https") as tls:
                assert api(client,key,"domain-report",change).status_code==200
                assert tls.call_count==1
            assert api(client,key,"domains").json()["domains"]=={"DEV":domain}
            old=tenant.password(task,owner,0)
            res=assign(task,Assignment(tenant_id=one,version=0))
            assert res["version"]==1 and api(client,key,"catalog").status_code==403
            first=keys[-1]
            with patch("codex_access.verify_provider_key") as verify:
                assert api(client,first,"codex-models").json()=={"ready":True}
                verify.assert_called_once_with("fixture-first-tenant-codex")
            assert api(client,first,"domains").json()["domains"]=={}
            assert api(client,first,"tenant-secret",spec).json()["value"]=="fixture-red"
            assert api(client,first,"tenant-secret",query("APP_KEY")).json()=={"present":False}
            assert api(client,first,"portal-auth",json.dumps({"password":old}).encode()).status_code==401
            password=tenant.password(task,one,1)
            assert api(client,first,"portal-auth",json.dumps({"password":password}).encode()).status_code==200
            session=issue_session(bytes.fromhex(os.environ["SESSION_KEY"]))
            mark=account_cookie(session,"1")
            with patch("smd_portal_generation.owner_json",return_value={"version":1}),patch.dict(os.environ,{"SMD_PORTAL_PROJECTS":str(project)}):
                assert valid_account(session,mark)
            with patch.object(tenant,"stage_generation",side_effect=HTTPException(503,"Cleanup not confirmed")):
                try: assign(task,Assignment(tenant_id=two,version=1))
                except HTTPException as exc: assert exc.status_code==503
                else: raise AssertionError("Unverified handoff was accepted")
            assert api(client,first,"tenant-secret",spec).json()["value"]=="fixture-red"
            assign(task,Assignment(tenant_id=two,version=1))
            second=keys[-1]
            with patch("codex_access.verify_provider_key") as verify:
                assert api(client,second,"codex-models").status_code==409
                verify.assert_not_called()
            assert api(client,first,"tenant-secret",spec).status_code==403
            assert api(client,second,"tenant-secret",spec).json()["value"]=="fixture-blue"
            assert api(client,second,"portal-auth",json.dumps({"password":password}).encode()).status_code==401
            assert api(client,second,"portal-auth",json.dumps({"password":tenant.password(task,two,2)}).encode()).status_code==200
            with patch("smd_portal_generation.owner_json",return_value={"version":2}),patch.dict(os.environ,{"SMD_PORTAL_PROJECTS":str(project)}):
                assert not valid_account(session,mark)
                assert valid_account(session,account_cookie(session,"owner"))
            assign(task,Assignment(tenant_id=None,version=2))
            assert api(client,key,"catalog").status_code==403 and api(client,second,"catalog").status_code==403
            assert api(client,keys[-1],"catalog").status_code==200
        with database() as db:
            assert db.execute("SELECT count(*) AS n FROM projects") .fetchone()["n"]==1
            assert db.execute("SELECT count(*) AS n FROM smd_tenant_default_secret").fetchone()["n"]==5
            assert "fixture-" not in str(db.execute("SELECT * FROM audit").fetchall())
        print("PASS verified owner / tenant A / tenant B / owner, independent portal access, unchanged project, revoked sessions, replay rejection, failed-cleanup rollback and tenant-specific canaries")
    finally:
        os.environ["PGOPTIONS"]=original
        with database() as db:
            db.execute("DROP SCHEMA "+schema+" CASCADE")


if __name__=="__main__":
    run()
