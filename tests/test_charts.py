import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

from bench import charts, report


def trial(name="first", date="2026-10-23T19:00:00Z"):
    return {"trial": name, "recorded_utc": date, "suite": "fixed suite", "memory": "fixed memory",
            "bench": "BENCH model=evaluated medium standard tier, A=base@pin, B=other@pin, 1200 active s per task side",
            "tasks": {"task": {"verdict": "tie", "results": {
                "base": {"status": "complete", "score": .75},
                "other": {"status": "complete", "score": .75}}}}}


class Charts(unittest.TestCase):
    def test_generated_symlinks_cannot_write_outside_the_gallery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root / "outside"
            outside.write_text("owned sentinel")
            gallery = root / "charts"
            (gallery / "latest").mkdir(parents=True)
            (gallery / "latest/summary.svg").symlink_to(outside)
            with self.assertRaisesRegex(ValueError, "regular files"):
                charts.write([trial()], gallery)
            self.assertEqual(outside.read_text(), "owned sentinel")
            (gallery / "latest/summary.svg").unlink()
            (gallery / "linked").symlink_to(root, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "regular files"):
                charts.write([], gallery)
            self.assertEqual(outside.read_text(), "owned sentinel")

    def test_labels_metadata_and_escaping_match_source(self):
        row = trial('<run & "quoted">')
        node = charts.leaderboard(row, "task")
        text = ET.tostring(node, encoding="unicode")
        self.assertIn("&lt;run &amp;", text)
        self.assertEqual(text.count(">0.7500<"), 2)
        self.assertEqual(json.loads(node.find("metadata").text), row)
        self.assertIn("prefers-color-scheme:dark", text)
        self.assertEqual(len(node.findall("rect")), 2)

    def test_missing_failed_and_zero_are_distinct(self):
        row = trial()
        results = row["tasks"]["task"]["results"]
        results["base"]["score"] = 0
        results["other"] = {"status": "error", "score": .9}
        node = charts.leaderboard(row, "task")
        text = ET.tostring(node, encoding="unicode")
        self.assertIn(">0.0000<", text)
        self.assertIn(">not scored<", text)
        self.assertEqual(len(node.findall("rect")), 1)
        for value in (float("nan"), float("inf"), True, -1, 2):
            with self.subTest(value=value), self.assertRaises(ValueError):
                charts.score({"status": "complete", "score": value})

    def test_trends_order_time_and_separate_settings(self):
        one = trial()
        two = trial("second", "2026-10-24T19:00:00Z")
        three = trial("changed", "2026-10-25T19:00:00Z")
        three["memory"] = "different memory"
        node = charts.trend([one, two, three], "base")
        self.assertEqual(len(node.findall("polyline")), 1)
        self.assertEqual(len(node.findall("circle")), 3)
        self.assertEqual(len(node.findall("polyline")[0].get("points").split()), 2)

    def test_backfill_latest_selection_and_determinism(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "charts"
            old = trial("old")
            new = trial("new", "2026-10-24T19:00:00Z")
            new["tasks"]["another"] = new["tasks"].pop("task")
            original = copy.deepcopy([new, old])
            top, _ = charts.write([new, old], path)
            files = {str(p.relative_to(path)): p.read_bytes() for p in path.rglob("*") if p.is_file()}
            charts.write([old, new], path)
            self.assertEqual(files, {str(p.relative_to(path)): p.read_bytes() for p in path.rglob("*") if p.is_file()})
            self.assertEqual([new, old], original)
            self.assertIn("another", top)
            self.assertNotIn("leaderboard-task-", top)
            self.assertEqual(len(list((path / "runs").glob("*/summary.svg"))), 2)
            self.assertIn("Older runs", (path / "README.md").read_text())
            self.assertFalse((path / "../old").exists())


if __name__ == "__main__":
    unittest.main()
