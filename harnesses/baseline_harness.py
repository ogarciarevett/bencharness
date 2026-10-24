"""Native baseline loop (our baseline harness): one Codex proposal plus one native search round."""
from harnesses import _pilot

refuse = _pilot.refuse


def run(spec, workdir):
    return _pilot.run_side(spec, workdir, "A")
