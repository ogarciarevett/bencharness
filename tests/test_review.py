"""Exercise the morning merge gate without a model or GitHub access."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

FAKE = r"""#!/usr/bin/env python3
import json, os, pathlib, sys
name, args = pathlib.Path(sys.argv[0]).name, sys.argv[1:]
with open(os.environ["CALLS"], "a") as out:
    out.write(json.dumps([name, *args]) + "\n")
if name == "git":
    if "get-url" in args: print("https://github.com/example/bencharness.git")
    elif "rev-parse" in args: print("a" * 40)
    elif "diff" in args: print("pinned diff")
elif name == "python3": print("PASS: verified")
elif name == "gh":
    if args[:2] == ["pr", "list"]: print("9 nightly/test")
    elif args[:2] == ["pr", "diff"]: print("unbound diff")
    elif args[:1] == ["api"]: print("success")
elif name == "codex":
    pathlib.Path(args[args.index("-o") + 1]).write_text(os.environ["VERDICT"] + "\n")
    sys.exit(int(os.environ.get("REVIEW_EXIT", "0")))
"""


class MorningReview(unittest.TestCase):
    def run_review(self, verdict="VERDICT: PASS", exit_code=0):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "schedule").mkdir()
            (root / "runs").mkdir()
            (root / "bin").mkdir()
            shutil.copy2(ROOT / "schedule/review.sh", root / "schedule/review.sh")
            # Integration of the real safety wrapper is covered in test_safety.
            wrapper = root / "schedule/codex-safe"
            wrapper.write_text('#!/bin/sh\nexec codex "$@"\n')
            wrapper.chmod(0o755)
            for name in ("git", "gh", "codex", "python3"):
                path = root / "bin" / name
                path.write_text(FAKE)
                path.chmod(0o755)
            # The mock interpreter needs the real Python before the fake python3 in PATH.
            import sys
            for path in (root / "bin").iterdir():
                path.write_text(path.read_text().replace("#!/usr/bin/env python3", "#!" + sys.executable))
            calls = root / "calls.jsonl"
            env = {**os.environ, "PATH": str(root / "bin") + ":" + os.environ["PATH"],
                   "CALLS": str(calls), "VERDICT": verdict, "REVIEW_EXIT": str(exit_code), "BENCH_REPORT_CMD": "cat"}
            done = subprocess.run(["sh", str(root / "schedule/review.sh")], env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(done.returncode, 0, done.stderr)
            return [json.loads(line) for line in calls.read_text().splitlines()]

    def test_success_reviews_pinned_diff_and_merges_exact_head(self):
        calls = self.run_review()
        self.assertFalse(any(c[:3] == ["gh", "pr", "diff"] for c in calls))
        self.assertTrue(any(c[0] == "git" and "diff" in c and "a" * 40 in " ".join(c) for c in calls))
        review = next(c for c in calls if c[0] == "codex")
        self.assertTrue((ROOT / "schedule/codex-safe").is_file())
        self.assertIn("features.apps=false", review)
        self.assertIn("features.multi_agent=false", review)
        self.assertEqual(review[review.index("-s") + 1], "read-only")
        merge = next(c for c in calls if c[:3] == ["gh", "pr", "merge"])
        self.assertEqual(merge[merge.index("--match-head-commit") + 1], "a" * 40)

    def test_reviewer_failure_cannot_merge_even_with_pass_file(self):
        calls = self.run_review(exit_code=1)
        self.assertFalse(any(c[:3] == ["gh", "pr", "merge"] for c in calls))

    def test_only_exact_pass_can_merge(self):
        for verdict in ("VERDICT: FAIL bad", "VERDICT: PASSIVE", "VERDICT: PASS extra", ""):
            with self.subTest(verdict=verdict):
                calls = self.run_review(verdict)
                self.assertFalse(any(c[:3] == ["gh", "pr", "merge"] for c in calls))
