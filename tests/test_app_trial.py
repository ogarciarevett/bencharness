from pathlib import Path
import tempfile
import unittest
from threading import Condition
from bench import app_trial, guard
from suites.desktop import grade

class AppTrialState(unittest.TestCase):
    def test_atomic_output_replaces_symlink_without_modifying_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();target=root/'protected';target.write_text('receipt')
            output=root/'result.json';output.symlink_to(target)
            app_trial.write(output,{'score':1})
            self.assertEqual(target.read_text(),'receipt')
            self.assertFalse(output.is_symlink())
            self.assertIn('"score": 1',output.read_text())

    def test_symlink_directory_cannot_expand_filesystem_grants(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();(root/'actual').mkdir();(root/'alias').symlink_to(root/'actual',target_is_directory=True)
            with self.assertRaises(ValueError):app_trial.directory(root/'alias/app')

    def test_dependency_fingerprint_refuses_external_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as external:
            root=Path(tmp).resolve();(root/'.rust-deps').mkdir();(root/'.rust-deps/a.rlib').write_bytes(b'rlib')
            for name in ['package.json','package-lock.json','src-tauri/Cargo.lock']:
                p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('{}')
            (root/'src-tauri/target').mkdir()
            outside=Path(external)/'dependency';outside.write_text('external fixture')
            (root/'node_modules').mkdir();(root/'node_modules/outside').symlink_to(outside)
            with self.assertRaisesRegex(ValueError,'escapes'):app_trial.fingerprint(root)

    def test_host_pause_is_not_scored_as_submission_failure(self):
        def paused(*args):raise guard.HostBusy('pause')
        with self.assertRaises(guard.HostBusy):grade.score(paused,paused)

    def test_cached_receipt_cannot_cross_specs(self):
        with tempfile.TemporaryDirectory() as tmp:
            work=Path(tmp).resolve()/'native-codex-app';private=Path(tmp).resolve()/'.app-ledger/native-codex-app';private.mkdir(parents=True)
            (private/'state.json').write_text('{"identity":"different"}')
            with self.assertRaises(ValueError):app_trial.run({'tasks':['desktop-queue']},work,'native-codex-app')

    def test_late_transport_failure_and_unstarted_timeout_are_not_scores(self):
        from types import SimpleNamespace
        broker=SimpleNamespace(receipt={'model':'confirmed'},turn_started=True,errors=[],turn_status=None,client_eof=False,expired=False,expiry=None,io=Condition(),output_closed=False)
        self.assertTrue(app_trial.model_finished('budget',False,broker))
        for field,value in [('errors',['transport']),('turn_status','failed'),('turn_started',False),('client_eof',True),('output_closed',True)]:
            prior=getattr(broker,field);setattr(broker,field,value)
            self.assertFalse(app_trial.model_finished('budget',False,broker));setattr(broker,field,prior)
        self.assertFalse(app_trial.model_finished('error',False,broker))
        broker.expiry=vars(broker).copy()
        broker.expired=True;broker.turn_status='interrupted';broker.client_eof=True
        self.assertTrue(app_trial.model_finished('budget',False,broker))
        self.assertEqual(app_trial.turn_reason(broker,1,1200),'budget')
