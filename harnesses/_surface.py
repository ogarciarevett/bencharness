"""Harness-surface trials: a candidate's text harness (AGENTS.md rules, skills) on the native loop.

Each task runs the native baseline harness side A of our internal pilot (round2.py run_pack, steps copied with
credit): the shared base workspace, one Codex proposal, one native search round, sealed scoring. The
only difference between harnesses is the files an adapter adds to that base. Candidate text reaches
only Codex, inside its sandbox (workspace-only files, command network off), through the model seam.
Sealed labels and graders stay on this side and never reach the workspace.

An adapter declares APPEND (files appended to AGENTS.md) and COPY ({source dir: workspace dir});
paths are relative to the candidate checkout the runner made at the trial's pin.
"""
import importlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOOP_PIN = "ac0aba4e"  # baseline harness checkout in the pilot folder
MEMORY = "Hyperag multilingual-e5-small, no reranker, no graph, limit 10"
NO_MEMORY = "none: every item carries its full evidence"


MAX_AGENT_CALLS = 6
# Agentic mode: a visible test runner in the workspace (standard library only). Labels it shows are
# the train items already in source.json; sealed items never reach the workspace.
CHECK = r"""import json, sys, traceback
from pathlib import Path

source = json.loads(Path("source.json").read_text())
try:
    manifest = json.loads(Path("output/manifest.json").read_text())
    scope = {}
    exec(compile(Path("output/plugin.py").read_text(), "output/plugin.py", "exec"), scope)
    task = {"params": manifest.get("params", {}), "context": source.get("context"), "train": source["train"],
            "items": [{"id": r["id"], "input": r["input"]} for r in source["train"]]}
    predictions = scope["run"](task)
except Exception:
    print("plugin failed to load or run:")
    traceback.print_exc(limit=5)
    sys.exit(1)
right = 0
for row in source["train"]:
    got, want = predictions.get(row["id"]), row["label"]
    if not isinstance(got, dict):
        print(f"{row['id']}: prediction is not a map: {got!r:.200}")
        continue
    wrong = [k for k in want if got.get(k) != want[k]]
    extra = [k for k in got if k not in want]
    right += not wrong and not extra
    for k in wrong[:5]:
        print(f"{row['id']} {k}: expected {want[k]!r:.200} got {got.get(k)!r:.200}")
    if extra:
        print(f"{row['id']}: unexpected keys {extra[:5]}")
print(f"visible: {right}/{len(source['train'])} items fully right")
sys.exit(0 if right == len(source["train"]) else 1)
"""


def suite(spec):
    """(repo checkout, path in it, commit): the task suite every harness of this trial runs."""
    if "suite_pin" in spec:
        pin = spec["suite_pin"]
        repo = ROOT if pin["repo"] == "bencharness" else Path(os.environ.get("BENCH_HYPERAG", "/nonexistent"))
        return repo, pin["path"], pin["commit"]
    return Path(os.environ.get("BENCH_HYPERAG", "/nonexistent")), "eval/harness2", spec.get("memory_pin")


def refuse(spec):
    sys.path.insert(0, str(ROOT))
    from bench import seam
    pilot = Path(os.environ.get("BENCH_PILOT", "/nonexistent"))
    found = seam.refuse(spec)
    repo, path, commit = suite(spec)
    memory = MEMORY if path == "eval/harness2" else NO_MEMORY
    if spec.get("memory") != memory:
        found.append(f"memory {spec.get('memory')!r} is not {memory!r}")
    head = subprocess.run(["git", "-C", str(pilot / "baseline"), "rev-parse", "HEAD"], capture_output=True, text=True).stdout
    if not head.startswith(LOOP_PIN):
        found.append(f"baseline harness checkout {head[:8] or 'missing'} is not the pinned {LOOP_PIN}")
    if spec.get("pins", {}).get("baseline-native", LOOP_PIN) != LOOP_PIN:
        found.append(f"baseline-native pin {spec['pins']['baseline-native']} is not the loop {LOOP_PIN} that runs")
    if subprocess.run(["git", "-C", str(repo), "cat-file", "-e", f"{commit}:{path}/packs.py"], capture_output=True).returncode:
        found.append(f"suite pin {commit!r} has no {path}/packs.py in {repo.name}")
    return found


