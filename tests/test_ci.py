"""CI accepts only the newest run of every name for the checked commit."""
import contextlib
import io
import json
import subprocess
import unittest
from unittest.mock import patch

from bench import ci

HEAD = "a" * 40


def check(name, number, conclusion="success", head=HEAD, status="completed"):
    return {"name": name, "id": number, "head_sha": head, "status": status, "conclusion": conclusion}


def passing():
    return [check(name, i) for i, name in enumerate(sorted(ci.REQUIRED), 1)]


class CheckSelection(unittest.TestCase):
    def test_real_shape_old_failed_verify_new_pass_two_skipped_refresh(self):
        runs = [check("evidence / refresh", 121050931866, "skipped"),
                check("evidence / verify", 121050931058),
                check("evidence / refresh", 121030270563, "skipped"),
                check("evidence / verify", 121030269765, "failure"),
                check("clean-install", 121030269179),
                check("test", 121030269085),
                check("homebrew-install", 121030268982)]
        self.assertEqual(ci.status(runs, HEAD), "success")
        self.assertEqual(ci.status(list(reversed(runs)), HEAD), "success")

    def test_latest_failure_or_cancel_blocks_even_with_older_success(self):
        for result in ("failure", "cancelled", "timed_out", "action_required", None):
            with self.subTest(result=result):
                self.assertEqual(ci.status(passing() + [check("test", 100, result)], HEAD), "failure")

    def test_missing_required_and_newer_pending_block(self):
        self.assertEqual(ci.status([], HEAD), "pending")
        self.assertEqual(ci.status(passing()[1:], HEAD), "pending")
        self.assertEqual(ci.status(passing() + [check("test", 100, None, status="in_progress")], HEAD), "pending")

    def test_wrong_head_cannot_supply_or_override_checked_results(self):
        self.assertEqual(ci.status([check(name, 100, head="b" * 40) for name in ci.REQUIRED], HEAD), "pending")
        self.assertEqual(ci.status(passing() + [check("test", 100, "failure", head="b" * 40)], HEAD), "success")

    def test_additional_check_failure_blocks_and_skipped_is_neutral(self):
        self.assertEqual(ci.status(passing() + [check("extra", 100, "failure")], HEAD), "failure")
        for result in ("skipped", "neutral"):
            self.assertEqual(ci.status(passing() + [check("extra", 100, result)], HEAD), "success")

    def test_cli_fetches_all_pages_for_exact_head(self):
        output = io.StringIO()
        rows = passing()
        with patch.object(ci.subprocess, "check_output", return_value=json.dumps([
            {"check_runs": rows[:2]}, {"check_runs": rows[2:]},
        ])) as fetch, contextlib.redirect_stdout(output):
            ci.main("example/bencharness", HEAD)
        self.assertEqual(output.getvalue(), "success\n")
        command = fetch.call_args[0][0]
        self.assertIn("--paginate", command)
        self.assertIn("--slurp", command)
        self.assertIn(f"repos/example/bencharness/commits/{HEAD}/check-runs?per_page=100", command)

    def test_fetch_and_malformed_payload_fail_closed(self):
        for error in (subprocess.CalledProcessError(1, "gh"), ValueError("bad json"), KeyError("check_runs")):
            output = io.StringIO()
            with patch.object(ci.subprocess, "check_output", side_effect=error), contextlib.redirect_stdout(output):
                ci.main("example/bencharness", HEAD)
            self.assertEqual(output.getvalue(), "pending\n")
