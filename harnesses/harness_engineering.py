"""harness-engineering (lenalab-me/harness-engineering): the grow-guardrails and tidy-rules skills.

Its CLAUDE.md is maintainer notes for that repo, not agent rules, so only the skills are added.
"""
from harnesses import _surface

APPEND, COPY = [], {"skills": ".agents/skills"}
refuse = _surface.refuse


def run(spec, workdir):
    return _surface.run(spec, workdir, "harness-engineering")
