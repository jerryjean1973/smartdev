import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID


ROOT = Path(__file__).resolve().parents[1]


class ClaudeAdapterTests(unittest.TestCase):
    def test_stream_is_normalized_for_existing_chat_and_session_storage(self):
        with TemporaryDirectory() as temporary:
            fake = Path(temporary) / "claude"
            fake.write_text("""#!/usr/bin/env python3
import json,sys
args=sys.argv[1:]
session=args[args.index('--session-id')+1]
events=[
 {'type':'system','subtype':'init','session_id':session},
 {'type':'assistant','session_id':session,'message':{'content':[{'type':'tool_use','id':'tool-1','name':'Bash','input':{'command':'pytest -q'}}]}},
 {'type':'user','session_id':session,'message':{'content':[{'type':'tool_result','tool_use_id':'tool-1','content':'ok'}]}},
 {'type':'result','session_id':session,'result':'Done','is_error':False,'usage':{'input_tokens':2}},
]
for event in events: print(json.dumps(event),flush=True)
""")
            fake.chmod(0o755)
            environment = {**os.environ, "PATH": temporary + os.pathsep + os.environ.get("PATH", "")}
            result = subprocess.run(
                [sys.executable, str(ROOT / "workspace/claude-task-adapter"), "--mode", "workspace-write",
                 "--session-id", "00000000-0000-4000-8000-000000000001"],
                input="Fix the tests", text=True, capture_output=True, env=environment, check=True,
            )
        events = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(events[0]["type"], "thread.started")
        self.assertEqual(events[0]["agent"], "claude_code")
        UUID(events[0]["thread_id"])
        command = next(event for event in events if event["type"] == "item.started")
        self.assertEqual(command["item"]["command"], "pytest -q")
        completed = next(event for event in events if event["type"] == "item.completed" and event["item"].get("id") == "tool-1")
        self.assertEqual(completed["item"]["exit_code"], 0)
        self.assertEqual(events[-1]["type"], "turn.completed")


if __name__ == "__main__":
    unittest.main()
