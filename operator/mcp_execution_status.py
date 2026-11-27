"""Project exact workspace execution events onto registered MCP requests."""
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

API="smart-dev-ide-browser-api-1"
FETCH="""import json
from core import workspace_name
from store import database
with database() as db:
 rows=db.execute('''SELECT r.project_id,r.request_id,s.result->>'executionId' AS execution
 FROM mcp_task_request r JOIN conversation_submission s
 ON (s.task_project_id,s.request_id)=(r.project_id,r.request_id)
 WHERE s.result->>'executionId' IS NOT NULL AND r.owner_done_at IS NULL
 AND (r.execution_phase IS DISTINCT FROM 'Processed' OR r.waiting_for_input OR
      (r.execution_code=78 AND r.failure_reason IS NULL))''').fetchall()
 print(json.dumps([{**r,'workspace':workspace_name(r['project_id'])} for r in rows],default=str))
"""
SCAN="""import json,sys,re,pathlib
wanted=set(json.load(sys.stdin));root=pathlib.Path.home()/'.local/state/command-web-chat';out={}
for f in root.glob('*.log'):
 if not re.fullmatch('[0-9a-f]{64}\\.log',f.name) or not f.is_file() or f.is_symlink():continue
 with f.open(errors='replace') as stream:
  for line in stream:
   if '"smartdev.result"' not in line and '"smartdev.run_status"' not in line:continue
   try:e=json.loads(line)
   except ValueError:continue
   key=e.get('execution');kind=e.get('type');at=e.get('at')
   if key not in wanted or not isinstance(at,str) or not re.fullmatch(r'\\d{4}-\\d\\d-\\d\\dT[0-9:.]+Z',at):continue
   if key in out and out[key][0]>at:continue
   if kind=='smartdev.result' and type(e.get('code')) is int:out[key]=[at,'Processed',e['code'],bool(e.get('awaitingInput')),
    ('Codex could not start because Command Center could not read the tenant credentials from OpenBao.' if 'OpenBao' in line
     else 'Codex credential check failed before execution.' if e['code']==78 else None)]
   elif kind=='smartdev.run_status' and e.get('state')=='running':out[key]=[at,'Processing',None,False,None]
print(json.dumps(out))
"""
SAVE="""import sys,json
from store import database
with database() as db:
 for row in json.load(sys.stdin):
  db.execute('''UPDATE mcp_task_request SET execution_updated_at=%s,execution_phase=%s,execution_code=%s,waiting_for_input=%s,failure_reason=%s
    WHERE project_id=%s AND request_id=%s AND (execution_updated_at IS NULL OR execution_updated_at <= %s)''',(*row,row[0]))
"""
def run(args,data=None):
 return subprocess.check_output(args,input=data,text=True,timeout=50)
def main():
 rows=json.loads(run(['docker','exec',API,'python','-c',FETCH]))
 by_name={}
 for row in rows:by_name.setdefault(row['workspace'],{})[row['execution']]=row
 ids=run(['docker','ps','-q','--filter','name=command-dev-']).split()
 def scan(cid):
  name=run(['docker','exec',cid,'printenv','PROJECT_NAME']).strip()
  wanted=by_name.get(name,{})
  if not wanted:return []
  result=json.loads(run(['docker','exec','-i','-u','1000',cid,'python3','-c',SCAN],json.dumps(list(wanted))))
  return [[*values,wanted[key]['project_id'],wanted[key]['request_id']] for key,values in result.items() if key in wanted]
 with ThreadPoolExecutor(max_workers=4) as pool:updates=[r for group in pool.map(scan,ids) for r in group]
 if updates:run(['docker','exec','-i',API,'python','-c',SAVE],json.dumps(updates))
if __name__=='__main__':main()
