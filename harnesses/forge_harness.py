"""forge-harness (chrono-meta/forge-harness): the portable governor rules and the fh-commons skills."""
from harnesses import _surface

APPEND = ["AGENTS.md", ".agents/rules/fh-governor.md"]
COPY = {"plugins/fh-commons/skills": ".agents/skills"}
refuse = _surface.refuse


def run(spec, workdir):
    return _surface.run(spec, workdir, "forge-harness")
