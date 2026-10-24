"""Device checks execute harmless stand-ins, never installed device tools."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from bench import run

TOOLS = ('devicectl', 'xcrun', 'xcodebuild', 'simctl', 'instruments',
         'ideviceinstaller', 'xcodebuildmcp')


@unittest.skipUnless(sys.platform == 'darwin', 'requires Seatbelt')
class DeviceSandbox(unittest.TestCase):
    def test_trial_cannot_start_device_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            marker = work / 'started'
            for name in TOOLS:
                tool = work / name
                tool.write_text('#!/bin/sh\nprintf started > "' + str(marker) + '"\n')
                tool.chmod(0o755)
                # Python catches exec denial; the stand-in must never run.
                code = ('import subprocess; '
                        f'subprocess.run([{str(tool)!r}, "--help"], check=True)')
                done = run.sandboxed([sys.executable, '-c', code], work)
                with self.subTest(tool=name):
                    self.assertNotEqual(done.returncode, 0)
                    self.assertFalse(marker.exists())
                marker.unlink(missing_ok=True)

    def test_candidate_policy_denies_tools_and_interpreted_mcp(self):
        from bench import safety
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            marker = work / 'started'
            for name in TOOLS:
                tool = work / name
                tool.write_text('#!/bin/sh\nprintf started > "' + str(marker) + '"\n')
                tool.chmod(0o755)
                done = subprocess.run(safety.device_command([str(tool), '--help']), capture_output=True)
                with self.subTest(tool=name):
                    self.assertNotEqual(done.returncode, 0)
                    self.assertFalse(marker.exists())
            script = work / 'xcodebuildmcp.js'
            script.write_text(f'open({str(marker)!r}, "w").write("started")')
            done = subprocess.run(safety.device_command([sys.executable, str(script)]), capture_output=True)
            self.assertNotEqual(done.returncode, 0)
            self.assertFalse(marker.exists())
            allowed = subprocess.run(safety.device_command([sys.executable, '-c', 'print("ok")']),
                                     capture_output=True, text=True)
            self.assertEqual((allowed.returncode, allowed.stdout), (0, 'ok\n'), allowed.stderr)

    def test_usb_policy_and_bonjour_denial_without_contacting_devices(self):
        from bench import safety
        # sandbox_check queries policy without opening IOKit or any USB device.
        code = ('import ctypes,os; '
                's=ctypes.CDLL("/usr/lib/system/libsystem_sandbox.dylib"); '
                'assert s.sandbox_check(os.getpid(), b"iokit-open", 0) != 0')
        done = subprocess.run(safety.device_command([sys.executable, '-c', code]), capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        # A policy query avoids even sending an mDNS packet on a broken policy.
        self.assertIn('(deny network* (local udp "*:*"))', safety.device_policy())
        self.assertIn('/var/run/usbmuxd', safety.device_policy())

    def test_real_codex_inventory_disables_a_configured_server_without_starting_it(self):
        from bench import safety
        if not safety.CODEX.is_file():
            self.skipTest('Codex is not installed')
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / 'server-started'
            command = ['--probe', '-c', 'mcp_servers.safety_canary.command="/bin/sh"', '-c',
                       'mcp_servers.safety_canary.args=' + __import__('json').dumps(['-c', f'touch {marker}'])]
            done = subprocess.run([str(safety.WRAPPER), *command], capture_output=True, text=True, timeout=40)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn('enabled MCP servers: []', done.stderr)
            self.assertFalse(marker.exists())

    def test_native_codex_command_sandbox_allows_shell_and_denies_devices(self):
        from bench import safety
        if not safety.CODEX.is_file():
            self.skipTest('Codex is not installed')
        _, flags = safety.command_policy(['exec', '-s', 'read-only'])
        def probe(code):
            return subprocess.run([str(safety.CODEX), 'sandbox', *flags, '--', sys.executable, '-c', code],
                                  capture_output=True, text=True, timeout=20)
        allowed = probe('print("ok")')
        self.assertEqual((allowed.returncode, allowed.stdout), (0, 'ok\n'), allowed.stderr)
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            for name in (*TOOLS, 'xcodebuildmcp.js'):
                tool = work / name
                # Print-only: a read-only filesystem must not itself make this stand-in fail.
                tool.write_text('#!/bin/sh\nprintf DEVICE_TOOL_STARTED\n')
                tool.chmod(0o755)
                code = f'import subprocess; subprocess.run([{str(tool)!r}, "devicectl"], check=True)'
                done = probe(code)
                with self.subTest(tool=name):
                    self.assertNotEqual(done.returncode, 0)
                    self.assertNotIn('DEVICE_TOOL_STARTED', done.stdout)
                    self.assertNotEqual(probe(f'open({str(tool)!r}).read()').returncode, 0)
        query = ('import ctypes,os; '
                 's=ctypes.CDLL("/usr/lib/system/libsystem_sandbox.dylib"); '
                 'assert s.sandbox_check(os.getpid(), b"iokit-open", 0) != 0')
        done = probe(query)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_native_profile_preserves_pilot_filesystem_limits(self):
        import json
        from bench import safety
        if not safety.CODEX.is_file():
            self.skipTest('Codex is not installed')
        base = {':root': 'deny', ':minimal': 'read', ':workspace_roots': 'write',
                str(safety.CODEX): 'read', str(Path(sys.executable).resolve().parent.parent): 'read'}
        table = '{' + ','.join(json.dumps(k) + '=' + json.dumps(v) for k, v in base.items()) + '}'
        original = safety.config_flags(['default_permissions="pilot"',
                                       'permissions.pilot.filesystem=' + table,
                                       'permissions.pilot.network.enabled=false'])
        _, flags = safety.command_policy(['exec', *original])
        with tempfile.TemporaryDirectory() as tmp:
            def probe(code):
                return subprocess.run([str(safety.CODEX), 'sandbox', '-P', 'bench-device-safe', '-C', tmp,
                                       *original, *flags, '--', sys.executable, '-c', code],
                                      capture_output=True, text=True, timeout=20)
            allowed = probe('open("workspace-write", "w").write("ok"); print("ok")')
            self.assertEqual((allowed.returncode, allowed.stdout), (0, 'ok\n'), allowed.stderr)
            denied = probe('from pathlib import Path; list(Path.home().iterdir())')
            self.assertNotEqual(denied.returncode, 0)
            self.assertIn('PermissionError', denied.stderr)


class LaunchPaths(unittest.TestCase):
    def test_pilot_calls_are_redirected_and_keep_original_permissions(self):
        from bench import safety
        from types import SimpleNamespace
        original = ['-c', 'permissions.pilot.network.enabled=false']
        pilot = SimpleNamespace(CODEX='/pinned/codex', config_args=lambda: original)
        safety.configure_round2(pilot)
        self.assertEqual(pilot.CODEX, safety.WRAPPER)
        self.assertEqual(pilot.config_args()[:2], original)
        self.assertIn('features.plugins=false', pilot.config_args())
        self.assertIn('features.hooks=false', pilot.config_args())

    def test_inventory_failure_prevents_execution(self):
        from bench import safety
        from unittest.mock import patch
        with patch.object(safety, 'isolated_flags', side_effect=RuntimeError('refused')), \
                patch.object(safety.subprocess, 'call') as call:
            with self.assertRaises(RuntimeError):
                safety.launch(['exec', '-s', 'read-only', 'hello'])
            call.assert_not_called()

    def test_pinned_pair_entry_point_uses_wrapper(self):
        from bench import safety
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / 'round2.py').write_text('CODEX="unsafe"\ndef config_args(): return []\n')
            script = work / 'arm.py'
            script.write_text('import round2\nprint(round2.CODEX)\nprint(round2.config_args())\n')
            done = subprocess.run([sys.executable, str(safety.ROOT / 'bench/paired.py'), str(script)],
                                  env={**os.environ, 'PILOT': tmp}, capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(done.stdout.splitlines()[0], str(safety.WRAPPER))
            self.assertIn('features.plugins=false', done.stdout)