def run(spec, workdir, adapter):
    """Called by the runner (any Python); the side itself runs with the pilot's Python."""
    # -m from the repo root: running the file directly would put harnesses/ first on the import path,
    # where harness2.py would shadow the real harness2 package.
    done = subprocess.run([str(Path(os.environ["BENCH_PILOT"]) / ".venv/bin/python"), "-m", "harnesses._surface",
                           adapter, json.dumps(spec), str(workdir)], capture_output=True, text=True, cwd=ROOT)
    try:
        return json.loads(done.stdout.splitlines()[-1])
    except (IndexError, ValueError):
        return {task: {"status": "error", "error": done.stderr[-2000:]} for task in spec["tasks"]}


def inside(src, path):
    """Candidate files are read outside the sandbox: refuse symlinks, which could point at secrets."""
    if path.is_symlink() or src.resolve() not in path.resolve().parents:
        raise ValueError(f"refusing {path.relative_to(src)}: a symlink or outside the checkout")
    return path


def add_surface(base, src, adapter, name):
    agents = base / "AGENTS.md"
    for rel in adapter.APPEND:
        inside(src, src / rel)
        agents.write_text(agents.read_text() + f"\n\n## Harness under test: {name} ({rel})\n\n" + (src / rel).read_text())
    for rel, dest in adapter.COPY.items():
        for path in sorted(inside(src, src / rel).rglob("*")):
            inside(src, path)
            target = base / dest / path.relative_to(src / rel)
            if path.is_file():
                if target.exists() and target.read_bytes() != path.read_bytes():
                    raise ValueError(f"{name} would overwrite {target.relative_to(base)}")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)


class SeamRefused(Exception):
    pass


def call(task, workspace, prompt, label):
    import round2
    from bench import seam
    reply = seam.request("call", task=task, workspace=str(workspace), prompt=prompt, label=label)
    if not reply["ok"]:
        raise {"BudgetEnd": round2.BudgetEnd, "StopPilot": round2.StopPilot}.get(reply["kind"], SeamRefused)(reply["error"])
    return reply


def visible_check(workspace):
    """The bench's own check between calls. Nothing from the workspace is executed directly: the
    plugin runs in the baseline harness plugin sandbox (deny by default, time and memory limits), the
    one sealed scoring uses, on the visible train items only; the comparison happens here."""
    import round2
    try:
        candidate = round2.load(workspace / "output", "codex")
        source = json.loads((workspace / "source.json").read_text())
        task = {"params": candidate["params"], "context": source.get("context"), "train": source["train"],
                "items": [{"id": r["id"], "input": r["input"]} for r in source["train"]]}
        predictions = round2.run_plugin(candidate["code"], task)
    except Exception as error:  # noqa: BLE001  any plugin failure is feedback for the next call
        return False, f"plugin failed to load or run: {type(error).__name__}: {str(error)[:1500]}"
    lines, right = [], 0
    for row in source["train"]:
        got, want = predictions.get(row["id"]), row["label"]
        if not isinstance(got, dict):
            lines.append(f"{row['id']}: prediction is not a map: {str(got)[:200]}")
            continue
        wrong = [k for k in want if got.get(k) != want[k]]
        extra = [k for k in got if k not in want]
        right += not wrong and not extra
        lines += [f"{row['id']} {k}: expected {want[k]!r:.200} got {got.get(k)!r:.200}" for k in wrong[:5]]
        lines += [f"{row['id']}: unexpected keys {extra[:5]}"] if extra else []
    lines.append(f"visible: {right}/{len(source['train'])} items fully right")
    return right == len(source["train"]), "\n".join(lines)[-4000:]


def agentic(task, workspace, result):
    """Multi-turn: the agent runs check.py itself; between calls the bench runs it too and hands the
    failures to the next call, until the visible items pass, the calls run out, or the budget ends."""
    import round2
    (workspace / "check.py").write_text(CHECK)
    command = f"{round2.PYTHON} check.py"
    prompt = round2.TASK + (f" Agentic mode: run `{command}` to test output/plugin.py on the labelled train items in "
                            "source.json; read the failures, fix the plugin, and run it again until every visible item "
                            "passes, then return.")
    calls, passed, report = 0, False, ""
    while calls < MAX_AGENT_CALLS:
        try:
            call(task, workspace, prompt, f"agent-{calls:02d}")
        except round2.BudgetEnd:
            if calls == 0 or not (workspace / "output/plugin.py").exists():
                raise
            break  # the cap ended a later call: score what the agent last delivered
        calls += 1
        passed, report = visible_check(workspace)
        if passed:
            break
        prompt = (round2.TASK + f" Agentic mode, continued: `{command}` still fails. Its last output:\n{report}\n"
                  "Fix output/plugin.py, rerun the check, and stop when every visible item passes.")
    result.update(agent_calls=calls, visible_pass=passed)


