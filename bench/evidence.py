"""Render public PR evidence only after the deterministic nightly check passes."""
import contextlib
import io
import sys

from bench import verify


def main(base, head, runs):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        status = verify.main(base, head, runs)
    if status:
        print(output.getvalue(), end="")
        return status
    counts = []
    for path in ("queue/candidates.jsonl", "results/trials.jsonl"):
        before = verify.show(base, path).splitlines()
        after = verify.show(head, path).splitlines()
        counts.append(len(after) - len(before))
    print(f"## Evidence (head {head[:7]})\n")
    print(f"Run summary: {counts[0]} new candidate(s); {counts[1]} new record(s).")
    print("Verification: PASS — added records match local run files; RESULTS.md, README.md and charts regenerate.")
    print(f"Check: `python3 -m bench.verify {base} {head} <local-runs-dir>` (exit 0).")
    print("Counts are additions relative to the checked base; no new records means no new benchmark result.")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:4]))
