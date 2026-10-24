"""TAUSIK (Kibertum/tausik-core): agent onboarding rules and the host-neutral skills.

Its MCP server and CLI are not installed: only the text surface is under test.
"""
from harnesses import _surface

APPEND = ["AGENTS.md"]
COPY = {"harness/skills": ".agents/skills"}
refuse = _surface.refuse


def run(spec, workdir):
    return _surface.run(spec, workdir, "tausik-core")