def side(task, spec, workdir, adapter, name):
    import round2
    from bench import seam
    out = workdir / task
    result_path = out / "result.json"
    if result_path.exists():
        saved = json.loads(result_path.read_text())
        if saved["status"] in ("complete", "budget-exhausted", "failed"):
            return saved  # host stops and bench errors run again
    root, native, base, source = round2.prepare(task)
    if (workdir / "src").is_dir():
        add_surface(base, workdir / "src", adapter, name)
    round2.write_json(out / "surface.json", {str(p.relative_to(base)): round2.sha(p) for p in sorted(base.rglob("*")) if p.is_file()})
    result = {"pack": task, "harness": name, "cap_seconds": spec["budget_active_seconds"], "pass_score": round2.PASS_SCORE}
    try:
        attempt = len(list(out.glob("worker-*")))
        workspace = out / f"worker-{attempt:02d}"
        round2.stage(base, source, workspace)
        mode = spec.get("mode", "one-shot")
        result["mode"] = mode
        if mode == "agentic":
            agentic(task, workspace, result)
        else:
            call(task, workspace, round2.TASK, "native-agent-proposer")
        shutil.copytree(workspace / "output", native.proposals / "pilot-proposal", dirs_exist_ok=True)
        native.rounds(1)
        result["selection"] = native.incumbent["id"]
        result.update(round2.evaluate(native, native.dir / "candidates" / native.incumbent["id"], name), status="complete")
    except SeamRefused as error:
        # The seam refused or broke: a bench error, never a harness result.
        return {**result, "status": "error", "error": "model seam: " + str(error)}
    except round2.BudgetEnd as error:
        result.update(status="budget-exhausted", error=str(error), score=None, **{"pass": None})
    except round2.StopPilot as error:
        # A host stop waits for the next run; any other stop (Codex exit, usage limit, auth) is not
        # a harness result.
        stopped = str(error) == seam.HOST_STOP
        result.update(status="blocked" if stopped else "error", error=str(error), score=None, **{"pass": None})
        result["model_error"] = not stopped
    except Exception as error:
        result.update(status="failed", error=str(error), score=None, **{"pass": False})
    result.update(seam.request("metrics", task=task)["metrics"])
    round2.write_json(result_path, result)
    return result


def main(name, spec, workdir):
    pilot = Path(os.environ["BENCH_PILOT"])
    repo, path, commit = suite(spec)
    arm = workdir / "suite"
    if not arm.exists():
        # The suite at its pinned commit, the same tree for every harness and every night: same packs,
        # same items, same saved retrieval if it has one.
        staging = workdir / "suite-pin"
        staging.mkdir(parents=True, exist_ok=True)
        archive = subprocess.run(["git", "-C", str(repo), "archive", commit, path], capture_output=True, check=True).stdout
        subprocess.run(["tar", "-x", "-C", str(staging)], input=archive, check=True)
        (staging / path).rename(arm)
        shutil.rmtree(staging)
        (arm / ".memory_pin").write_text(commit)
    marker = arm / ".memory_pin"
    if not marker.exists() or marker.read_text() != commit:
        # Resuming on another tree would mix two suites or memory backends under one label.
        return {task: {"status": "error", "error": f"{arm} is not the suite tree at {commit}"} for task in spec["tasks"]}
    os.environ["PILOT"] = str(pilot)
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(pilot), str(arm), str(ROOT)]
    # Registers the suite's packs, seeds and task text in round2 (the Hyperag arm calls it run.py).
    importlib.import_module("register" if (arm / "register.py").exists() else "run")
    from bench.run import adapter as load_adapter
    adapter = load_adapter(name)
    out = {}
    for task in spec["tasks"]:
        out[task] = side(task, spec, workdir, adapter, name)
        if out[task].get("status") in ("blocked", "error"):
            break  # the host is busy or the model broke: do not start the next task
    return out


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1], json.loads(sys.argv[2]), Path(sys.argv[3]))))
