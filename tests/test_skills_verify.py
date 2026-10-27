import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bench import skills, verify


def complete_record(day='2026-10-27'):
    identity = skills.digest(Path(skills.__file__).read_bytes() + json.dumps(skills.PIN, sort_keys=True).encode())
    result = dict(status='complete', score=1, usage=dict(input_tokens=100, output_tokens=10), wall_seconds=2)
    return dict(identity=identity, run_id=f'skills-{day}-{identity[:12]}', pin=skills.PIN, settings=skills.SPEC,
                rows=[dict(name=name, task=task[0], baseline=copy.deepcopy(result), skill=copy.deepcopy(result))
                      for name, task in skills.TASKS.items()])


class SkillsVerification(unittest.TestCase):
    def check_record(self, row, receipt=None, markdown=None, head_source=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            local = root/row['run_id']
            local.mkdir()
            (local/'result.json').write_text(json.dumps(row if receipt is None else receipt))
            def show(ref, path):
                if ref == 'base': return ''
                if path == 'bench/skills.py': return Path(skills.__file__).read_text() if head_source is None else head_source
                if path.endswith('.json'): return json.dumps(row)
                return skills.table(row['rows']) if markdown is None else markdown
            with patch.object(verify, 'changed_files', return_value={'results/skills/2026-10-27.json'}), \
                 patch.object(verify, 'show', side_effect=show):
                return verify.check_skills('base', 'head', root)

    def test_nightly_verifier_accepts_only_date_named_results(self):
        self.assertTrue(verify.allowed('results/skills/2026-10-27.json'))
        self.assertFalse(verify.allowed('results/skills/execute.py'))

    def test_matching_complete_receipt_and_table_pass(self):
        self.assertEqual(self.check_record(complete_record()), [])

    def test_executing_source_differs_from_result_head(self):
        # A complete receipt is still invalid when it came from another source checkout.
        for head_source in ('', Path(skills.__file__).read_text() + '\n# different runner\n'):
            with self.subTest(missing_source=not head_source):
                self.assertEqual(self.check_record(complete_record(), head_source=head_source), [
                    'results/skills/2026-10-27.json: skill receipt source identity differs from result head'
                ])

    def test_changed_receipt_and_header_only_table_fail(self):
        row = complete_record()
        self.assertTrue(self.check_record(row, receipt={}))
        self.assertTrue(self.check_record(row, markdown=skills.table([])))

    def test_incomplete_or_unpinned_records_fail_closed(self):
        def missing_metric(r): del r['rows'][0]['skill']['wall_seconds']
        def missing_tokens(r): del r['rows'][0]['baseline']['usage']['input_tokens']
        def missing_arm(r): del r['rows'][0]['skill']
        mutations = [
            lambda r: r.update(rows=[]),
            lambda r: r['rows'].pop(),
            lambda r: r['rows'].__setitem__(0, r['rows'][1]),
            lambda r: r['rows'][0].update(name='unknown'),
            lambda r: r['rows'][0].update(task='wrong'),
            lambda r: r['rows'][0]['skill'].update(status='partial'),
            missing_arm, missing_metric, missing_tokens,
            lambda r: r['rows'][0]['skill'].update(score=1.1),
            lambda r: r['rows'][0]['skill'].update(wall_seconds=float('nan')),
            lambda r: r['rows'][0]['baseline']['usage'].update(input_tokens=-1),
            lambda r: r['rows'][0]['baseline']['usage'].update(output_tokens=True),
            lambda r: r.update(settings={}), lambda r: r.update(pin={}),
            lambda r: r.update(identity='a'*64), lambda r: r.update(rows=None),
            lambda r: r.update(run_id='skills-2026-10-26-aaaaaaaaaaaa'),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(case=index):
                row = complete_record()
                mutate(row)
                self.assertTrue(self.check_record(row))
