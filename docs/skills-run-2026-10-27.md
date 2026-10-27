# Skills run, 2026-10-27

The last scheduled job began at **2026-10-27T01:30:05Z (02:30 Madrid)** and launchd reported exit **0** before the scheduler reload. It wrote candidate additions to `queue/candidates.jsonl` in nightly PR #11, with **zero new benchmark records**. Its log reports stale desktop suite/dependency fingerprints and a missing pinned baseline checkout. The last trial-store benchmark timestamp remains 2026-10-23T21:26:15Z. Scheduler success therefore did not prove a benchmark ran.

The final manual attempt completed three paired tasks through the existing restricted model seam, holding the shared local build lock. It then stopped during research at the existing seam host gate (the stop message says load exceeded 20 for any load/memory rule). No complete 11-skill result was published. Code tasks use functional assertions in the adapter sandbox; prototype uses artifact/term checks only.

| Skill | Task | Baseline / skill pass rate | Tokens baseline / skill | Seconds baseline / skill | Delta pp |
|---|---|---:|---:|---:|---:|
| diagnosing-bugs | expiry-bug | 100% / 100% | 73203 / 99921 | 17.3 / 23.9 | +0 |
| tdd | retry-code | 100% / 100% | 73976 / 159954 | 23.3 / 43.1 | +0 |
| prototype | reservation-demo | 100% / 100% | 45721 / 100207 | 23.6 / 47.4 | +0 |

The remaining skills (research, domain-modeling, codebase-design, code-review, pr, wizard, grilling, writing-for-agents) are **pending**; comparable pass rate, tokens, time and delta are unavailable. These are one-task smoke checks, not statistically established effects.

Private final evidence: `runs/skills-2026-10-27-7629c7af116e/` in the skills worktree. Each completed side has `result.json` and model call logs. The runner source is the one in PR #12; the later core commit changes only test lock isolation. The canonical daily aggregate is absent because eight pairs are incomplete. Nightly uses its own persistent run directory and resumes its own completed sides on later attempts.

An earlier attempt completed two pairs, then deferred at swap 9059 MB/free memory 38% (start: load 8.47, swap 8674 MB, free memory 48%). Its initial grader used the macOS Python shim, which tried blocked Xcode discovery and exited 71; those zeros were invalid. Unchanged artifacts passed regrading with the resolved interpreter. Original receipts and `rescored-result.json` are preserved in the earlier run directory. The runner now treats grader startup failures as infrastructure errors. The final table above comes from fresh calls with that corrected runner.

Final run log:

```text
RUN diagnosing-bugs expiry-bug baseline
RUN diagnosing-bugs expiry-bug skill
RUN tdd retry-code skill
RUN tdd retry-code baseline
RUN prototype reservation-demo baseline
RUN prototype reservation-demo skill
RUN research offline-source-research skill
INCOMPLETE skills: research/skill: StopPilot: Host 1-minute load exceeded 20
```

Validation: `python3 -m unittest discover -s tests -q`: 151 tests passed, one skipped before the additional receipt/date validation test. Final focused checks: 19 tests passed. `sh -n schedule/nightly.sh schedule/review.sh` and report regeneration passed. No complete run, browser exercise, wizard execution or human interview was claimed.

Scheduler rollout (superseded): both native launchers were previously rebuilt to the separate source worktree, retaining the original repository for nightly state via `BENCH_SCHEDULE_REPO`. Those loaded instances were idle and had not run; their configuration does not prove scheduled execution. Original launchers and plists are preserved under the original repository's `runs/skills-scheduler-backup-2026-10-27/`.

The separate-source rollout is no longer supported: `BENCH_SCHEDULE_REPO` and `BENCH_SKILLS_ROOT` are ignored. After #12 and #13 merge, update the main checkout and reinstall both jobs with `schedule/install.sh` from that checkout, using the existing pilot/hyperag paths and report destination. Do not install or trigger the skills schedule before both merges. Nightly now runs the skills runner and evidence verifier inside its worktree reset to `origin/main`, so the executing runner source matches the result head checked by verification. A receipt from another runner source is rejected with an explicit source identity mismatch. This fix does not reinstall or trigger the existing launchd jobs; a full 11-pair run and scheduled publication remain unproven.
