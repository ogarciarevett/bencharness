"""Run trials: every harness in a trial gets the same tasks, model, budget and memory backend.

python3 -m bench.run --queue                      run every trial in trials/ that passes the checks and has no result
python3 -m bench.run --trial trials/X.json        run one trial
python3 -m bench.run --import trials/X.json R     record a trial that ran elsewhere (pilot results.json shape)
"""
import argparse
import contextlib
import fcntl
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bench import checks, guard, report, safety  # noqa: E402

TRIALS = ROOT / "trials"
RESULTS = ROOT / "results/trials.jsonl"
OWNER = ROOT / "queue/needs-owner.jsonl"
RUNS = ROOT / "runs"
QUEUE = ROOT / "queue/candidates.jsonl"
# The pinned pair brings its own runner and model calls (harness2 pilot through the Hyperag arm).
PAIRED = {"baseline", "harness2"}
# Adapters reviewed here that run no candidate program: candidate text reaches only Codex, inside its
# sandbox, through the model seam. Every other adapter is never imported in this process and runs only
# through sandboxed(), reaching a model only through the seam.
TRUSTED = PAIRED | {"native-codex-app", "omnigent", "baseline-native", "forge-harness", "tausik-core", "harness-engineering"}


def module_file(name):
    return ROOT / "harnesses" / ({"baseline": "baseline_harness", "baseline-native": "baseline_harness_native"}.get(name, name.replace("-", "_")) + ".py")


def adapter(name):
    return importlib.import_module("harnesses." + {"baseline": "baseline_harness", "baseline-native": "baseline_harness_native"}.get(name, name.replace("-", "_")))


def sandboxed(argv, workdir, seam_socket=None):
    """No network but the seam socket, writes only in workdir, no reads in the home folder, an empty environment."""
    workdir = Path(workdir).resolve()
    (workdir / "tmp").mkdir(parents=True, exist_ok=True)
    profile = ("(version 1)(allow default)(deny network*)" + safety.device_policy() +
               f'(deny file-write*)(allow file-write* (subpath "{workdir}") (subpath "/dev"))'
               f'(deny file-read* (subpath "{Path.home().resolve()}"))'
               f'(allow file-read* (subpath "{ROOT}") (subpath "{workdir}") (subpath "{Path(sys.prefix).resolve()}")'
               f' (subpath "{Path(sys.base_prefix).resolve()}"))')
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(workdir), "TMPDIR": str(workdir / "tmp"),
           "PYTHONDONTWRITEBYTECODE": "1"}
    if seam_socket:
        profile += f'(allow network-outbound (remote unix-socket (path-literal "{Path(seam_socket).resolve()}")))'
        env["BENCH_SEAM"] = str(seam_socket)
    return subprocess.run(["sandbox-exec", "-p", profile, *argv], capture_output=True, text=True, env=env, cwd=workdir)


def fetch(spec, name, work):
    """Check out the candidate at its pin. Cloning runs no candidate code: hooks and LFS filters are off."""
    url, pin, src = spec.get("sources", {}).get(name), spec["pins"][name], work / "src"
    if not url:
        return
    head = subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    if head == pin:
        return
    env = {**os.environ, "GIT_LFS_SKIP_SMUDGE": "1"}
    safe = ["-c", "core.hooksPath=/dev/null", "-c", "protocol.file.allow=never"]
    if not src.is_dir():
        subprocess.run(["git", *safe, "clone", "-q", "--filter=blob:none", "--no-checkout", url, str(src)], check=True, env=env)
    subprocess.run(["git", *safe, "-C", str(src), "checkout", "-q", "--detach", pin], check=True, env=env)


@contextlib.contextmanager
def seam(name, spec, work):
    """Start the model seam for one harness (bench/seam.py) and stop it afterwards."""
    if name in PAIRED:
        yield None
        return
    # A private 0700 folder in /tmp: Unix socket paths are limited to 104 bytes.
    path = Path(tempfile.mkdtemp(prefix="bench-", dir="/tmp")).resolve() / "seam.sock"
    python = Path(os.environ["BENCH_PILOT"]) / ".venv/bin/python"
    ledger = work.parent / ".seam" / name  # outside the folder a sandboxed harness may write
    server = subprocess.Popen([str(python), str(ROOT / "bench/seam.py"), str(path), str(work), json.dumps(spec), str(ledger)],
                              stdout=open(work / "seam.log", "a"), stderr=subprocess.STDOUT)
    try:
        for _ in range(300):
            if path.exists() or server.poll() is not None:
                break
            time.sleep(0.1)
        if not path.exists():
            raise RuntimeError(f"model seam did not start, see {work / 'seam.log'}")
        os.environ["BENCH_SEAM"] = str(path)
        yield path
    finally:
        os.environ.pop("BENCH_SEAM", None)
        server.terminate()
        server.wait()
        shutil.rmtree(path.parent, ignore_errors=True)


