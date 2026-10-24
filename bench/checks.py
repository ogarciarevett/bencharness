"""Fixed checks that replace human approval. A trial that passes runs on the next schedule and is
reported after. A trial that needs money beyond the budget or a new account goes to the owner."""

# OSI licences by SPDX id, as GitHub reports them.
OPEN_LICENCES = {"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "MPL-2.0", "0BSD",
                 "Unlicense", "GPL-2.0", "GPL-3.0", "LGPL-2.1", "LGPL-3.0", "AGPL-3.0"}
# Owner policy: every trial uses the Codex subscription only.
FREE_MODELS = {"gpt-6.1-sol"}
MAX_ACTIVE_SECONDS = 1200  # per task and harness
MAX_TASKS = 4


def licence(candidate):
    """Problem text for a research candidate, or None when it may be admitted."""
    spdx = candidate.get("licence")
    return None if spdx in OPEN_LICENCES else f"licence {spdx or 'unknown'} is not an open licence"


def trial(spec):
    """(blocking problems, owner problems). Owner problems are money or a new account."""
    blocking, owner = [], []
    if spec.get("budget_active_seconds", 0) > MAX_ACTIVE_SECONDS:
        blocking.append(f"budget {spec['budget_active_seconds']} s is over {MAX_ACTIVE_SECONDS} s per task side")
    if len(spec.get("tasks", ())) > MAX_TASKS:
        blocking.append(f"{len(spec['tasks'])} tasks is over {MAX_TASKS}")
    model = str(spec.get("model") or "")
    if model.startswith("claude"):
        blocking.append("owner policy: Codex only; Claude trials are disabled")
    elif model not in FREE_MODELS:
        owner.append(f"model {model or 'unset'} is not on an existing subscription")
    if model.startswith("gpt") and spec.get("service_tier") != "standard":
        owner.append("Codex runs must use service_tier=standard")
    if spec.get("paid_usd", 0) > 0:
        owner.append(f"needs USD {spec['paid_usd']} beyond the set budget")
    if spec.get("needs_account"):
        owner.append(f"needs a new account: {spec['needs_account']}")
    return blocking, owner
