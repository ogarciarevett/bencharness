# Skills run, 2026-10-27

The last scheduled job began at **2026-10-27T01:30:05Z (02:30 Madrid)** and launchd reports exit **0**. It wrote candidate additions to `queue/candidates.jsonl` in nightly PR #11, with **zero new benchmark records**. Its log reports stale desktop suite/dependency fingerprints and a missing pinned baseline checkout. The last trial-store benchmark timestamp remains 2026-10-23T21:26:15Z. Scheduler success therefore did not prove a benchmark ran.

The manual skills run completed two paired tasks through the existing restricted model seam. It started with load 8.47, swap 8674 MB, free memory 48%, holding the shared local build lock. Before the next pair it deferred at swap 9059 MB/free memory 38%. No complete 11-skill result was published. The functional checks run in the existing adapter sandbox. Initial grading used the macOS Python shim, which attempted blocked Xcode discovery and exited 71; those zero scores were invalid. Regrading the unchanged artifacts with the resolved interpreter passed all checks. Original receipts are preserved; corrected scores are in `rescored-result.json`. The runner now treats grading startup failures as infrastructure errors.

| Skill | Task | Baseline / skill pass rate | Tokens baseline / skill | Seconds baseline / skill | Delta pp |
|---|---|---:|---:|---:|---:|
| diagnosing-bugs | expiry-bug | 100% / 100% | 73207 / 103841 | 21.5 / 23.1 | +0 |
| tdd | retry-code | 100% / 100% | 73946 / 158799 | 27.7 / 31.2 | +0 |

The other skills (prototype, research, domain-modeling, codebase-design, code-review, pr, wizard, grilling, writing-for-agents) are **pending**; pass rate, tokens, time and delta are unavailable. These are one-task smoke checks, not statistically established effects.

Private evidence: `runs/skills-2026-10-27-2b8856ca1b2a/` in the skills worktree; each completed side has `result.json` and model call logs. The source runner changed only to add the nightly workspace CLI after this run began; task fixtures are unchanged; the grader interpreter fix described above was applied afterward. A later invocation uses the final runner identity and does not relabel these partial receipts.

Run log:

```text
RUN diagnosing-bugs expiry-bug baseline
RUN diagnosing-bugs expiry-bug skill
RUN tdd retry-code skill
RUN tdd retry-code baseline
DEFERRED skills: swap 9059 MB/free 38%: needs swap<8 GB or swap<10 GB with free>45%
```

Validation: `python3 -m unittest discover -s tests -q`: 151 tests passed, one skipped. `sh -n schedule/nightly.sh schedule/review.sh` passed. No complete run, browser exercise, wizard execution or human interview was claimed.

Scheduler rollout: both native launchers were rebuilt to this source worktree, retaining the original repository for nightly state via `BENCH_SCHEDULE_REPO`. Both launchd jobs are loaded and idle; calendars remain 02:30 and 07:30 local. Their newly loaded instances have not run yet. Original launchers and plists are preserved under the original repository’s `runs/skills-scheduler-backup-2026-10-27/`. The complete new scheduled path remains to be observed at its next calendar tick.
