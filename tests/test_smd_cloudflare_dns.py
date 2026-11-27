"""Cloudflare API and runner authorization behavior with an offline fixture."""

import copy
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import UUID
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from smd_cloudflare_dns import DNSBlocked, DeploymentDNS, expected_hosts
from smd_vultr_create import CreateUnconfirmed, instance_ipv4

TASK = "72f92907-4914-4819-a60e-21d4d6973d48"
PROJECT = "c3975e38-af32-42eb-a209-d7902adc56d9"
INSTANCE = "d14fa3a2-ded0-4b76-8fa9-61721d8e1948"
ZONE = "smd.example.test"
ZONE_ID = "1" * 32
PORTAL = f"smd-{UUID(TASK).hex}.{ZONE}"
CODER = f"coder-{UUID(TASK).hex}.{ZONE}"
APP = f"app-{UUID(PROJECT).hex[:12]}-{UUID(TASK).hex[:12]}.{ZONE}"
IP = "8.8.4.4"
CLAIM = {"id": TASK, "dns_zone": ZONE, "deployment_hostname": PORTAL, "coder_hostname": CODER,
         "projects": [{"id": PROJECT, "hostname": APP}]}


class CloudflareFixture:
    def __init__(self):
        self.rows = []
        self.writes = []
        self.lose_reply = False
        self.zone = ZONE

    def __call__(self, method, path, body=None):
        if path == f"/zones/{ZONE_ID}":
            return {"result": {"id": ZONE_ID, "name": self.zone, "status": "active"}}
        expected_path = f"/zones/{ZONE_ID}/dns_records"
        url = urlsplit(path)
        assert url.path == expected_path
        if method == "GET":
            parameters = parse_qs(url.query)
            rows = [row for row in self.rows if row["name"] == parameters["name.exact"][0]]
            return {"result": rows, "result_info": {"page": 1, "total_pages": 1}}
        assert method == "POST"
        row = {"id": f"{len(self.rows) + 1:032x}", **body}
        self.rows.append(row)
        self.writes.append(row)
        if self.lose_reply:
            raise DNSBlocked("Cloudflare reply lost.")
        return {"result": row}


class CloudflareDNSTest(unittest.TestCase):
    def setUp(self):
        self.provider = CloudflareFixture()
        self.dns = DeploymentDNS(self.provider, ZONE, ZONE_ID)
        self.state = {}
        self.snapshots = []

    def assign(self, ip=IP):
        self.dns.assign(CLAIM, ip, self.state, lambda: self.snapshots.append(copy.deepcopy(self.state)))

    def test_assigns_only_pinned_hosts_and_preserves_owned_state_across_reload(self):
        self.dns.preflight(CLAIM)
        self.assertEqual(self.provider.writes, [])
        self.assign()
        self.assertEqual([row["name"] for row in self.provider.writes], [PORTAL, CODER, APP])
        for row in self.provider.writes:
            self.assertEqual(row["type"], "A")
            self.assertFalse(row["proxied"])
            self.assertEqual(row["comment"], "smartdev:smd:" + TASK)
            self.assertEqual(row["content"], IP)
            self.assertTrue(any(snap.get(row["name"]) == {"create_started": True} for snap in self.snapshots))
        self.state = self.snapshots[-1]
        self.assign()
        self.assertEqual(len(self.provider.writes), 3)

    def test_conflicting_third_party_record_stops_all_writes(self):
        self.provider.rows.append({"id": "3" * 32, "name": APP, "type": "AAAA"})
        with self.assertRaisesRegex(DNSBlocked, "conflicts"):
            self.assign()
        self.assertEqual(self.provider.writes, [])

    def test_managed_dev_origins_are_scoped_to_each_project_and_deployment(self):
        from uuid import uuid4
        second=str(uuid4())
        entries=[]
        for project in (PROJECT,second):
            part=UUID(project).hex[:12]+"-"+UUID(TASK).hex[:12]+"."+ZONE
            entries.append({"id":project,"hostname":"app-"+part,"preview_hostname":"dev-"+part})
        hosts=expected_hosts({**CLAIM,"projects":entries},ZONE)
        self.assertEqual(len(set(hosts)),6)
        entries[0]["preview_hostname"]=entries[1]["preview_hostname"]
        with self.assertRaises(DNSBlocked):
            expected_hosts({**CLAIM,"projects":entries},ZONE)

    def test_zone_and_hostname_mismatches_and_nonpublic_ip_are_rejected(self):
        self.provider.zone = "unconfigured.example.test"
        with self.assertRaisesRegex(DNSBlocked, "active DNS zone"):
            self.assign()
        with self.assertRaisesRegex(DNSBlocked, "pinned deployment hostname"):
            expected_hosts({**CLAIM, "coder_hostname": "malicious.example.test"}, ZONE)
        with self.assertRaisesRegex(ValueError, "public unicast"):
            self.assign("192.168.1.4")
        self.assertEqual(self.provider.writes, [])

    def test_ambiguous_create_reconciles_without_repeat(self):
        self.provider.lose_reply = True
        with self.assertRaisesRegex(DNSBlocked, "reply lost"):
            self.assign()
        visible, self.provider.rows = self.provider.rows, []
        self.provider.lose_reply = False
        with self.assertRaisesRegex(DNSBlocked, "unconfirmed"):
            self.assign()
        self.provider.rows = visible
        self.assign()
        self.assertEqual([row["name"] for row in self.provider.writes], [PORTAL, CODER, APP])

    def test_provider_ip_is_bound_to_exact_instance_and_plan(self):
        tag = "smd-" + TASK
        instance = {"id": INSTANCE, "label": tag, "tags": [tag], "region": "atl",
                    "plan": "vc2-4c-8gb", "os_id": 2284, "status": "active",
                    "power_status": "running", "server_status": "ok", "main_ip": IP}

        def read(method, path):
            self.assertEqual((method, path), ("GET", f"/instances/{INSTANCE}"))
            return {"instance": instance}

        self.assertEqual(instance_ipv4(read, TASK, INSTANCE), IP)
        instance["server_status"] = "installing"
        self.assertIsNone(instance_ipv4(read, TASK, INSTANCE))
        instance["label"] = "unrelated-host"
        with self.assertRaisesRegex(CreateUnconfirmed, "exact authorized"):
            instance_ipv4(read, TASK, INSTANCE)


if __name__ == "__main__":
    unittest.main()
