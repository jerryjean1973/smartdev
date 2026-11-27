"""Exercise concurrent admissions against a disposable PostgreSQL schema."""
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from psycopg import sql
from psycopg.conninfo import make_conninfo
from store import database
import mcp_intake as intake
from external_mcp_tasks import ScopedTasks, WorkspaceUnavailable


def run():
    schema = "qa_mcp_" + uuid4().hex
    with database() as db:
        db.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        isolated = make_conninfo(os.environ["DATABASE_URL"], options="-csearch_path="+schema+",public")
        with patch.dict(os.environ, DATABASE_URL=isolated):
            exercise()
    finally:
        with database() as db:
            db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def exercise():
    first, other, grant, owner = [uuid4() for _ in range(4)]
    with database() as db:
        for table in ("projects","objective_contract","objective_run","objective_presentation","acceptance_fixture",
                      "conversation_submission","business_objective","external_mcp_grant"):
            db.execute(sql.SQL("CREATE TABLE {} AS SELECT * FROM public.{} WITH NO DATA").format(
                sql.Identifier(table),sql.Identifier(table)))
        db.execute("ALTER TABLE projects ADD PRIMARY KEY(id)")
        db.execute("ALTER TABLE external_mcp_grant ADD PRIMARY KEY(id)")
        db.execute("INSERT INTO projects(id,name,archived) VALUES (%s,'Test A',false),(%s,'Test B',false)",(first,other))
        db.execute("INSERT INTO external_mcp_grant(id,project_ids,scopes,expires_at) VALUES(%s,%s,ARRAY['write'],now()+interval '1 hour')",(grant,[first,other]))
        db.execute("INSERT INTO business_objective(id) VALUES ('BO-01')")
        root=Path(intake.__file__).parent
        for name in ("052_mcp_task_requests.sql","053_mcp_request_scope.sql","055_mcp_task_lifecycle.sql","059_mcp_intake.sql"):
            db.execute((root/name).read_text().replace("CREATE TABLE IF NOT EXISTS workspace_screenshot","CREATE TABLE workspace_screenshot"))
    prompt="Correct duplicate delivery of checkout receipts when reconnecting the agent"
    def receive(project=first,text=prompt,title="Checkout receipt delivery",key=None,scope="global",request=None):
        return intake.receive(project,request or uuid4(),"create",text,title,None,scope,work_key=key,grant_id=grant)
    barrier=Barrier(6)
    def submit(i):
        barrier.wait()
        return receive()
    with ThreadPoolExecutor(max_workers=6) as pool:
        rows=list(pool.map(submit,range(6)))
    assert sorted(r["admission"] for r in rows)==["created"]+["duplicate"]*5
    created=next(r for r in rows if r["admission"]=="created")
    target=created["task_id"]
    assert all(r["task_id"]==target for r in rows)
    assert receive(request=created["request_id"])["task_id"]==target
    try: receive(text="Changed content",request=created["request_id"])
    except ValueError: pass
    else: raise AssertionError("Conflicting replay accepted")
    assert receive(text=prompt+" and include disconnect tests")["admission"]=="updated"
    assert receive(text="Rewrite the supplier registration splash animation")["task_id"]!=target
    scoped=receive(scope="BO-01")
    assert scoped["task_id"]!=target
    assert receive(project=other)["task_id"]!=target
    keyed=receive(text="Correct spacing in narrow navigation menus",key="design/menus")
    assert receive(text="Also retain the keyboard focus ring",key="design/menus")["task_id"]==keyed["task_id"]
    client=ScopedTasks({"id":grant,"owner_id":owner,"project_ids":[first]},projects=lambda:[{"id":first,"name":"Test","status":"created"}],task_navigation=lambda:[])
    with patch.object(client,"_workspace",side_effect=WorkspaceUnavailable("Offline")):
        receipt=client.create_task(first,"Correct sorting of the binary package inventory",uuid4(),scope="global")
        assert receipt["status"]=="Pending" and receipt["task_id"]==receipt["codex_task_id"]
        assert client.get_task(receipt["task_id"])["status"]=="Pending"
        assert client.send_message(receipt["task_id"],"Include archived packages",uuid4())["codex_task_id"]==receipt["task_id"]
    with database() as db:
        db.execute("UPDATE mcp_task_outbox SET next_attempt=now() WHERE request_id=%s",(receipt["request_id"],))
        record=db.execute("SELECT envelope FROM mcp_task_outbox WHERE request_id=%s",(receipt["request_id"],)).fetchone()
        assert b"inventory" not in record["envelope"]
    def workspace(project,url,method,endpoint,*,payload):
        assert project==first and payload["newTask"] and payload["prompt"].endswith("inventory")
        return {"objective":receipt["task_id"],"conversationId":receipt["task_id"],"messageId":str(uuid4()),"executionId":"a"*24}
    with patch("external_mcp_tasks.workspace_chat_request",workspace):
        assert intake.retry_due()
    with database() as db:
        assert not db.execute("SELECT 1 FROM mcp_task_outbox WHERE request_id=%s",(receipt["request_id"],)).fetchone()
        native=uuid4()
        db.execute("""INSERT INTO objective_contract(id,project_id,status,original_request,display_name,business_objective,version,criteria)
                   VALUES (%s,%s,'active',%s,'Scroll receipts','BO-01',1,'[]')""",(native,first,"Repair scroll position in older receipts"))
    assert receive(text="Repair scroll position in older receipts",scope="BO-01")["task_id"]==native
    with database() as db:
        db.execute("UPDATE objective_contract SET status='done' WHERE id=%s",(native,))
    assert receive(text="Repair scroll position in older receipts",scope="BO-01")["task_id"]!=native


if __name__=="__main__":
    run()
    print("Concurrent submissions, refinements, scope isolation, follow-up ID, encrypted redelivery, and completed-task exclusion passed.")
