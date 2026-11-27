import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"api"))
from task_titles import backfill_titles, task_title, valid_title


class TaskTitleTests(unittest.TestCase):
    def test_json_objective_returns_safe_topic_label(self):
        objective = '{"leads":[{"account_id":848,"email":"private@example.com","content":"private payload"}]}'
        self.assertEqual(task_title(objective),"CRM lead processing")
        self.assertEqual(task_title('{"token":"secret"}'),"Project development task")

    def test_generated_titles_are_bounded_and_deterministic(self):
        for objective, expected in [
            ("Run the first five retained leads through native BRIPe and return projections","CRM lead forecast"),
            ("Fix SmartDev task navigation and layout","Task navigation redesign"),
            ("Show me the next 5.5 layers","Project architecture review"),
            ("Unrecognized prompt with a private account name","Project development task"),
        ]:
            self.assertEqual(task_title(objective),expected)
            self.assertTrue(valid_title(expected))
            self.assertLessEqual(len(expected.split()),6)

    def test_task_heading_takes_precedence_over_crm_terms_in_body(self):
        expected = "IMPLEMENT EXPERT-DRIVEN VISUAL INTELLIGENCE IN ODP STUDIO"
        document = "# CODEX BUILD — " + expected + "\n\n## Objective\nCRM lead forecast projections"
        self.assertEqual(task_title(document), expected)
        self.assertEqual(task_title("TASK: Implement visual intelligence in ODP Studio\nLead forecast"),
                         "Implement visual intelligence in ODP Studio")

    def test_rename_guard_accepts_a_label_rejects_payloads(self):
        self.assertTrue(valid_title("CRM lead forecast and qualification"))
        for label in ('{"leads":[]}',"https://example.com", "person@example.com", "sk-12345678901234567890",
                      "line one\nline two", "word "*14):
            with self.subTest(label=label):
                self.assertFalse(valid_title(label))

    def test_title_backfill_preserves_intentional_rename(self):
        db=Mock()
        db.execute.return_value.fetchall.return_value=[
            {"id":"old","original_request":"Improve task navigation","display_name":'{"broken":true}'},
            {"id":"renamed","original_request":"Keep history","display_name":"Owner chosen title"},
        ]
        backfill_titles(db)
        self.assertEqual(db.execute.call_count,2)
        statement,values=db.execute.call_args.args
        self.assertIn("UPDATE objective_contract SET display_name",statement)
        self.assertEqual(values,("Task navigation redesign","old"))