def call(name, spec, workdir):
    work = workdir / name
    work.mkdir(parents=True, exist_ok=True)
    if spec.get('mode')=='repo-app':
        return adapter(name).run(spec,work)
    fetch(spec, name, work)
    with seam(name, spec, work) as socket_path:
        if name in PAIRED:
            return adapter(name).run(spec, workdir)
        if name in TRUSTED:
            return adapter(name).run(spec, work)
        done = sandboxed([sys.executable, str(ROOT / "bench/run.py"), "--child", name, json.dumps(spec), str(work)],
                         work, socket_path)
    try:
        return json.loads(done.stdout.splitlines()[-1])
    except (IndexError, ValueError):
        return {task: {"status": "error", "error": done.stderr[-2000:]} for task in spec["tasks"]}


def verdict(by_harness):
    if any(r.get("status") != "complete" for r in by_harness.values()):
        return "no verdict: a harness did not finish"
    scores = sorted({r["score"] for r in by_harness.values()}, reverse=True)
    if len(scores) == 1:
        return "tie"
    top = [h for h, r in by_harness.items() if r["score"] == scores[0]]
    return f"{' and '.join(top)} higher by {scores[0] - scores[1]:.4f} (one trial)"


def bench_line(spec):
    sides = ", ".join(f"{chr(65 + i)}={h}@{spec['pins'][h]}" for i, h in enumerate(spec["harnesses"]))
    k = f" k={spec['k']}" if "k" in spec else ""
    k += f" mode={spec['mode']}" if "mode" in spec else ""
    return (f"BENCH model={spec['model']} {spec['effort']} {spec['service_tier']} tier, {sides}{k}, "
            f"{spec['budget_active_seconds']} active s per task side")


def record(spec, results):
    """results: {harness: {task: result}}. Appends one line to the store and regenerates RESULTS.md."""
    tasks = {}
    for task in spec["tasks"]:
        by_harness = {h: results.get(h, {}).get(task, {"status": "missing"}) for h in spec["harnesses"]}
        tasks[task] = {"results": by_harness, "verdict": verdict(by_harness)}
    row = {"trial": spec["id"], "recorded_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "suite": spec["suite"], "memory": spec["memory"], "memory_pin": spec.get("memory_pin"), "bench": bench_line(spec),
           "notes": spec.get("notes", ""), "tasks": tasks}
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    with RESULTS.open("a") as out:
        out.write(json.dumps(row) + "\n")
    report.write()
    return row


def recorded():
    return {json.loads(line)["trial"] for line in RESULTS.read_text().splitlines()} if RESULTS.exists() else set()


def notify(text):
    """Report after the run. BENCH_REPORT_CMD is any shell command; it gets the text on stdin."""
    print(text, flush=True)
    if os.environ.get("BENCH_REPORT_CMD"):
        subprocess.run(os.environ["BENCH_REPORT_CMD"], shell=True, input=text, text=True)


def escalate(spec, owner):
    seen = {json.loads(line)["trial"] for line in OWNER.read_text().splitlines()} if OWNER.exists() else set()
    if spec["id"] in seen:
        return
    OWNER.parent.mkdir(parents=True, exist_ok=True)
    with OWNER.open("a") as out:
        out.write(json.dumps({"trial": spec["id"], "needs": owner}) + "\n")
    notify(f"bencharness: trial {spec['id']} needs the owner: " + "; ".join(owner))


