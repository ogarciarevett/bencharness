"""harness2 (google-research/harness2), parallel mode: Propose, Probe, Compose, then selection."""
from harnesses import _pilot

refuse = _pilot.refuse


def run(spec, workdir):
    return _pilot.run_side(spec, workdir, "B")
