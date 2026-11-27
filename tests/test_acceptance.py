import hashlib
import sys
import unittest
from pathlib import Path
from uuid import UUID

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"api"))
from acceptance import allowed_submission, fixture_binding
from core import workspace_name


class AcceptanceIdentityTests(unittest.TestCase):
    def setUp(self):
        self.owner = UUID(int=1)
        self.fixture = UUID(int=2)
        self.projects = {self.fixture}
        self.token = "test-identity-credential-fixture-00001"
        self.digest = hashlib.sha256(self.token.encode()).hexdigest()

    def allowed(self, project, credential):
        return allowed_submission(project, credential, self.projects, self.digest)

    def test_user_main_accepts_normal_identity(self):
        self.assertTrue(self.allowed(self.owner,None))

    def test_automation_cannot_submit_to_user_main(self):
        self.assertFalse(self.allowed(self.owner,self.token))

    def test_fixture_requires_separate_valid_identity(self):
        self.assertTrue(self.allowed(self.fixture,self.token))
        self.assertFalse(self.allowed(self.fixture,None))
        self.assertFalse(self.allowed(self.fixture,self.token+"bad"))

    def test_default_configuration_fails_closed_for_test_requests(self):
        self.assertFalse(allowed_submission(self.owner,self.token,set(),""))
        self.assertFalse(allowed_submission(self.fixture,self.token,self.projects,""))
        self.assertTrue(allowed_submission(self.owner,None,set(),""))

    def test_fixture_binding_requires_every_fixture_field(self):
        workspace = workspace_name(self.fixture)
        conversation = UUID(int=3)
        row = {"conversation_id": conversation, "closed_at": None, "status": "created",
               "workspace_url": f"https://coder.invalid/@validation/{workspace}",
               "token_digest": self.digest}
        self.assertTrue(fixture_binding(self.fixture, row, self.token, str(conversation), workspace))
        self.assertFalse(fixture_binding(self.fixture, None, self.token, str(conversation), workspace))
        self.assertFalse(fixture_binding(self.fixture, row, self.token, None, workspace))
        self.assertFalse(fixture_binding(self.fixture, row, self.token, str(conversation), None))
        self.assertFalse(fixture_binding(self.owner, row, self.token, str(conversation), workspace))
        self.assertFalse(fixture_binding(self.fixture, row, None, str(conversation), workspace))
        self.assertFalse(fixture_binding(self.fixture, {**row, "closed_at": True},
                                         self.token, str(conversation), workspace))
        self.assertFalse(fixture_binding(self.fixture, {**row, "workspace_url": row["workspace_url"] + "-other"},
                                         self.token, str(conversation), workspace))
