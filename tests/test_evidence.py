"""Public evidence contains verified counts, never raw run output."""
import contextlib
import io
import unittest
from unittest.mock import patch

from bench import evidence


class Evidence(unittest.TestCase):
    def render(self, status):
        output = io.StringIO()
        def verify(*args):
            print("private /Users/operator/run token=secret")
            return status
        with patch.object(evidence.verify, "main", side_effect=verify), \
             patch.object(evidence.verify, "show", side_effect=["old\n", "old\nnew\nextra\n", "", "record\n"]), \
             contextlib.redirect_stdout(output):
            result = evidence.main("b" * 40, "a" * 40, "/private/runs")
        return result, output.getvalue()

    def test_verified_counts_and_current_head_without_private_output(self):
        result, body = self.render(0)
        self.assertEqual(result, 0)
        self.assertIn("## Evidence (head aaaaaaa)", body)
        self.assertIn("2 new candidate(s); 1 new record(s)", body)
        self.assertIn("Verification: PASS", body)
        self.assertNotIn("/Users/", body)
        self.assertNotIn("/private/", body)
        self.assertNotIn("token=", body)

    def test_failed_verification_has_no_evidence_marker(self):
        result, body = self.render(1)
        self.assertEqual(result, 1)
        self.assertNotIn("Evidence (head", body)
        self.assertNotIn("Verification: PASS", body)
