import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from bench import repo_env, safety


class RepoEnvironment(unittest.TestCase):
    def test_credentials_and_network_environment_are_not_inherited(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'OPENAI_API_KEY':'canary','HTTPS_PROXY':'canary'}):
            env = repo_env.environment(tmp)
            self.assertNotIn('OPENAI_API_KEY', env)
            self.assertNotIn('HTTPS_PROXY', env)
            self.assertEqual(env['HOME'], str(Path(tmp).resolve() / '_home'))

    def test_app_server_uses_safe_settings_without_exec_only_flag(self):
        with patch.object(safety, 'isolated_flags', return_value=safety.config_flags(safety.SETTINGS)), \
                patch.object(safety.subprocess, 'call', return_value=0) as call:
            self.assertEqual(safety.launch(['app-server']), 0)
            args = call.call_args[0][0]
            self.assertNotIn('--ignore-rules', args)
            self.assertIn('forced_login_method="chatgpt"', args)
            self.assertIn('features.plugins=false', args)
            with self.assertRaises(RuntimeError):
                safety.launch(['app-server', '--listen', 'ws://127.0.0.1:9999'])

    @unittest.skipUnless(sys.platform == 'darwin' and safety.CODEX.is_file(), 'requires native Codex sandbox')
    def test_leaf_cannot_read_sealed_source_or_plant_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            reads = [Path(sys.executable).resolve().parent.parent]
            allowed = repo_env.execute([sys.executable, '-c', 'import tempfile,os; p=tempfile.mkdtemp(dir=".");os.rmdir(p);print("ok")'], work, reads, writable=True)
            self.assertEqual((allowed.returncode, allowed.stdout), (0, 'ok\n'), allowed.stderr)
            for code in [f'open({str(safety.ROOT / "suites/desktop/grade.py")!r}).read()',
                         'import os; os.mkdir(".codex")',
                         'import socket; socket.socket().connect(("127.0.0.1", 9))']:
                denied = repo_env.execute([sys.executable, '-c', code], work, reads)
                self.assertNotEqual(denied.returncode, 0)

    @unittest.skipUnless(sys.platform == 'darwin', 'requires Seatbelt')
    def test_untrusted_program_output_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = repo_env.execute([sys.executable,'-c','import os;\nwhile True:os.write(1,b"x"*65536)'],
                                 Path(tmp),[Path(sys.executable).resolve().parent.parent],timeout=3)
            self.assertEqual(r.returncode,124)
            self.assertIn('output limit',r.stderr)
            self.assertLess(len(r.stdout),1_100_000)

    @unittest.skipUnless(sys.platform == 'darwin', 'requires Seatbelt')
    def test_submitted_runtime_cannot_replace_grading_controls(self):
        with tempfile.TemporaryDirectory() as tmp:
            work=Path(tmp);control=work/'frontend.cjs';control.write_text('trusted')
            result=repo_env.execute([sys.executable,'-c','open("frontend.cjs","w").write("forged")'],work,
                                    [Path(sys.executable).resolve().parent.parent])
            self.assertNotEqual(result.returncode,0)
            self.assertEqual(control.read_text(),'trusted')

    @unittest.skipUnless(sys.platform == 'darwin', 'requires Seatbelt')
    def test_submitted_runtime_cannot_spawn_unaccounted_processes(self):
        with tempfile.TemporaryDirectory() as tmp:
            code='import subprocess,sys;subprocess.run([sys.executable,"-c","print(\\\"CHILD_STARTED\\\")"],check=True)'
            result=repo_env.execute([sys.executable,'-c',code],Path(tmp),[Path(sys.executable).resolve().parent.parent])
            self.assertNotEqual(result.returncode,0)
            self.assertNotIn('CHILD_STARTED',result.stdout)

    def test_memory_bound_cannot_be_combined_with_child_process_creation(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError):
            repo_env.execute(['unused'],Path(tmp),memory_mb=10,allow_children=True)

    @unittest.skipUnless(sys.platform == 'darwin', 'requires Seatbelt')
    def test_trusted_build_group_is_cleaned_after_root_success(self):
        import signal,time
        with tempfile.TemporaryDirectory() as tmp:
            child='import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);print("ready",flush=True);time.sleep(60)'
            code='import subprocess,sys;p=subprocess.Popen([sys.executable,"-c",'+repr(child)+'],stdout=subprocess.PIPE,text=True);p.stdout.readline();print(p.pid,flush=True)'
            started=time.monotonic()
            result=repo_env.execute([sys.executable,'-c',code],Path(tmp),[Path(sys.executable).resolve().parent.parent],allow_children=True)
            self.assertLess(time.monotonic()-started,3)
            pid=int(result.stdout)
            try:
                state=subprocess.run(['/bin/ps','-p',str(pid),'-o','stat='],capture_output=True,text=True).stdout.strip()
                self.assertTrue(not state or state.startswith('Z'),state)
            finally:
                try: os.kill(pid,signal.SIGKILL)
                except ProcessLookupError: pass

    @unittest.skipUnless(sys.platform == 'darwin', 'requires Seatbelt')
    def test_memory_monitor_survives_closed_output_streams(self):
        import time
        with tempfile.TemporaryDirectory() as tmp:
            code='import os,time;os.close(1);os.close(2);time.sleep(.4);data=bytearray(80*1024*1024);time.sleep(3)'
            started=time.monotonic()
            result=repo_env.execute([sys.executable,'-c',code],Path(tmp),
                                    [Path(sys.executable).resolve().parent.parent],timeout=5,memory_mb=48)
            self.assertEqual(result.returncode,124)
            self.assertIn('memory limit',result.stderr)
            self.assertLess(time.monotonic()-started,2.5)

    @unittest.skipUnless(sys.platform == 'darwin', 'requires Seatbelt')
    def test_host_monitor_interrupts_active_command_and_propagates(self):
        from bench.guard import HostBusy
        def paused():raise HostBusy('pause')
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(HostBusy):
            repo_env.execute([sys.executable,'-c','import time;time.sleep(30)'],Path(tmp),
                             [Path(sys.executable).resolve().parent.parent],monitor=paused)
