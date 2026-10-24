"""Shared seam for the paired pilot runner: our internal harness2 pilot (round2.py) through the Hyperag arm
(the retrieval evaluation runner). Both are used unchanged, from their own checkouts.

BENCH_PILOT    path to the harness2 pilot folder (round2.py, .venv, baseline, harness2)
BENCH_HYPERAG  path to a hyperag checkout that has eval/harness2

shortcut: round2 always runs side A (native baseline harness) and side B (harness2) together, so the
first adapter of a trial runs both and the second reuses the saved results. Replace this when a
harness needs its own single-side runner.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

HOST_STOP = "Host 1-minute load exceeded 20"  # round2 error text for every host-rule stop

# What round2.py and the Hyperag arm actually run; they take no model, budget or memory options.
FIXED = {"model": "gpt-6.1-sol", "effort": "medium", "service_tier": "standard", "k": 1,
         "budget_active_seconds": 1200, "memory": "Hyperag multilingual-e5-small, no reranker, no graph, limit 10",
         "harnesses": ["baseline", "harness2"]}
CHECKOUTS = {"baseline": "baseline", "harness2": "harness2"}


def mismatch(spec, pilot):
    found = [f"{key} {spec.get(key)!r} is not {value!r}" for key, value in FIXED.items() if spec.get(key) != value]
    for name, folder in CHECKOUTS.items():
        head = subprocess.run(["git", "-C", str(pilot / folder), "rev-parse", "HEAD"], capture_output=True, text=True).stdout
        if not head.startswith(spec.get("pins", {}).get(name, "?")):
            found.append(f"{name} checkout {head[:8] or 'missing'} is not the pinned {spec.get('pins', {}).get(name)}")
    return found


def refuse(spec):
    """Why this runner cannot honour the spec; the trial is then skipped, never run under other settings."""
    return mismatch(spec, Path(os.environ.get("BENCH_PILOT", "/nonexistent")))


def run_side(spec, workdir, side):
    pilot, hyperag = Path(os.environ["BENCH_PILOT"]), Path(os.environ["BENCH_HYPERAG"])
    arm = workdir / "hyperag-arm"
    if not arm.exists():
        # A fresh copy per trial, so saved results from earlier runs are never reused.
        shutil.copytree(hyperag / "eval/harness2", arm, ignore=shutil.ignore_patterns("runs", "__pycache__", "results.json"))
    out = {}
    for task in spec["tasks"]:
        result = arm / "runs/round2" / task / side / "result.json"
        if not result.exists() or json.loads(result.read_text()).get("error") == HOST_STOP:
            # --resume reuses completed sides and continues a host stop with only the unspent cap.
            subprocess.run([str(pilot / ".venv/bin/python"), str(Path(__file__).resolve().parent.parent / "bench/paired.py"), str(arm / "run.py"), "--pack", task, "--resume"],
                           env={**os.environ, "PILOT": str(pilot)}, cwd=arm)
        out[task] = json.loads(result.read_text()) if result.exists() else {"status": "failed", "error": "no result.json"}
        if out[task].get("error") == HOST_STOP:
            break  # the host is busy: do not start the next task
    return out
