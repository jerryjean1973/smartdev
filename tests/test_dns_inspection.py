import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))
from dns_inspection import domain_name, matching_zone, parse_nameservers


class DnsInspectionTests(unittest.TestCase):
    def test_domain_validation(self):
        self.assertEqual(domain_name(" Example.COM. "), "example.com")
        for invalid in ("-option.example", "example.com/path", "a..example", "@resolver.example", "localhost"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                domain_name(invalid)

    def test_parse_only_exact_zone_ns_answers(self):
        output = """example.com. 300 IN NS B.ns.example.
example.com. 300 IN NS a.ns.example.
other.example. 300 IN NS wrong.example.
example.com. 300 IN CNAME wrong.example.
"""
        self.assertEqual(parse_nameservers("example.com",output),["a.ns.example","b.ns.example"])

    def test_requires_unique_active_exact_match(self):
        zone = {"id":"a"*32,"status":"active","name":"example.com","name_servers":["A.ns.example.", "b.ns.example"]}
        expected = ["a.ns.example","b.ns.example"]
        self.assertEqual(matching_zone("example.com",expected,[zone]),zone["id"])
        self.assertIsNone(matching_zone("example.com",expected[:1],[zone]))
        self.assertIsNone(matching_zone("example.com",expected,[zone,dict(zone,id="b"*32)]))
        self.assertIsNone(matching_zone("example.com",expected,[dict(zone,status="pending")]))
