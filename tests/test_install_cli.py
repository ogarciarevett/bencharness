import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from bench import cli, run


class InstalledCommand(unittest.TestCase):
    def test_abbreviated_trial_flags_never_enter_the_installed_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            for arguments in (["--que"], ["--tri", "trial.json"]):
                with self.subTest(arguments=arguments), mock.patch("sys.argv", ["bench", "--workspace", tmp, *arguments]), \
                     mock.patch.object(run, "queue") as queue, contextlib.redirect_stderr(io.StringIO()), \
                     self.assertRaises(SystemExit) as error:
                    cli.main()
                self.assertEqual(error.exception.code, 2)
                queue.assert_not_called()

    def test_trial_without_source_checkout_refuses_before_execution(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch("sys.argv", ["bench", "--workspace", tmp, "--queue"]), \
             mock.patch.object(run, "queue") as queue, self.assertRaisesRegex(SystemExit, "source checkout"):
            cli.main()
        queue.assert_not_called()

    def test_source_dispatch_preserves_working_directory_arguments_and_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / "bench").mkdir()
            (root / "bench/run.py").write_text("import json,os,pathlib,sys\npathlib.Path('probe.json').write_text(json.dumps([os.getcwd(),sys.argv[1:]]))\nsys.exit(7)\n")
            with mock.patch("sys.argv", ["bench", "--workspace", tmp, "--trial", "owned.json"]):
                self.assertEqual(cli.main(), 7)
            self.assertEqual(json.loads((root / "probe.json").read_text()), [str(root.resolve()), ["--trial", "owned.json"]])