def run_trial(path):
    """True: recorded. False: skipped by a check. None: the host is busy, try again later."""
    spec = json.loads(Path(path).read_text())
    blocking, owner = checks.trial(spec)
    if owner:
        escalate(spec, owner)
        return False
    if blocking:
        print(f"skip {spec['id']}: " + "; ".join(blocking))
        return False
    missing = [name for name in spec["harnesses"] if not module_file(name).is_file() or name not in spec.get("pins", {})]
    if missing:
        print(f"skip {spec['id']}: no adapter or pin for " + ", ".join(missing))
        return False
    admitted = {json.loads(line)["url"] for line in QUEUE.read_text().splitlines()
                if json.loads(line)["status"] == "admitted"} if QUEUE.exists() else set()
    unlicensed = [n for n, url in spec.get("sources", {}).items() if url.removesuffix(".git") not in admitted]
    if unlicensed:
        print(f"skip {spec['id']}: not admitted by the licence check: " + ", ".join(unlicensed))
        return False
    refused = []
    for name in spec["harnesses"]:
        reasons = adapter(name).refuse(spec) if name in TRUSTED and hasattr(adapter(name), "refuse") else []
        refused += [f"{name}: " + "; ".join(reasons)] if reasons else []
    if refused:
        print(f"skip {spec['id']}: " + " | ".join(refused))
        return False
    busy = guard.problems()
    if busy:
        print(f"wait {spec['id']}: " + "; ".join(busy))
        return None
    RUNS.mkdir(exist_ok=True)
    with open(RUNS / ".lock", "w") as lock, contextlib.ExitStack() as resources:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)  # one heavy job at a time
        except BlockingIOError:
            print("wait: another trial is running")
            return None
        if spec.get('mode')=='repo-app':
            from bench.app_trial import exclusive
            if not resources.enter_context(exclusive()):
                print('wait: shared local build lock or another build is active')
                return None
        # Every adapter gets the same spec: same tasks, model, budget and memory backend.
        results = {}
        for name in spec["harnesses"]:
            try:
                results[name] = call(name, spec, RUNS / spec["id"])
            except Exception as error:  # clone, checkout or seam start: a bench error, not a result
                results[name] = {task: {"status": "error", "error": f"{type(error).__name__}: {error}"} for task in spec["tasks"]}
            for task in spec["tasks"]:
                results[name].setdefault(task, {"status": "error", "error": "the adapter returned no result for this task"})
            if any(r.get("status")=="blocked" for r in results[name].values()):
                print(f"wait {spec['id']}: host gate")
                return None
            if any(r.get("error") == "Host 1-minute load exceeded 20" for r in results[name].values()):
                # A host stop is not a result: the next scheduled run resumes with the unspent cap.
                print(f"wait {spec['id']}: {name} stopped for the host rules")
                return None
            if any(r.get("status") == "error" for r in results[name].values()):
                # An adapter crash is a bench bug, not a trial result: report it and record nothing.
                message = (f"bencharness: {spec['id']} not recorded, {name} hit a bench or model error: "
                       + next(r["error"] for r in results[name].values() if r.get("status") == "error")[-500:])
                print(message) if spec.get("report")=="different_scores" else notify(message)
                # A broken model (usage limit, auth) would fail every later trial too: stop for tonight.
                return None if any(r.get("model_error") for r in results[name].values()) else False
    row = record(spec, results)
    summary = f"bencharness: {spec['id']} recorded. " + "; ".join(f"{t}: {v['verdict']}" for t, v in row["tasks"].items())
    # Report only news: a candidate that beats the baseline (the first harness). Failures report above.
    wins = [t for t, v in row["tasks"].items() if v["verdict"].split(" ")[0] in spec["harnesses"][1:]]
    different = spec.get('report')=='different_scores' and any(v['verdict']!='tie' for v in row['tasks'].values())
    notify(summary) if wins or different else print(summary)
    return True


def queue(limit=None):
    """Run trials without a result, in name order; limit counts recorded trials (the nightly run uses 1)."""
    done, ran = recorded(), 0
    for path in sorted(TRIALS.glob("*.json")):
        if limit is not None and ran >= limit:
            return
        if json.loads(path.read_text())["id"] not in done:
            outcome = run_trial(path)
            if outcome is None:
                return  # host busy: stop here, the next scheduled run resumes
            ran += outcome


def import_pilot(trial_path, results_path):
    """Pilot shape: {task: {"A": result, "B": result, ...}}; sides map to the trial's harness order."""
    spec = json.loads(Path(trial_path).read_text())
    if spec["id"] in recorded():
        raise SystemExit(f"{spec['id']} is already recorded")
    data = json.loads(Path(results_path).read_text())
    results = {h: {t: data[t][chr(65 + i)] for t in spec["tasks"]} for i, h in enumerate(spec["harnesses"])}
    return record(spec, results)


def main(argv=None):
    global TRIALS, RESULTS, OWNER, RUNS, QUEUE
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue", action="store_true")
    parser.add_argument("--max", type=int, help="with --queue: record at most this many trials")
    parser.add_argument("--trial")
    parser.add_argument("--import", dest="imported", nargs=2, metavar=("TRIAL", "RESULTS"))
    parser.add_argument("--child", nargs=3, help=argparse.SUPPRESS)
    parser.add_argument("--workspace", type=Path, help="directory for trial state and saved reports")
    parser.add_argument("--report", action="store_true", help="regenerate saved charts and tables without running a trial")
    args = parser.parse_args(argv)
    if args.workspace:
        root = args.workspace.resolve()
        TRIALS, RESULTS = root / "trials", root / "results/trials.jsonl"
        OWNER, RUNS, QUEUE = root / "queue/needs-owner.jsonl", root / "runs", root / "queue/candidates.jsonl"
        report.ROOT, report.STORE = root, RESULTS
    if args.report:
        if not RESULTS.is_file() or not (report.ROOT / "README.md").is_file():
            parser.error("reporting needs results/trials.jsonl and README.md in the workspace")
        report.write()
        return
    if args.child:
        name, spec, workdir = args.child
        print(json.dumps(adapter(name).run(json.loads(spec), Path(workdir))))
    elif args.imported:
        print(import_pilot(*args.imported)["bench"])
    elif args.trial:
        run_trial(args.trial)
    else:
        queue(args.max)


if __name__ == "__main__":
    main()
