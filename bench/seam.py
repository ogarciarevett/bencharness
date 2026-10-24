"""Model seam: the only way a non-pinned harness reaches a model.

The runner starts one seam per harness and trial, outside the sandbox, with the pilot's Python:
    $BENCH_PILOT/.venv/bin/python bench/seam.py SOCKET WORKDIR SPEC_JSON LEDGER_DIR
A harness, sandboxed or not, connects to the Unix socket in BENCH_SEAM and sends one JSON request per
connection: {"op": "call", "task", "workspace", "prompt", "label"} or {"op": "metrics", "task"}.

The seam runs Codex through our internal pilot Budget (round2.py, unchanged): pinned binary, Codex sandbox
on, command network off, gpt-6.1-sol, low or medium, service_tier=standard, one call at a time, and the trial's
active-second cap per task. It stops a call on the host rules. The harness chooses only the prompt and
a workspace inside its own folder.
"""
import json
import os
import socket
import sys
import time
from pathlib import Path

HOST_STOP = "Host 1-minute load exceeded 20"  # round2's error text for every host-rule stop


def request(op, **fields):
    """Client side, standard library only (works inside the sandbox)."""
    with socket.socket(socket.AF_UNIX) as conn:
        conn.connect(os.environ["BENCH_SEAM"])
        conn.sendall(json.dumps({"op": op, **fields}).encode() + b"\n")
        conn.shutdown(socket.SHUT_WR)
        reply = b"".join(iter(lambda: conn.recv(65536), b""))
    return json.loads(reply)


def refuse(spec):
    """Only supported subscription settings; every side receives the same trial spec."""
    fixed = {"model": "gpt-6.1-sol", "service_tier": "standard"}
    found = [f"{k} {spec.get(k)!r} is not {v!r}" for k, v in fixed.items() if spec.get(k) != v]
    if spec.get("effort") not in ("low", "medium"):
        found.append(f"effort {spec.get('effort')!r} is not low or medium")
    return found


def serve(path, workdir, spec, ledger):
    sys.path[:0] = [os.environ["BENCH_PILOT"], str(Path(__file__).resolve().parent.parent)]
    sys.dont_write_bytecode = True  # never write caches into the read-only pilot folder
    import round2
    from bench import guard, safety

    assert not refuse(spec), refuse(spec)
    round2.CAP = spec["budget_active_seconds"]
    safety.configure_round2(round2)
    config_args = round2.config_args
    round2.config_args = lambda: [*config_args(), "--ignore-rules", "-c", f'model_reasoning_effort="{spec["effort"]}"',
                                  "-c", 'features.apps=false', "-c", 'service_tier="standard"']
    loadavg, sample = os.getloadavg, [0., []]

    def guarded():
        # memory_pressure is slow: sample the memory and swap rules at most every 15 seconds.
        # The real load is passed in: guard.problems must not call the patched os.getloadavg.
        real = loadavg()
        if time.monotonic() - sample[0] > 15:
            sample[:] = [time.monotonic(), guard.problems(heavy=False, load=real[0])]
        return (99., *real[1:]) if sample[1] else real
    round2.os.getloadavg = guarded

    workdir, ledger = Path(workdir).resolve(), Path(ledger).resolve()
    if ledger == workdir or workdir in ledger.parents:
        raise SystemExit("the ledger must be outside the harness folder")
    ledger.mkdir(parents=True, exist_ok=True)
    budgets = {}

    def budget(task):
        if task not in spec["tasks"]:
            raise ValueError(f"task {task!r} is not in this trial")
        if task not in budgets:
            # Seconds spent before a host stop come from the seam's own ledger, which the harness
            # cannot write, never from files in the harness folder.
            spent = json.loads((ledger / f"{task}.json").read_text())["spent"] if (ledger / f"{task}.json").exists() else 0
            b = round2.Budget(workdir / task / "model-calls")
            b.start = time.monotonic() - spent
            b.deadline = b.start + round2.CAP
            budgets[task] = b
        return budgets[task]

    def save(task):
        (ledger / f"{task}.json").write_text(json.dumps({"spent": time.monotonic() - budgets[task].start}))

    def reset(task):
        # Like round2: only a host stop carries spent seconds into the next attempt. After a model
        # error (Codex exit, usage limit, auth) the attempt reruns with the full cap.
        (ledger / f"{task}.json").unlink(missing_ok=True)
        budgets.pop(task, None)

    def handle(message):
        if message["op"] == "metrics":
            metrics = budget(message["task"]).metrics()
            save(message["task"])
            return {"ok": True, "metrics": metrics}
        workspace = Path(message["workspace"]).resolve()
        if workdir not in workspace.parents:
            raise ValueError("workspace is outside this harness folder")
        task = message["task"]
        try:
            text, record = budget(task).call(workspace, message["prompt"], str(message["label"]))
            save(task)
            return {"ok": True, "text": text, "record": record}
        except round2.BudgetEnd as error:
            save(task)
            return {"ok": False, "kind": "BudgetEnd", "error": str(error)}
        except round2.StopPilot as error:
            save(task) if str(error) == HOST_STOP else reset(task)
            return {"ok": False, "kind": "StopPilot", "error": str(error)}

    Path(path).unlink(missing_ok=True)
    os.umask(0o077)  # the socket is for this user only
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(path)
        server.listen(1)
        while True:  # one request at a time: model calls stay serialized
            conn, _ = server.accept()
            with conn:
                data = b"".join(iter(lambda: conn.recv(65536), b""))
                try:
                    reply = handle(json.loads(data))
                except Exception as error:
                    reply = {"ok": False, "kind": "Refused", "error": str(error)}
                conn.sendall(json.dumps(reply).encode())


if __name__ == "__main__":
    serve(sys.argv[1], sys.argv[2], json.loads(sys.argv[3]), sys.argv[4])
