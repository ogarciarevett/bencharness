"""Installed entry point; state stays in the selected workspace."""
from importlib.metadata import version
import argparse
from pathlib import Path
import subprocess
import sys


def main():
    if sys.argv[1:] == ["--version"]:
        print("bencharness " + version("bencharness"))
        return
    from bench.run import main as run
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    selected, arguments = parser.parse_known_args()
    workspace = selected.workspace.resolve()
    trial_flags = {"--trial", "--queue", "--max", "--import", "--child"}
    if "--help" not in arguments and any(arg.split("=", 1)[0] in trial_flags for arg in arguments):
        source = workspace / "bench/run.py"
        if not source.is_file() or not (workspace / ".git").exists():
            raise SystemExit("Trial execution needs --workspace pointing to a source checkout with pinned assets; no trial started.")
        # A fresh process preserves the checkout's code roots, suite pins and sandbox.
        return subprocess.call([sys.executable, str(source), *arguments], cwd=workspace)
    # Installing or invoking the bare command never starts a subscription trial.
    run(["--workspace", str(workspace), *(arguments or ["--help"])])
