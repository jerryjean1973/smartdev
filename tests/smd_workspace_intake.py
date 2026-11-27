"""Validate an empty tenant install with the production enrollment and API entry point."""
import importlib.util
import io
import json
import os
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import make_conninfo
from store import database

def run():
    project, foreign = uuid4(),uuid4()
    schema="qa_smd_"+uuid4().hex
    with database() as db:
        db.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        with patch.dict(os.environ, DATABASE_URL=make_conninfo(os.environ["DATABASE_URL"],options="-csearch_path="+schema+",public -csmartdev.install_kind=tenant"),
                        SMD_PORTAL_PROJECTS=str(project),SMD_PORTAL_TASK=str(uuid4()),SMD_PORTAL_INSTANCE=str(uuid4()),
                        SMD_PORTAL_ISSUER="https://owner.qa.test",PORTAL_ORIGIN="https://tenant.qa.test",CODER_PUBLIC_URL="https://coder.qa.test"):
            from deployment_portal_receiver import app
            from core import issue_session
            with database() as db:
                db.execute("CREATE TABLE projects(id uuid primary key,name text,repository text,runtime text,status text,workspace_url text,error text,archived boolean default false)")
                db.execute("CREATE TABLE objective_contract (id uuid,project_id uuid,business_objective text)")
                db.execute("CREATE TABLE audit (project_id uuid,action text)")
                db.execute("CREATE TABLE project_preview_url(project_id uuid,environment text,mode text,url text,primary key(project_id,environment))")
                db.execute(Path("041_frozen_business_objectives.sql").read_text())
                assert not db.execute("SELECT id FROM business_objective").fetchall()
            spec=importlib.util.spec_from_file_location("enrollment","enroll-api.py")
            enrollment=importlib.util.module_from_spec(spec)
            spec.loader.exec_module(enrollment)
            manifest=[{"id":str(project),"hostname":"app.qa.test","name":"Demo","runtime":"node","repository":"test/demo"}]
            with patch("sys.stdin",io.StringIO(json.dumps(manifest))): enrollment.finish()
            with database() as db:
                assert db.execute("SELECT url FROM project_preview_url").fetchone()["url"]=="https://app.qa.test/"
                db.execute("INSERT INTO business_objective VALUES ('BO-01','Owner seed','Owner seed','P0',1,true)")
                db.execute("INSERT INTO objective_contract VALUES (%s,%s,'BO-01')",(uuid4(),foreign))
            client=TestClient(app,base_url=os.environ["PORTAL_ORIGIN"])
            assert client.get("/taskboard/business-objectives").status_code==401
            client.cookies.set("__Host-command",issue_session(bytes.fromhex(os.environ["SESSION_KEY"])))
            assert client.get("/taskboard/business-objectives").json()==[]
            assert client.get("/admin/project-tenants").status_code==404
            assert client.get("/projects/"+str(foreign)+"/objectives").status_code==404
            assert client.get("/cc-codex/activity").status_code==404
            assert client.get("/projects/"+str(project)+"/preview").json()["DEV"]["url"]=="https://app.qa.test/"
            print("Fresh tenant enrollment, persisted DEV preview, empty tenant catalog and owner route isolation passed.")
    finally:
        with database() as db:
            db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))

if __name__=="__main__": run()
