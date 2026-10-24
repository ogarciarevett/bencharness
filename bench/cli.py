"""Installed entry point; state stays in the selected workspace."""
from importlib.metadata import version
from pathlib import Path
import sys


def main():
    if sys.argv[1:] == ["--version"]:
        print("bencharness " + version("bencharness"))
        return
    from bench.run import main as run
    # Installing or invoking the bare command never starts a subscription trial.
    run(["--workspace", str(Path.cwd()), *(sys.argv[1:] or ["--help"])])
