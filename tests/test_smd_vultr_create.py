"""Exercise the approved SMD Vultr create flow with a simulated provider."""

import base64
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from smd_vultr_create import CreateUnconfirmed, PLAN, REGION, create_or_reconcile

JOB = "ec0935d4-eb79-4ac2-b78d-e05db0faf693"
TAG = "smd-" + JOB
INSTANCE = "ffa2cf3c-883f-4ac7-ac38-e422520ef932"
BOOTSTRAP = base64.b64encode(b"#cloud-config\n").decode()
OFFER = {"id": PLAN, "vcpu_count": 4, "ram": 8192, "monthly_cost": 40}


class VultrFixture:
    def __init__(self):
        self.instances = []
        self.offer = OFFER.copy()
        self.fail_reply = False
        self.posts = []

    def request(self, method, path, body=None):
        if method == "POST":
            assert path == "/instances"
            self.posts.append(body)
            self.instances.append({"id": INSTANCE, **body})
            if self.fail_reply:
                raise TimeoutError("simulated lost response")
            return {"instance": {"id": INSTANCE}}
        if path.startswith("/instances?"):
            return {"instances": self.instances}
        if path.startswith("/plans?"):
            return {"plans": [self.offer]}
        if path == "/regions/atl/availability":
            return {"available_plans": [PLAN]}
        raise AssertionError(path)


class VultrCreateTests(unittest.TestCase):
    def setUp(self):
        self.vultr = VultrFixture()
        self.state = {}
        self.saved = []

    def run_claim(self, request=None, persist=None):
        return create_or_reconcile(request or self.vultr.request, JOB, self.state,
                                   persist or (lambda state: self.saved.append(state.copy())), BOOTSTRAP)

    def test_persists_intent_before_creating_exactly_one_approved_vm(self):
        def request(method, path, body=None):
            if method == "POST":
                self.assertEqual(self.saved, [{"tag": TAG, "create_started": True}])
            return self.vultr.request(method, path, body)

        self.assertEqual(self.run_claim(request=request), INSTANCE)
        self.assertEqual(self.run_claim(), INSTANCE)
        self.assertEqual(self.vultr.posts, [{
            "region": REGION, "plan": PLAN, "os_id": 2284,
            "label": TAG, "tags": [TAG], "user_data": BOOTSTRAP,
            "block_devices": [], "backups": "disabled", "ddos_protection": False,
        }])

    def test_lost_response_reconciles_after_restart_without_second_create(self):
        self.vultr.fail_reply = True
        with self.assertRaises(CreateUnconfirmed):
            self.run_claim()
        self.state = self.saved[-1].copy()
        instances, self.vultr.instances = self.vultr.instances, []
        with self.assertRaises(CreateUnconfirmed):
            self.run_claim()
        self.vultr.instances = instances
        self.assertEqual(self.run_claim(), INSTANCE)
        self.assertEqual(len(self.vultr.posts), 1)

    def test_offer_change_prevents_creation(self):
        self.vultr.offer["monthly_cost"] = 48
        with self.assertRaisesRegex(ValueError, "approved offer"):
            self.run_claim()
        self.assertEqual(self.saved, [])
        self.assertEqual(self.vultr.posts, [])

    def test_failed_persistence_prevents_creation(self):
        def unavailable(state):
            raise OSError("journal unavailable")

        with self.assertRaises(OSError):
            self.run_claim(persist=unavailable)
        self.assertEqual(self.vultr.posts, [])

    def test_conflicting_provider_tag_prevents_creation_or_adoption(self):
        self.vultr.instances.append({"id": INSTANCE, "label": "wrong", "tags": [TAG],
                                    "region": REGION, "plan": PLAN})
        with self.assertRaisesRegex(CreateUnconfirmed, "unexpected"):
            self.run_claim()
        self.assertEqual(self.vultr.posts, [])


if __name__ == "__main__":
    unittest.main()
