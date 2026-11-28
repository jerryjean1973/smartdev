"""Exercise receipt projection, retries and project isolation in temporary PostgreSQL tables."""
from contextlib import contextmanager
import json
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import UUID, uuid4

import mcp_request_history as history
import mcp_intake
from external_mcp_tasks import ScopedTasks, WorkspaceUnavailable
from external_mcp_transport import dispatch
from store import database


def run():
    known=UUID('bca501cf-7f58-4cd6-b254-33bf4fe48c7c')
    assert history.execution_reference(known,'3af2baff-d0c2-43f8-92ce-59f62edb932e')=='451cc5ae4bdd71d5c1d67b0d'
    a,b,t,owner,client_id,message,execution=[uuid4() for _ in range(7)]
    with database() as db:
        db.execute('CREATE TEMP TABLE projects(id uuid PRIMARY KEY,name text,archived boolean default false)')
        db.execute('CREATE TEMP TABLE acceptance_fixture(project_id uuid)')
        db.execute('CREATE TEMP TABLE objective_contract(id uuid,project_id uuid,display_name text,business_objective text,status text,original_request text,created_at timestamptz,criteria jsonb,version integer)')
        db.execute('CREATE TEMP TABLE objective_run AS SELECT * FROM public.objective_run WITH NO DATA')
        db.execute('CREATE TEMP TABLE objective_presentation(objective_id uuid,excluded_from_board boolean,reason text)')
        migration=Path('api/052_mcp_task_requests.sql').read_text().replace('CREATE TABLE IF NOT EXISTS','CREATE TEMP TABLE')
        db.execute(migration)
        db.execute('CREATE TEMP TABLE business_objective(id text PRIMARY KEY)')
        db.execute("INSERT INTO business_objective VALUES ('BO-01'),('BO-02')")
        db.execute(Path('api/053_mcp_request_scope.sql').read_text())
        db.execute(Path('api/055_mcp_task_lifecycle.sql').read_text().replace('CREATE TABLE IF NOT EXISTS','CREATE TEMP TABLE'))

        db.execute("CREATE TEMP TABLE external_mcp_grant(id uuid PRIMARY KEY)")
        db.execute(Path('api/059_mcp_intake.sql').read_text().replace('CREATE TABLE IF NOT EXISTS','CREATE TEMP TABLE'))
        db.execute("INSERT INTO projects(id,name) VALUES (%s,'QA A'),(%s,'QA B')",(a,b))
        db.execute("INSERT INTO objective_contract(id,project_id,display_name,business_objective,status) VALUES (%s,%s,'Task','BO-01','active')",(t,a))

        @contextmanager
        def connection():
            with db.transaction(): yield db

        with patch.object(history,'database',connection), patch.object(mcp_intake,'database',connection):
            client=ScopedTasks({'project_ids':[a],'owner_id':owner},projects=lambda:[{'id':a,'name':'A','status':'ready'}],
                task_navigation=lambda:[{'id':t,'project_id':a,'title':'Task','status':'Active'}])
            client._workspace=Mock(side_effect=WorkspaceUnavailable('Offline.'))
            pending=client.send_message(t,'Investigate task receipt.',client_id)
            assert pending['status']=='Pending' and pending['codex_task_id']==str(t)
            first,=history.board_requests()
            assert first['work_status']=='Pending' and first['delivery']=='unconfirmed' and first['task_id']==t and first['received_at']
            assert first['task_admitted'] is False
            try: client.send_message(t,'A different instruction.',client_id)
            except ValueError: pass
            else: raise AssertionError('Mismatched replay accepted.')
            assert first['scope_kind']=='unassigned' and first['origin_business_objective']=='BO-01'
            assert first['business_objective'] is None
            assert client._workspace.call_count==1
            result={'objective':str(t),'messageId':str(message),'conversationId':str(t),'executionId':str(execution)}
            history.confirm_submission(db,a,first['request_id'],message,result)
            ref=history.execution_reference(a,execution)
            assert history.lookup(ref,[a])['delivery']=='accepted'
            assert history.lookup(ref,[a])['task_admitted'] is True
            assert history.lookup(ref,[a])['work_status']=='Queued'
            db.execute("UPDATE mcp_task_request SET execution_phase='Processing' WHERE request_id=%s",(first['request_id'],))
            assert history.lookup(ref,[a])['work_status']=='Processing'
            db.execute("UPDATE mcp_task_request SET execution_phase='Processed',execution_code=2 WHERE request_id=%s",(first['request_id'],))
            assert history.lookup(ref,[a])['work_status']=='Processed'
            from fastapi import HTTPException
            try: history.complete_request(a,first['request_id'])
            except HTTPException as e: assert e.status_code==409
            else: raise AssertionError('Failed execution was marked done.')
            db.execute("UPDATE mcp_task_request SET execution_code=0 WHERE request_id=%s",(first['request_id'],))
            history.complete_request(a,first['request_id'])
            assert history.lookup(ref,[a])['work_status']=='Done'
            history.delivered(a,first['request_id'])  # late transport timeout
            assert history.lookup(ref,[a])['delivery']=='accepted'
            client._workspace=Mock(return_value={**result,'executionId':ref})
            assert client.send_message(t,'Investigate task receipt.',client_id)['execution_id']==ref
            assert len(history.board_requests())==1
            assert history.board_request(ref,a)['instruction']=='Investigate task receipt.'
            frame={'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'smartdev_get_receipt','arguments':{'receipt':ref}}}
            data=json.loads(dispatch(frame,client)['result']['content'][0]['text'])
            assert data['receipt']==ref and 'instruction' not in data
            try: history.lookup(ref,[b])
            except LookupError: pass
            else: raise AssertionError('Cross-project receipt available.')
            for declared,subject,expected in [
                (None,'P0 Ask CC bug in Taskboard','global'),
                ('BO-02','Assigned tenant field','objective'),
                ('global','Work in BO-01 conversation','global'),
            ]:
                scoped=uuid4()
                history.received(a,scoped,'message',subject,None,t,declared)
                row=history.board_request(str(scoped),a)
                assert row['scope_kind']==expected and row['origin_business_objective']=='BO-01'
                assert row['business_objective']==('BO-02' if declared=='BO-02' else None)
                try: history.received(a,scoped,'message',subject,None,t,'BO-01')
                except ValueError: pass
                else: raise AssertionError('Conflicting scope replay accepted.')
            second=uuid4()
            history.received(b,second,'create','Standalone ticket','Standalone ticket',None)
            assert history.board_request(str(second),b)['task_id'] is None
            db.execute("INSERT INTO objective_presentation VALUES (%s,true,'Removed from Taskboard by the owner.')",(t,))
            assert len(history.board_requests())==5
            db.execute('INSERT INTO acceptance_fixture VALUES (%s)',(b,))
            assert all(row['project_id']==a for row in history.board_requests())
            assert history.board_request(str(scoped),a)['work_status']=='Pending'


if __name__=='__main__':
    run()
    print('MCP request receipt integration checks passed.')
