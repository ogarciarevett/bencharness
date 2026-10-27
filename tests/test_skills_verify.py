import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bench import skills, verify


class SkillsVerification(unittest.TestCase):
    def test_nightly_verifier_accepts_only_date_named_results(self):
        self.assertTrue(verify.allowed('results/skills/2026-10-27.json'))
        self.assertFalse(verify.allowed('results/skills/execute.py'))

    def test_changed_skill_receipt_is_rejected(self):
        row=dict(run_id='skills-2026-10-27-aaaaaaaaaaaa',rows=[])
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve()
            (root/'skills-2026-10-27-aaaaaaaaaaaa').mkdir()
            (root/'skills-2026-10-27-aaaaaaaaaaaa/result.json').write_text('{}')
            with patch.object(verify,'changed_files',return_value={'results/skills/2026-10-27.json'}), \
                 patch.object(verify,'show',side_effect=lambda ref,path: '' if ref=='base' else json.dumps(row) if path.endswith('.json') else skills.table([])):
                self.assertTrue(verify.check_skills('base','head',root))


    def test_matching_receipt_and_table_pass(self):
        row=dict(run_id='skills-2026-10-27-aaaaaaaaaaaa',rows=[])
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve()
            (root/row['run_id']).mkdir()
            (root/row['run_id']/'result.json').write_text(json.dumps(row))
            with patch.object(verify,'changed_files',return_value={'results/skills/2026-10-27.json'}), \
                 patch.object(verify,'show',side_effect=lambda ref,path: '' if ref=='base' else json.dumps(row) if path.endswith('.json') else skills.table([])):
                self.assertEqual(verify.check_skills('base','head',root),[])
                row['run_id']='..'
                self.assertTrue(verify.check_skills('base','head',root))
