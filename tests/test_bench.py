import importlib
import json
import os
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bench import checks, guard, report, research, run  # noqa: E402
from bench import seam  # noqa: E402
from harnesses import _pilot, _surface  # noqa: E402

FAKE_ROUND2 = """
import os
class BudgetEnd(BaseException): pass
class StopPilot(BaseException): pass
CAP = 0
def config_args(): return []
class Budget:
    def __init__(self, directory, resume=False): self.calls = []
    def call(self, workspace, prompt, label):
        if prompt == "stop": raise StopPilot("Host 1-minute load exceeded 20")
        if prompt == "limit": raise StopPilot("usage limit reached")
        self.calls.append(label)
        return "reply:" + prompt, {"label": label, "argv": config_args()}
    def metrics(self): return {"model_calls": len(self.calls), "cap": round(self.deadline - self.start)}
"""


class FakeSeam:
    """The real seam server over a fake pilot Budget: no model is called."""
    def __init__(self, effort="medium"):
        self.effort = effort

    def __enter__(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "pilot").mkdir()
        (self.tmp / "pilot/round2.py").write_text(FAKE_ROUND2)
        self.work = self.tmp / "work"
        (self.work / "handoff").mkdir(parents=True)
        # A harness may write anything in its folder: a forged elapsed time must not raise its cap.
        (self.work / "handoff/result.json").write_text(json.dumps({"wall_seconds": -1e9}))
        self.sock = Path(f"/tmp/bench-test-{os.getpid()}.sock")
        spec = {**TRIAL, "budget_active_seconds": 600, "effort": self.effort}
        self.ledger = self.tmp / "ledger"
        self.server = subprocess.Popen([sys.executable, str(ROOT / "bench/seam.py"), str(self.sock), str(self.work), json.dumps(spec),
                                        str(self.ledger)],
                                       env={**os.environ, "BENCH_PILOT": str(self.tmp / "pilot")})
        for _ in range(100):
            if self.sock.exists():
                break
            time.sleep(0.05)
        os.environ["BENCH_SEAM"] = str(self.sock)
        return self

    def __exit__(self, *exc):
        os.environ.pop("BENCH_SEAM")
        self.server.terminate()
        self.server.wait()
        self.sock.unlink(missing_ok=True)

TRIAL = json.loads((ROOT / "trials/2026-10-23-hyperag-harness2-arm.json").read_text())


def result(score, status="complete"):
    return {"status": status, "score": score, "pass": False, "wall_seconds": 75.2, "model_calls": 1, "cost_estimate_usd": 0.08}


