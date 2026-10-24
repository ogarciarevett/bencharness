"""Check a nightly PR's records against the run files, and that RESULTS.md regenerates. Read only.

python3 -m bench.verify BASE_REF HEAD_REF RUNS_DIR [--any-files]
Exit 0 and "PASS" when every record that HEAD_REF adds equals the result.json files the runner wrote
under RUNS_DIR, and RESULTS.md and the README table at HEAD_REF are exactly what bench.report makes
from HEAD_REF's store. Otherwise exit 1 and one "FAIL: ..." line per problem.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def show(ref, path):
    done = subprocess.run(["git", "-C", str(ROOT), "show", f"{ref}:{path}"], capture_output=True, text=True)
    return done.stdout if done.returncode == 0 else ""


# A nightly PR may touch only these files; anything else needs a normal reviewed PR.
ALLOWED = {"results/trials.jsonl", "queue/candidates.jsonl", "RESULTS.md", "README.md"}


def changed_files(base, head):
    done = subprocess.run(["git", "-C", str(ROOT), "diff", "--name-only", f"{base}...{head}"], capture_output=True, text=True, check=True)
    return set(done.stdout.split())


def new_records(base, head):
    old = [json.loads(line) for line in show(base, "results/trials.jsonl").splitlines()]
    rows = [json.loads(line) for line in show(head, "results/trials.jsonl").splitlines()]
    problems = [] if rows[:len(old)] == old else ["the store changes or drops existing records"]
    added = rows[len(old):]
    ids = [r["trial"] for r in rows]
    problems += [f"trial {i} is recorded twice" for i in sorted({i for i in ids if ids.count(i) > 1})]
    return added, problems


def check_records(records, runs):
    from bench.run import verdict
    problems = []
    for row in records:
        for task, entry in row["tasks"].items():
            if entry["verdict"] != verdict(entry["results"]):
                problems.append(f"{row['trial']} {task}: verdict {entry['verdict']!r} does not follow from the results")
            for harness, result in entry["results"].items():
                path = Path(runs) / row["trial"] / harness / task / "result.json"
                if not path.exists():
                    problems.append(f"{row['trial']} {harness} {task}: no run file {path}")
                elif json.loads(path.read_text()) != result:
                    problems.append(f"{row['trial']} {harness} {task}: record differs from {path}")
    return problems


def check_labels(records, head):
    """The BENCH line and memory of each new record must follow from its trial file at head."""
    from bench.run import bench_line
    problems = []
    for row in records:
        text = show(head, f"trials/{row['trial']}.json")
        if not text:
            problems.append(f"{row['trial']}: no trial file at {head}")
            continue
        spec = json.loads(text)
        if row["bench"] != bench_line(spec) or row["memory"] != spec["memory"]:
            problems.append(f"{row['trial']}: BENCH line or memory differs from trials/{row['trial']}.json")
    return problems


def check_generated(head):
    with tempfile.TemporaryDirectory() as tmp:
        archive = subprocess.run(["git", "-C", str(ROOT), "archive", head], capture_output=True, check=True).stdout
        subprocess.run(["tar", "-x", "-C", tmp], input=archive, check=True)
        before = {name: (Path(tmp) / name).read_text() for name in ("RESULTS.md", "README.md")}
        subprocess.run([sys.executable, "-m", "bench.report"], cwd=tmp, check=True)
        return [f"{name} does not match bench.report output" for name, text in before.items()
                if (Path(tmp) / name).read_text() != text]


def main(base, head, runs, any_files=False):
    outside = sorted(changed_files(base, head) - ALLOWED)
    # Never run code from a PR that changes code; --any-files is for a person checking a code PR's records.
    if outside and not any_files:
        print("FAIL: the PR changes files a nightly PR may not touch: " + ", ".join(outside))
        return 1
    records, problems = new_records(base, head)
    problems += check_records(records, runs) + check_labels(records, head) + check_generated(head)
    for problem in problems:
        print("FAIL: " + problem)
    if not problems:
        print(f"PASS: {len(records)} new record(s) match the run files; RESULTS.md and README regenerate")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:4], any_files="--any-files" in sys.argv[4:]))
