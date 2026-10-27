import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bench import skills


class Skills(unittest.TestCase):
    def test_each_requested_skill_has_task_and_pinned_source(self):
        self.assertEqual(set(skills.TASKS), set(skills.PIN['skills']))
        for name, entry in skills.PIN['skills'].items():
            self.assertIn(entry['path']+'/SKILL.md', entry['files'])
        self.assertEqual(skills.SPEC['effort'], 'medium')

    def test_table_uses_uncached_plus_cached_input_once(self):
        result=dict(score=1,usage=dict(input_tokens=100,cached_input_tokens=80,output_tokens=10),wall_seconds=2)
        text=skills.table([dict(name='tdd',task='code',baseline=result,skill={**result,'score':0})])
        self.assertIn('110 / 110',text)
        self.assertIn('-100',text)

    def test_no_output_or_symlink_cannot_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve()
            self.assertEqual(skills.score('research',root),0)
            (root/'RESEARCH.md').symlink_to('/etc/hosts')
            self.assertEqual(skills.score('research',root),0)

    def test_host_gate_defers_before_model(self):
        with patch.object(skills,'installed'), patch.object(skills.guard,'problems',return_value=['busy']), \
             patch.object(skills,'model_seam') as model, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(skills.run('1900-01-01'),0)
            model.assert_not_called()

    def test_model_error_is_not_a_completed_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve()
            @contextlib.contextmanager
            def model(*args):yield 'socket'
            with patch.object(skills,'ROOT',root), patch.object(skills,'RUNS',root/'runs'), \
                 patch.object(skills,'installed',return_value=root), patch.object(skills.guard,'problems',return_value=[]), \
                 patch.object(skills,'Path',side_effect=lambda value: root/'build.lock' if str(value)=='/Volumes/Studio/.cache/local-ci-build.lock' else Path(value)), \
                 patch.object(skills,'model_seam',model), patch.object(skills.fcntl,'flock'), \
                 patch.object(skills.seam,'request',return_value=dict(ok=False,kind='StopPilot',error='quota exceeded')), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(skills.run('1900-01-01'),1)
                self.assertFalse((root/'results').exists())

    def test_grader_startup_failure_is_not_a_zero_score(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve()
            (root/'cache.py').write_text('')
            with patch.object(skills,'sandboxed',return_value=SimpleNamespace(returncode=71,stderr='interpreter unavailable')):
                with self.assertRaises(RuntimeError):skills.score('diagnosing-bugs',root)
