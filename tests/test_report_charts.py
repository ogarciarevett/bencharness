import json
from pathlib import Path
import tempfile
import unittest
import shutil
import subprocess
from unittest import mock
from bench import report, verify
from test_charts import trial


class ReportCharts(unittest.TestCase):
    def test_verifier_rejects_changed_or_missing_chart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "bench").mkdir()
            for name in ("__init__.py", "charts.py", "report.py"):
                shutil.copy2(report.ROOT / "bench" / name, root / "bench" / name)
            (root / "results").mkdir()
            source = root / "results/trials.jsonl"
            source.write_text(json.dumps(trial()) + "\n")
            (root / "README.md").write_text(f"# Title\n\nIntro.\n\n{report.START}\n{report.END}\n")
            with mock.patch.object(report, "ROOT", root), mock.patch.object(report, "STORE", source):
                report.write()
            def git(*args):
                subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
            git("init", "-q")
            git("add", ".")
            def commit():
                git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture")
            commit()
            with mock.patch.object(verify, "ROOT", root):
                self.assertEqual(verify.check_generated("HEAD"), [])
                image = root / "docs/charts/latest/summary.svg"
                image.write_text(image.read_text().replace("Recorded benchmark summary", "Forged summary"))
                git("add", "."); commit()
                self.assertTrue(any("summary.svg" in p for p in verify.check_generated("HEAD")))
                image.unlink(); git("add", "-u"); commit()
                self.assertTrue(any("summary.svg" in p for p in verify.check_generated("HEAD")))
                sentinel = root / "sentinel"
                sentinel.write_text("must not change")
                image.symlink_to(sentinel)
                git("add", "."); commit()
                self.assertTrue(any("regular files" in p for p in verify.check_generated("HEAD")))
                self.assertEqual(sentinel.read_text(), "must not change")
        self.assertTrue(verify.allowed("docs/charts/latest/summary.svg"))
        self.assertFalse(verify.allowed("docs/charts/execute.py"))

    def test_report_refreshes_images_preserves_prose_and_keeps_raw_appendix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "trials.jsonl"
            source.write_text(json.dumps(trial()) + "\n")
            readme = root / "README.md"
            readme.write_text(f"# Owner title\n\nOwner introduction.\n\n{report.START}\nold table\n{report.END}\nOwner ending.\n")
            with mock.patch.object(report, "ROOT", root), mock.patch.object(report, "STORE", source):
                report.write()
                first = readme.read_text()
                report.write()
            self.assertEqual(first, readme.read_text())
            self.assertIn("Owner introduction.", first)
            self.assertIn("Owner ending.", first)
            self.assertIn("docs/charts/latest/summary.svg", first)
            self.assertNotIn("| Trial |", first)
            self.assertIn("| Trial |", (root / "RESULTS.md").read_text())
            self.assertTrue((root / "docs/charts/latest/summary.svg").exists())