class Guard(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(guard.parse_free_percent("x\nSystem-wide memory free percentage: 54%\n"), 54)
        self.assertEqual(guard.parse_swap_mb("total = 7168.00M  used = 6147.81M  free = 1020.19M  (encrypted)"), 6147.81)


class Checks(unittest.TestCase):
    def test_first_trial_passes(self):
        self.assertEqual(checks.trial(TRIAL), ([], []))

    def test_money_and_accounts_go_to_owner(self):
        _, owner = checks.trial({**TRIAL, "model": "gpt-6.1-sol-pro", "paid_usd": 5, "needs_account": "x.ai"})
        self.assertEqual(len(owner), 3)
        self.assertTrue(checks.trial({**TRIAL, "service_tier": "priority"})[1])
        self.assertTrue(checks.trial({**TRIAL, "model": None})[1])

    def test_over_budget_is_skipped(self):
        self.assertTrue(checks.trial({**TRIAL, "budget_active_seconds": 5000})[0])

    def test_claude_is_blocked_even_for_an_untrusted_adapter(self):
        for model in ("claude-opus-5-5", "claude-sonnet-5-5"):
            self.assertTrue(checks.trial({**TRIAL, "model": model})[0])

    def test_licence(self):
        self.assertIsNone(checks.licence({"licence": "MIT"}))
        self.assertIsNotNone(checks.licence({"licence": "NOASSERTION"}))
        self.assertIsNotNone(checks.licence({}))


class Record(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "README.md").write_text(f"a\n{report.START}\nold\n{report.END}\nb\n")
        patches = [mock.patch.object(run, "RESULTS", tmp / "results/trials.jsonl"),
                   mock.patch.object(run, "OWNER", tmp / "queue/needs-owner.jsonl"),
                   mock.patch.object(report, "STORE", tmp / "results/trials.jsonl"),
                   mock.patch.object(report, "ROOT", tmp),
                   mock.patch.object(_pilot, "mismatch", return_value=[])]  # no real pilot checkout here
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.tmp = tmp

    def test_verdict(self):
        self.assertEqual(run.verdict({"a": result(0.5), "b": result(0.5)}), "tie")
        self.assertEqual(run.verdict({"a": result(0.7), "b": result(0.5)}), "a higher by 0.2000 (one trial)")
        self.assertTrue(run.verdict({"a": result(0.7), "b": result(None, "failed")}).startswith("no verdict"))

    def test_bench_line(self):
        self.assertEqual(run.bench_line(TRIAL), "BENCH model=gpt-6.1-sol medium standard tier, "
                         "A=baseline@ac0aba4e, B=harness2@1cfe254e k=1, 1200 active s per task side")

    def test_import_writes_store_results_and_readme(self):
        pilot = {t: {"A": result(0.7359), "B": result(0.7359)} for t in TRIAL["tasks"]}
        source = self.tmp / "pilot.json"
        source.write_text(json.dumps(pilot))
        trial = self.tmp / "trial.json"
        trial.write_text(json.dumps(TRIAL))
        run.import_pilot(trial, source)
        self.assertEqual(run.recorded(), {TRIAL["id"]})
        with self.assertRaises(SystemExit):
            run.import_pilot(trial, source)
        self.assertIn("| handoff | harness2 | complete | 0.7359 |", (self.tmp / "RESULTS.md").read_text())
        readme = (self.tmp / "README.md").read_text()
        self.assertNotIn("old", readme)
        self.assertTrue(readme.startswith("a\n") and readme.endswith("b\n"))

    def test_owner_trial_never_runs_and_escalates_once(self):
        path = self.tmp / "paid.json"
        path.write_text(json.dumps({**TRIAL, "paid_usd": 10}))
        with mock.patch.object(run, "call") as call, mock.patch.object(run, "notify") as notify:
            self.assertFalse(run.run_trial(path))
            self.assertFalse(run.run_trial(path))
        call.assert_not_called()
        notify.assert_called_once()

    def test_busy_host_waits(self):
        path = self.tmp / "t.json"
        path.write_text(json.dumps(TRIAL))
        with mock.patch.object(run.guard, "problems", return_value=["load 25.0 is not under 20"]) as problems, \
                mock.patch.object(run, "call") as call:
            self.assertFalse(run.run_trial(path))
        problems.assert_called_once()
        call.assert_not_called()

    def test_spec_the_runner_cannot_honour_is_skipped_not_recorded(self):
        path = self.tmp / "t.json"
        path.write_text(json.dumps(TRIAL))
        with mock.patch.object(_pilot, "mismatch", return_value=["budget"]), mock.patch.object(run, "call") as call:
            self.assertFalse(run.run_trial(path))
        call.assert_not_called()
        self.assertEqual(run.recorded(), set())

    def test_missing_pin_skips_before_any_call(self):
        path = self.tmp / "t.json"
        path.write_text(json.dumps({**TRIAL, "harnesses": ["baseline", "unknown-harness"]}))
        with mock.patch.object(run, "call") as call:
            self.assertFalse(run.run_trial(path))
        call.assert_not_called()

    def test_host_stop_is_not_recorded(self):
        path = self.tmp / "t.json"
        path.write_text(json.dumps(TRIAL))
        stopped = {t: {"status": "blocked", "error": "Host 1-minute load exceeded 20"} for t in TRIAL["tasks"]}
        with mock.patch.object(run.guard, "problems", return_value=[]), mock.patch.object(run, "RUNS", self.tmp / "runs"), \
                mock.patch.object(run, "call", return_value=stopped) as call:
            self.assertFalse(run.run_trial(path))
        call.assert_called_once()  # reached the run, stopped after the first harness
        self.assertEqual(run.recorded(), set())

    def test_unadmitted_source_is_skipped(self):
        path = self.tmp / "t.json"
        path.write_text(json.dumps({**TRIAL, "sources": {"baseline": "https://github.com/x/not-admitted"}}))
        queue = self.tmp / "candidates.jsonl"
        queue.write_text(json.dumps({"url": "https://github.com/x/not-admitted", "status": "rejected: licence"}) + "\n")
        with mock.patch.object(run, "QUEUE", queue), mock.patch.object(run, "call") as call:
            self.assertFalse(run.run_trial(path))
        call.assert_not_called()

    def test_queue_stops_when_the_host_is_busy(self):
        trials = self.tmp / "trials"
        trials.mkdir()
        for name in ("a", "b"):
            (trials / f"{name}.json").write_text(json.dumps({**TRIAL, "id": name}))
        with mock.patch.object(run, "TRIALS", trials), mock.patch.object(run, "run_trial", return_value=None) as attempt:
            run.queue(1)
        attempt.assert_called_once()


    def test_clone_or_seam_failure_is_reported_not_recorded(self):
        path = self.tmp / "t.json"
        path.write_text(json.dumps(TRIAL))
        with mock.patch.object(run.guard, "problems", return_value=[]), mock.patch.object(run, "RUNS", self.tmp / "runs"), \
                mock.patch.object(run, "call", side_effect=RuntimeError("model seam did not start")), \
                mock.patch.object(run, "notify") as notify:
            self.assertFalse(run.run_trial(path))
        self.assertEqual(run.recorded(), set())
        self.assertIn("seam did not start", notify.call_args[0][0])

    def test_model_error_stops_the_night(self):
        path = self.tmp / "t.json"
        path.write_text(json.dumps(TRIAL))
        broken = {t: {"status": "error", "error": "usage limit reached", "model_error": True} for t in TRIAL["tasks"]}
        with mock.patch.object(run.guard, "problems", return_value=[]), mock.patch.object(run, "RUNS", self.tmp / "runs"), \
                mock.patch.object(run, "call", return_value=broken), mock.patch.object(run, "notify"):
            self.assertIsNone(run.run_trial(path))
        self.assertEqual(run.recorded(), set())

    def test_adapter_crash_is_reported_not_recorded(self):
        path = self.tmp / "t.json"
        path.write_text(json.dumps(TRIAL))
        crashed = {t: {"status": "error", "error": "Traceback"} for t in TRIAL["tasks"]}
        with mock.patch.object(run.guard, "problems", return_value=[]), mock.patch.object(run, "RUNS", self.tmp / "runs"), \
                mock.patch.object(run, "call", return_value=crashed), mock.patch.object(run, "notify") as notify:
            self.assertFalse(run.run_trial(path))
        self.assertEqual(run.recorded(), set())
        self.assertIn("bench or model error", notify.call_args[0][0])


    def test_only_a_candidate_win_is_reported(self):
        path = self.tmp / "t.json"
        path.write_text(json.dumps(TRIAL))
        for scores, reported in (((0.7, 0.7), False), ((0.9, 0.7), False), ((0.6, 0.7), True)):
            results = {h: {t: result(s) for t in TRIAL["tasks"]} for h, s in zip(TRIAL["harnesses"], scores)}
            with mock.patch.object(run.guard, "problems", return_value=[]), mock.patch.object(run, "RUNS", self.tmp / "runs"), \
                    mock.patch.object(run, "call", side_effect=lambda name, spec, work: results[name]), \
                    mock.patch.object(run, "notify") as notify:
                self.assertTrue(run.run_trial(path))
            self.assertEqual(notify.called, reported, scores)
            run.RESULTS.unlink()


class Verify(unittest.TestCase):
    def test_records_must_equal_the_run_files(self):
        from bench import verify
        runs = Path(tempfile.mkdtemp())
        good = {"status": "complete", "score": 0.5}
        (runs / "t1/h/ledger").mkdir(parents=True)
        (runs / "t1/h/ledger/result.json").write_text(json.dumps(good))
        row = {"trial": "t1", "tasks": {"ledger": {"results": {"h": good}, "verdict": "tie"}}}
        self.assertEqual(verify.check_records([row], runs), [])
        forged = {"trial": "t1", "tasks": {"ledger": {"results": {"h": {**good, "score": 0.9}}, "verdict": "tie"}}}
        self.assertEqual(len(verify.check_records([forged], runs)), 1)
        claimed = {"trial": "t1", "tasks": {"ledger": {"results": {"h": good}, "verdict": "h higher by 0.5000 (one trial)"}}}
        self.assertIn("does not follow", verify.check_records([claimed], runs)[0])
        missing = {"trial": "t2", "tasks": {"ledger": {"results": {"h": good}, "verdict": "tie"}}}
        self.assertIn("no run file", verify.check_records([missing], runs)[0])

    def test_real_history_passes(self):
        from bench import verify
        if not (ROOT / "runs/2026-10-23-surface-harness-engineering").is_dir():
            self.skipTest("needs the local run files")
        self.assertEqual(verify.main("2607c09", "e58f228", str(ROOT / "runs")), 0)


class Pilot(unittest.TestCase):
    def test_spec_that_the_runner_cannot_honour_is_refused(self):
        with mock.patch.dict("os.environ", {"BENCH_PILOT": "/nonexistent"}):
            wrong = _pilot.refuse({**TRIAL, "budget_active_seconds": 600})
        self.assertTrue(any("budget_active_seconds" in w for w in wrong))
        self.assertTrue(any("checkout missing" in w for w in wrong))


class Seam(unittest.TestCase):
    def test_calls_metrics_and_refusals(self):
        with FakeSeam() as fake:
            workspace = fake.work / "handoff/worker-00"
            workspace.mkdir(parents=True)
            reply = seam.request("call", task="handoff", workspace=str(workspace), prompt="hi", label="p")
            self.assertEqual(reply["text"], "reply:hi")
            self.assertEqual(seam.request("metrics", task="handoff")["metrics"], {"model_calls": 1, "cap": 600})
            self.assertIn("spent", json.loads((fake.ledger / "handoff.json").read_text()))
            self.assertEqual(os.stat(fake.sock).st_mode & 0o077, 0)  # no group or other access
            outside = seam.request("call", task="handoff", workspace=str(fake.tmp), prompt="hi", label="p")
            self.assertEqual((outside["ok"], outside["kind"]), (False, "Refused"))
            self.assertFalse(seam.request("call", task="other", workspace=str(workspace), prompt="hi", label="p")["ok"])
            stop = seam.request("call", task="handoff", workspace=str(workspace), prompt="stop", label="p")
            self.assertEqual((stop["kind"], stop["error"]), ("StopPilot", seam.HOST_STOP))
            self.assertTrue((fake.ledger / "handoff.json").exists())  # a host stop keeps the spent seconds
            seam.request("call", task="handoff", workspace=str(workspace), prompt="limit", label="p")
            self.assertFalse((fake.ledger / "handoff.json").exists())  # a model error resets them

    def test_low_effort_reaches_the_budget_with_the_fixed_tier(self):
        for effort in ("low", "medium"):
            with self.subTest(effort=effort), FakeSeam(effort) as fake:
                workspace = fake.work / "handoff/worker-00"
                workspace.mkdir(parents=True)
                reply = seam.request("call", task="handoff", workspace=str(workspace), prompt="hi", label="p")
                self.assertTrue(reply["ok"], reply)
                self.assertIn(f'model_reasoning_effort="{effort}"', reply["record"]["argv"])
                self.assertIn('service_tier="standard"', reply["record"]["argv"])
                self.assertIn("--ignore-rules", reply["record"]["argv"])
                self.assertIn("features.apps=false", reply["record"]["argv"])

    def test_refuses_a_spec_the_budget_cannot_honour(self):
        self.assertEqual(seam.refuse(TRIAL), [])
        self.assertEqual(seam.refuse({**TRIAL, "effort": "low"}), [])
        self.assertTrue(seam.refuse({**TRIAL, "effort": "unknown"}))
        self.assertEqual(len(seam.refuse({**TRIAL, "model": "claude-opus-5-5", "service_tier": None})), 2)


class Surface(unittest.TestCase):
    def test_baseline_adapter_in_surface_runner(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "suite").mkdir()
            (work / "suite/.memory_pin").write_text("pin")
            real_import = importlib.import_module
            def load(name):
                return None if name == "run" else real_import(name)
            with mock.patch.dict(os.environ, {"BENCH_PILOT": tmp}), \
                 mock.patch.object(sys, "path", list(sys.path)), \
                 mock.patch.object(sys, "dont_write_bytecode", True), \
                 mock.patch.object(_surface, "suite", return_value=(work, "packs", "pin")), \
                 mock.patch.object(_surface.importlib, "import_module", side_effect=load), \
                 mock.patch.object(_surface, "side", return_value={"status": "complete"}) as side:
                _surface.main("baseline-native", {"tasks": ["sheet"]}, work)
                self.assertEqual(side.call_args.args[3].__name__, "harnesses.baseline_harness_native")

    def test_appends_rules_copies_skills_and_never_overwrites(self):
        tmp = Path(tempfile.mkdtemp())
        base, src = tmp / "base", tmp / "src"
        (base / ".agents/skills/ponytail").mkdir(parents=True)
        (base / ".agents/skills/ponytail/SKILL.md").write_text("ours")
        (base / "AGENTS.md").write_text("base rules")
        (src / "skills/guard").mkdir(parents=True)
        (src / "skills/guard/SKILL.md").write_text("theirs")
        (src / "AGENTS.md").write_text("their rules")
        adapter = types.SimpleNamespace(APPEND=["AGENTS.md"], COPY={"skills": ".agents/skills"})
        _surface.add_surface(base, src, adapter, "cand")
        self.assertIn("their rules", (base / "AGENTS.md").read_text())
        self.assertTrue((base / "AGENTS.md").read_text().startswith("base rules"))
        self.assertEqual((base / ".agents/skills/guard/SKILL.md").read_text(), "theirs")
        (src / "skills/ponytail").mkdir()
        (src / "skills/ponytail/SKILL.md").write_text("replaced")
        with self.assertRaises(ValueError):
            _surface.add_surface(base, src, types.SimpleNamespace(APPEND=[], COPY=adapter.COPY), "cand")
        self.assertEqual((base / ".agents/skills/ponytail/SKILL.md").read_text(), "ours")


class SurfaceSymlinks(unittest.TestCase):
    def test_symlinked_candidate_files_are_refused(self):
        tmp = Path(tempfile.mkdtemp())
        base, src = tmp / "base", tmp / "src"
        base.mkdir()
        (base / "AGENTS.md").write_text("base")
        (src / "skills/x").mkdir(parents=True)
        secret = tmp / "secret.txt"
        secret.write_text("token")
        (src / "skills/x/SKILL.md").symlink_to(secret)
        (src / "AGENTS.md").symlink_to(secret)
        for adapter in (types.SimpleNamespace(APPEND=["AGENTS.md"], COPY={}), types.SimpleNamespace(APPEND=[], COPY={"skills": ".agents/skills"})):
            with self.assertRaises(ValueError):
                _surface.add_surface(base, src, adapter, "cand")
        self.assertEqual((base / "AGENTS.md").read_text(), "base")
        self.assertFalse((base / ".agents/skills/x/SKILL.md").exists())


class Research(unittest.TestCase):
    def test_merge_dedupes_and_admits_by_licence(self):
        found = [{"kind": "repo", "url": "u1", "licence": "MIT"}, {"kind": "repo", "url": "u1", "licence": "MIT"},
                 {"kind": "repo", "url": "u2", "licence": None}, {"kind": "paper", "url": "u3"},
                 {"kind": "repo", "url": "old"}]
        added = research.merge([{"url": "old"}], found, "now")
        self.assertEqual([r["status"] for r in added], ["admitted", "rejected: licence unknown is not an open licence", "paper"])

    def test_parse_arxiv(self):
        atom = ('<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/1</id>'
                '<title>A  harness\n paper</title><summary> text </summary></entry></feed>')
        self.assertEqual(list(research.parse_arxiv(atom)),
                         [{"kind": "paper", "url": "http://arxiv.org/abs/1", "name": "A harness paper", "summary": "text"}])


@unittest.skipUnless(sys.platform == "darwin", "sandbox-exec is macOS only")
class Sandbox(unittest.TestCase):
    def probe(self, code):
        work = Path(tempfile.mkdtemp())
        return run.sandboxed([sys.executable, "-c", code], work), work

    def test_allowed(self):
        done, work = self.probe("open('out.txt', 'w').write('ok'); import os, json; print(json.dumps(sorted(os.environ)))")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual((work / "out.txt").read_text(), "ok")
        # macOS adds LC_CTYPE and __CF_USER_TEXT_ENCODING itself; nothing else from the parent passes.
        self.assertEqual(set(json.loads(done.stdout)) - {"LC_CTYPE", "__CF_USER_TEXT_ENCODING"},
                         {"HOME", "PATH", "PYTHONDONTWRITEBYTECODE", "TMPDIR"})

    def test_network_is_denied(self):
        self.assertNotEqual(self.probe("import socket; socket.create_connection(('1.1.1.1', 443), 5)")[0].returncode, 0)

    def test_writes_outside_the_workdir_are_denied(self):
        target = ROOT / "harnesses/_sandbox_probe.py"
        self.assertNotEqual(self.probe(f"open({str(target)!r}, 'w')")[0].returncode, 0)
        self.assertFalse(target.exists())

    def test_sandboxed_code_reaches_the_seam_and_nothing_else(self):
        with FakeSeam() as fake:
            (fake.work / "w").mkdir()
            code = ("import sys, json, socket; sys.path.insert(0, %r); from bench import seam; "
                    "print(json.dumps(seam.request('call', task='handoff', workspace=%r, prompt='hi', label='x')))"
                    % (str(ROOT), str(fake.work / "w")))
            done = run.sandboxed([sys.executable, "-c", code], fake.work, fake.sock)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(json.loads(done.stdout)["text"], "reply:hi")
            tcp = run.sandboxed([sys.executable, "-c", "import socket; socket.create_connection(('1.1.1.1', 443), 5)"], fake.work, fake.sock)
            self.assertNotEqual(tcp.returncode, 0)

    def test_home_folder_reads_are_denied(self):
        self.assertNotEqual(self.probe(f"import os; os.listdir({str(Path.home())!r})")[0].returncode, 0)


if __name__ == "__main__":
    unittest.main()
