import json
from pathlib import Path
import tempfile
import unittest
from suites.desktop import grade, task


class DesktopTask(unittest.TestCase):
    def test_staging_excludes_hidden_tests_and_has_three_app_layers(self):
        with tempfile.TemporaryDirectory() as tmp:
            task.stage(tmp)
            root = Path(tmp)
            self.assertTrue((root / 'src-tauri/src/lib.rs').is_file())
            self.assertTrue((root / 'src/App.tsx').is_file())
            self.assertTrue((root / 'src/bridge.ts').is_file())
            self.assertEqual(json.loads((root / 'src-tauri/tauri.conf.json').read_text())['identifier'], 'dev.bench.workqueue')
            self.assertFalse((root / 'grade.py').exists())
            self.assertFalse((root / 'tests').exists())

    def test_empty_candidate_does_not_pass_hidden_checks(self):
        result = grade.score(lambda data: {'state': {}, 'replies': []}, lambda data: False)
        self.assertEqual(result['score'], 0)
        self.assertEqual(len(result['checks']), 21)

    def test_one_case_failure_does_not_abort_scoring(self):
        def broken(data):
            raise ValueError('malformed candidate response')
        self.assertEqual(grade.score(broken, broken)['score'], 0)
