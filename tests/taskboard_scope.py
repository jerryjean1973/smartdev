"""Check command-center receipt scope and persistent numbers on a disposable database."""
from pathlib import Path
from uuid import uuid4
from mcp_request_history import board_requests, board_request, lookup,workspace_requests
from fastapi import HTTPException
from store import database

def run():
    import os
    import psycopg
    from psycopg.conninfo import make_conninfo
    name='cc_board_test_'+uuid4().hex
    original=os.environ['DATABASE_URL']
    with psycopg.connect(original,autocommit=True) as admin:
        admin.execute('CREATE DATABASE '+name+' TEMPLATE template0')
        try:
            os.environ['DATABASE_URL']=make_conninfo(original,dbname=name)
            assertions()
        finally:
            os.environ['DATABASE_URL']=original
            admin.execute('DROP DATABASE '+name)


def assertions():
    central,other,first,second,hidden,legacy=[uuid4() for _ in range(6)]
    with database() as db:
        for sql in sorted(Path('/app').glob('[0-9]*.sql')):
            db.execute(sql.read_text())
        for pid in (central,other):
            db.execute("INSERT INTO projects(id,name,repository,runtime,status) VALUES(%s,'test','test','test','created')",(pid,))
        db.execute("INSERT INTO taskboard_platform_project VALUES (%s)",(central,))
        def put(pid,request,kind,matched=None):
            db.execute("""INSERT INTO mcp_task_request(project_id,request_id,kind,matched_request,delivery,instruction)
                VALUES (%s,%s,%s,%s,'received','Global platform: arbitrary text cannot route workspace data')""",(pid,request,kind,matched))
        put(central,first,'message')
        put(central,second,'message',first)
        put(other,hidden,'message')
        put(central,legacy,'submission')
    entries=board_requests()
    assert {r['request_id'] for r in entries}=={first,second}
    assert len({r['task_number'] for r in entries})==1 and entries[0]['task_number'].startswith('CC-')
    assert entries[0]['actor'] is None
    assert board_requests(other)==[] and lookup(hidden,[other])['destination']=='Workspace'
    assert [r['request_id'] for r in workspace_requests(other)]==[hidden]
    for pid,ref in [(other,hidden),(central,hidden),(central,legacy)]:
        try: board_request(str(ref),pid)
        except HTTPException as exc: assert exc.status_code==404
        else: raise AssertionError('Unscoped receipt leaked')
    saved=entries[0]['task_number']
    with database() as db: db.execute(Path('/app/061_taskboard_identity.sql').read_text())
    assert all(item['task_number']==saved for item in board_requests())
    print('PASS scope, forged scope text, legacy workspace history, project isolation, filtered counts, matched follow-up and stable numbers')



if __name__=='__main__':
    run()
