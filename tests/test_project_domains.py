import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from subprocess import CompletedProcess, TimeoutExpired

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))
from dns_inspection import verification_txt_answer, verification_hostname, txt_matches


class DomainVerificationTests(unittest.TestCase):
    def test_reservation_requires_hostname(self):
        self.assertEqual(verification_hostname(" APP.Example.COM. "), "app.example.com")
        for value in ("*.example.com", "127.0.0.1", "https://example.com", "a.123"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                verification_hostname(value)

    def test_requires_exact_txt_owner_and_token(self):
        name = "_smartdev-verification.app.example.com"
        wrong = ('elsewhere.example.com. 60 IN TXT "token_abc"\n'
                 f'{name}. 60 IN TXT "token_abc_extra"\n'
                 f'{name}. 60 IN TXT "token_" "abc"\n')
        self.assertFalse(txt_matches(wrong, name, "token_abc"))
        self.assertTrue(txt_matches(wrong + f'{name.upper()}. 60 IN TXT "token_abc"', name, "token_abc"))

    def test_distinguishes_missing_record_from_resolver_failure(self):
        with patch("dns_inspection.subprocess.run", return_value=CompletedProcess([], 0, "status: NXDOMAIN,")):
            self.assertEqual(verification_txt_answer("_smartdev-verification.example.com"), "status: NXDOMAIN,")
        for side_effect in (TimeoutExpired("dig", 4), OSError("missing binary")):
            with patch("dns_inspection.subprocess.run", side_effect=side_effect), self.assertRaises(RuntimeError):
                verification_txt_answer("_smartdev-verification.example.com")
        with patch("dns_inspection.subprocess.run", return_value=CompletedProcess([], 0, "status: SERVFAIL,")):
            with self.assertRaises(RuntimeError):
                verification_txt_answer("_smartdev-verification.example.com")
