"""Match Taskboard work history to the task's public assistant messages."""
import io
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "operator"))
from task_work_history import project_turn
from work_messages import visible_text


class WorkHistory(unittest.TestCase):
    def test_host_includes_only_the_assigned_turns_visible_messages(self):
        def event(kind, payload):
            return json.dumps({"type": kind, "payload": payload}) + "\n"
        def start(turn):
            return event("event_msg", {"type": "task_started", "turn_id": turn})
        def message(role, phase, value):
            return event("response_item", {"type": "message", "role": role, "phase": phase,
                                          "content": [{"type": "output_text", "text": value}]})
        transcript = start("other") + message("assistant", "commentary", "other task") + start("selected")
        transcript += message("user", "commentary", "owner input") + message("assistant", "analysis", "hidden")
        transcript += "".join(message("assistant", "commentary", str(i)) for i in range(31))
        transcript += message("assistant", "final_answer", "finished") + start("next") + message("assistant", "commentary", "wrong task")
        messages, active, ended = project_turn(io.StringIO(transcript), "selected")
        self.assertEqual([m["label"] for m in messages], [str(i) for i in range(31)] + ["finished"])
        self.assertFalse(active)
        self.assertTrue(ended)

    def test_ask_cc_hides_memory_and_handles_incremental_updates(self):
        self.assertEqual(visible_text('{"reply":"public","memory":"private"}', structured_reply=True), "public")
        self.assertEqual(visible_text('{"memory":"partial', structured_reply=True), "")
        self.assertNotIn("abcdef1234567890123456", visible_text("{incomplete sk-abcdef1234567890123456", structured_reply=True))
        with TemporaryDirectory() as folder:
            os.environ.setdefault("STATE_DIRECTORY", folder)
            os.environ.setdefault("RUNTIME_DIRECTORY", folder)
            from cc_codex import read_activity
            path = Path(folder) / "events.jsonl"
            events = [{"type": "item.completed", "item": {"id": str(i), "type": "agent_message", "text": str(i)}} for i in range(31)]
            events.extend([{"type":"item.updated","item":{"id":"last","type":"agent_message","text":"partial"}},
                           {"type":"item.completed","item":{"id":"last","type":"agent_message","text":"complete"}},
                           {"type":"item.completed","item":{"id":"hidden","type":"reasoning","text":"hidden"}}])
            path.write_text("\n".join(map(json.dumps, events)))
            self.assertEqual([item["label"] for item in read_activity(path)], [str(i) for i in range(31)] + ["complete"])
