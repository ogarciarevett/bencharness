"""Summarize the latest check runs for the immutable head reviewed by the morning job."""
import json
import subprocess
import sys

# Keep these in step with ci.yml and the required evidence workflow.
REQUIRED = {"test", "clean-install", "homebrew-install", "evidence / verify"}


def status(runs, head):
    latest = {}
    for run in runs:
        if run["head_sha"] != head:
            continue
        name = run["name"]
        if name not in latest or run["id"] > latest[name]["id"]:
            latest[name] = run
    completed = [run["conclusion"] for run in latest.values() if run["status"] == "completed"]
    if any(result not in {"success", "skipped", "neutral"} for result in completed):
        return "failure"
    if REQUIRED - latest.keys() or any(run["status"] != "completed" for run in latest.values()):
        return "pending"
    return "success"


def main(slug, head):
    try:
        output = subprocess.check_output([
            "gh", "api", "--paginate", "--slurp",
            f"repos/{slug}/commits/{head}/check-runs?per_page=100",
        ], text=True)
        runs = [run for page in json.loads(output) for run in page["check_runs"]]
        print(status(runs, head))
    except (OSError, subprocess.CalledProcessError, ValueError, KeyError, TypeError):
        print("pending")


if __name__ == "__main__":
    main(*sys.argv[1:3])
