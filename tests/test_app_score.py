from pathlib import Path
import tempfile
import unittest
from bench import app_score
from suites.desktop import task


class AppSubmission(unittest.TestCase):
    def test_submission_does_not_control_grader_build_metadata_or_wire_driver(self):
        with tempfile.TemporaryDirectory() as tmp:
            app,out=Path(tmp)/'app',Path(tmp)/'out'
            task.stage(app)
            (app/'src-tauri/src/main.rs').write_text('malicious wire replacement')
            app_score.copy_submission(app,out)
            self.assertEqual((out/'src-tauri/src/main.rs').read_text(),task.FILES['src-tauri/src/main.rs'])
            (app/'package.json').write_text('{}')
            with self.assertRaises(ValueError):app_score.copy_submission(app,Path(tmp)/'second')

    def test_symlink_cannot_make_grader_read_outside_submission(self):
        with tempfile.TemporaryDirectory() as tmp:
            app=Path(tmp)/'app';task.stage(app)
            (app/'src/escape.ts').symlink_to('/private/etc/passwd')
            with self.assertRaises(ValueError):app_score.copy_submission(app,Path(tmp)/'out')

    def test_valid_heading_attributes_are_accepted_semantically(self):
        jobs=[dict(id='high',title='<danger>&',priority=3,status='queued',seq=2),dict(id='low',title='Work',priority=1,status='queued',seq=1)]
        backend=dict(ok=True,rev=2,jobs=jobs)
        state=dict(rev=2,jobs=jobs,request=1,loading=False,error=None)
        html='<h1 class="title"><span>Work queue</span></h1><ul aria-busy="false"><li data-id="high" data-status="queued">&lt;danger&gt;&amp;</li><li data-id="low" data-status="queued">Work</li></ul>'
        values=dict(initial=dict(rev=0,jobs=[],request=0,loading=False,error=None),loading=dict(rev=0,jobs=[],request=1,loading=True,error=None),loaded=state,accepted={**state,'request':2},failed={**state,'request':2,'error':'offline<&>'},bridge=dict(rev=2,jobs=jobs),calls=[['dispatch',{'request':{'op':'list'}}]],entryCalls=[['dispatch',{'request':{'op':'list'}}]],bridgeError='FAULT',stale=True,staleFailure=True,regressed=True,untouched=True,html=html,entryHtml=html,loadingHtml='<ul aria-busy="true"></ul><p role="status">Loading</p>',errorHtml='<p role="alert">offline&lt;&amp;&gt;</p>')
        self.assertTrue(app_score.frontend_ok(values,backend))

    def test_invalid_submission_is_a_zero_score_not_an_infrastructure_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            app=Path(tmp)/'app';task.stage(app)
            (app/'package.json').write_text('{}')
            result=app_score.score(app,Path(tmp)/'missing-tooling',Path(tmp)/'grade')
            self.assertTrue(result['invalid_submission'])
            self.assertEqual(result['score'],0)
            self.assertEqual(len(result['checks']),22)
            (app/'package.json').unlink();(app/'package.json').mkdir()
            self.assertTrue(app_score.score(app,Path(tmp),Path(tmp)/'second')['invalid_submission'])
