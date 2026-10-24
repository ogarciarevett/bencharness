"""Baseline for surface trials: native baseline harness side A with the shared base harness only."""
from harnesses import _surface

APPEND, COPY = [], {}
refuse = _surface.refuse


def run(spec, workdir):
    return _surface.run(spec, workdir, "baseline-native")
