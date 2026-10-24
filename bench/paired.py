"""Route the pinned external runner's model calls through the shared safety entry point."""
import os
from pathlib import Path
import runpy
import sys

sys.path[:0] = [str(Path(__file__).resolve().parent.parent), os.environ['PILOT']]
from bench import safety
import round2
safety.configure_round2(round2)
script = sys.argv.pop(1)
runpy.run_path(script, run_name='__main__')
