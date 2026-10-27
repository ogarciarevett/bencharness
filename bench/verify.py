"""Check a nightly PR's records against the run files, and that RESULTS.md regenerates. Read only.

python3 -m bench.verify BASE_REF HEAD_REF RUNS_DIR [--any-files]
Exit 0 and "PASS" when every record that HEAD_REF adds equals the result.json files the runner wrote
under RUNS_DIR, and RESULTS.md and the README table at HEAD_REF are exactly what bench.report makes
from HEAD_REF's store. Otherwise exit 1 and one "FAIL: ..." line per problem.
"""
import json
import re
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


def allowed(path):
    return path in ALLOWED or bool(re.fullmatch(r'results/skills/\d{4}-\d{2}-\d{2}\.(json|md)', path)) or (path.startswith("docs/charts/") and
                              (path.endswith(".svg") or Path(path).name == "README.md"))


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
    from bench import charts
    with tempfile.TemporaryDirectory() as tmp:
        archive = subprocess.run(["git", "-C", str(ROOT), "archive", head], capture_output=True, check=True).stdout
        subprocess.run(["tar", "-x", "-C", tmp], input=archive, check=True)
        try:
            charts.validate_output(Path(tmp) / "docs/charts")
            for name in ("RESULTS.md", "README.md"):
                path = Path(tmp) / name
                if path.is_symlink() or not path.is_file():
                    raise ValueError("Generated report must be a regular file")
        except ValueError as error:
            return [str(error)]
        def generated():
            paths = [Path(tmp) / name for name in ("RESULTS.md", "README.md")]
            paths += [p for p in (Path(tmp) / "docs/charts").rglob("*") if p.is_file()]
            return {str(p.relative_to(tmp)): p.read_bytes() for p in paths}
        before = generated()
        subprocess.run([sys.executable, "-m", "bench.report"], cwd=tmp, check=True)
        after = generated()
        return [f"{name} does not match bench.report output" for name in sorted(before.keys() | after.keys())
                if before.get(name) != after.get(name)]


def check_skills(base, head, runs):
    from bench.skills import table
    problems = []
    for path in changed_files(base, head):
        if not path.startswith('results/skills/'):
            continue
        if show(base, path):
            problems.append(f'{path}: existing skill results are immutable')
            continue
        try:
            record_path = str(Path(path).with_suffix('.json'))
            row = json.loads(show(head, record_path))
            if not re.fullmatch(r'skills-'+Path(path).stem+r'-[0-9a-f]{12}', row['run_id']):
                raise ValueError('invalid run id')
            local = Path(runs)/row['run_id']/'result.json'
            if any(p.is_symlink() for p in [local, *local.parents]):
                raise ValueError('run receipt ancestry contains a symlink')
            if json.loads(local.read_text()) != row:
                problems.append(f'{path}: skill record differs from run receipt')
            if show(head, str(Path(path).with_suffix('.md'))) != table(row['rows']):
                problems.append(f'{path}: skill table differs from record')
        except (OSError, ValueError, KeyError) as error:
            problems.append(f'{path}: cannot verify skill receipt: {error}')
    return problems


def main(base, head, runs, any_files=False):
    outside = sorted(path for path in changed_files(base, head) if not allowed(path))
    # Never run code from a PR that changes code; --any-files is for a person checking a code PR's records.
    if outside and not any_files:
        print("FAIL: the PR changes files a nightly PR may not touch: " + ", ".join(outside))
        return 1
    records, problems = new_records(base, head)
    problems += check_records(records, runs) + check_labels(records, head) + check_generated(head) + check_skills(base, head, runs)
    for problem in problems:
        print("FAIL: " + problem)
    if not problems:
        print(f"PASS: {len(records)} new record(s) match the run files; RESULTS.md and README regenerate")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:4], any_files="--any-files" in sys.argv[4:]))
