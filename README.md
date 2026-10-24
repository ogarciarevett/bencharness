# bencharness

Compare agent harnesses on the same tasks, model, memory, and time budget.

![bencharness command-line help](docs/cli.png)

*Actual output of `python3 -m bench.run --help`; no model call is made.*

## Quick start

Clone the repository. The Python runner uses only the standard library; Python 3.12
is recommended. Reporting works without a model account or external checkouts.

```sh
git clone https://github.com/ogarciarevett/bencharness.git && cd bencharness
```

```sh
python3 -m bench.run --help  # inspect the CLI shown above
python3 -m bench.report     # regenerate RESULTS.md and the README table offline
head -n 10 RESULTS.md       # inspect the recorded harness2 comparison
```

This displays saved measurements. It does not rerun a benchmark. Executing trials
requires macOS, the configured subscription runtime, and pinned external assets;
see [Running trials](#running-trials).

## What it measures

- Each trial fixes the task suite, harness revisions, model settings, memory backend,
  and active time budget so the recorded comparison has an explicit scope.
- Sealed checks stay outside the candidate workspace. Visible checks let an agent
  test its implementation without exposing the scoring set.
- Records keep scores alongside active seconds, model-call counts, and estimated
  model costs. A tie in score can still have a different runtime or cost.
- Reports regenerate from [results/trials.jsonl](results/trials.jsonl), without
  calling a model. Trial errors and host pauses are separate from completed scores.

## One harness2 trial

On our tasks, in one trial, Google's [harness2](https://github.com/google-research/harness2)
tied our baseline harness on both saved-retrieval tasks:

| Task | Score, both harnesses | Active seconds, baseline / harness2 | Model calls, baseline / harness2 | Estimated USD, baseline / harness2 |
| --- | --- | --- | --- | --- |
| handoff | 0.7359 | 75.2 / 793.1 | 1 / 10 | 0.0814 / 0.8701 |
| engineering | 0.7949 | 94.7 / 503.6 | 1 / 7 | 0.1142 / 0.9102 |

These are **one trial, 13 sealed items per task, on our own tasks**. All final
plugins reached the retrieval ceiling, and compose was a no-op on both tasks.
The tie therefore shows **no harness effect either way**. Runtime was about 10.6x
and 5.3x higher; estimated model cost was about 10.7x and 8.0x higher, respectively.
Both scores were below the trial's pass threshold. This does not establish how
harness2 performs on other tasks or at other settings.

The full generated table below includes other exploratory trials. Those rows are
separate experiments, not repetitions of the harness2 comparison.

## How a trial works

1. A JSON specification in [trials/](trials/) names the suite, tasks, harnesses,
   source revisions, model, reasoning effort, memory backend, and time budget.
2. `bench.checks` validates the specification and candidate licence. Host admission
   and a trial lock determine whether execution can start. A host pause preserves
   the remaining active budget instead of creating a completed score.
3. The runner prepares pinned candidate sources and invokes an adapter from
   [harnesses/](harnesses/). Text-surface adapters add rules or skills to a common
   base loop; they do not install the candidate's own CLI, servers, or hooks.
4. A trusted scorer checks the output against sealed items. The runner stores the
   result and its settings; `bench.report` generates the tables in this file and
   [RESULTS.md](RESULTS.md).

The saved-retrieval suite measures handoff and engineering against fixed retrieval
inputs. The synthetic rules suite covers ledger events, layered configuration,
querying, recurrence, and a spreadsheet engine. Each item includes its evidence.
The desktop suite stages a multi-file Tauri 2 / Rust / React work queue and uses
22 sealed headless checks, including Tauri MockRuntime IPC. It creates no native
window and provides no packaging or device acceptance.

## Running trials

The report and unit tests use the Python standard library. Trial execution is
macOS-specific and fails closed without its sandbox or pinned assets. The pinned
pilot adapters require `BENCH_PILOT` (a compatible pilot directory containing
`round2.py`, its virtual environment and pinned harness checkouts) and
`BENCH_HYPERAG` (the compatible retrieval evaluation checkout). These external
assets are not distributed here, so cloning this repo alone cannot reproduce the
historical model runs. The desktop comparison also requires staged offline
Rust/Node dependencies, the pinned executor, and matching fingerprints.
Set `BENCHARNESS_BUILD_LOCK` to the shared build lock when coordinating local
workers; its default is `.cache/local-ci-build.lock` inside this repository.

```sh
python3 -m unittest discover -s tests -v                    # local tests
python3 -m bench.run --trial trials/<id>.json               # configured model run
python3 -m bench.run --queue                               # pending configured trials
python3 -m bench.run --import trials/<id>.json results.json # import existing pilot output
```

Executing a trial can consume subscription usage. Fixed checks currently accept
only the configured model and standard subscription tier; paid-API trials and new
accounts are escalated. Model names in trial records describe the evaluated
runtime. Estimated model cost is a measurement, not an invoice or a claim that
those API charges were paid.

For a text adapter, declare `APPEND` (files appended to AGENTS.md) and `COPY`
(candidate directories copied into the workspace), as in
[harnesses/forge_harness.py](harnesses/forge_harness.py). Add a reviewed adapter,
a pinned specification, and an admitted candidate record. Adapters must refuse
settings they cannot honour.

## Execution boundaries

The shared model seam runs one call at a time with a fixed active budget.
`schedule/codex-safe` starts the subscription runtime with a temporary configuration
home, an empty extension allow list, and plugins, connected apps, hooks, browser
and computer tools, and delegation disabled. Command networking and device access
are denied. An inventory or policy failure refuses startup.

Untrusted adapters execute in a separate leaf sandbox with a restricted environment,
workspace writes only, and no home-directory reads. Submitted desktop programs
run without process creation; trusted compilation separately permits child
processes. Cleanup covers the original process group and observable descendants,
not arbitrary detached sessions. Trusted compilers must not daemonize.

The app broker pins the model, effort, workspace and command policy, requires
server confirmation, and keeps authentication and protected receipts outside the
candidate sandbox. Desktop comparisons use the candidate's pinned executor,
not its full server. Standard tier is requested; the current CLI may omit an
unsupported tier, so desktop results must disclose that limitation.

Host admission checks load, swap, free memory and known heavy runners. It is
host-specific and is not a general scheduler or a guarantee that unrelated jobs
are absent. Model and desktop compilation work must be serialized on the host.

## Optional scheduling

[schedule/](schedule/) contains nightly candidate discovery and trial scripts,
a reporting hook, and a morning review script. Installation is opt-in via
`schedule/install.sh`; the quick start installs no background jobs. Configure
external assets and `BENCH_REPORT_TO` before installing on macOS. Nightly runs
open a PR; the morning script checks stored evidence, requests a separate
read-only review, and merges only when its checks and exact-head CI succeed.

## Results

<!-- results:start -->
| Trial | Task | Harness | Status | Score | Pass | Active s | Model calls | USD est. | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-10-23-hyperag-harness2-arm | handoff | baseline | complete | 0.7359 | fail | 75.2 | 1 | 0.0814 | tie |
| 2026-10-23-hyperag-harness2-arm | handoff | harness2 | complete | 0.7359 | fail | 793.1 | 10 | 0.8701 | tie |
| 2026-10-23-hyperag-harness2-arm | engineering | baseline | complete | 0.7949 | fail | 94.7 | 1 | 0.1142 | tie |
| 2026-10-23-hyperag-harness2-arm | engineering | harness2 | complete | 0.7949 | fail | 503.6 | 7 | 0.9102 | tie |
| 2026-10-23-surface-forge-harness | handoff | baseline-native | complete | 0.7359 | fail | 107.2 | 1 | 0.1088 | baseline-native higher by 0.0256 (one trial) |
| 2026-10-23-surface-forge-harness | handoff | forge-harness | complete | 0.7103 | fail | 75.4 | 1 | 0.1272 | baseline-native higher by 0.0256 (one trial) |
| 2026-10-23-surface-forge-harness | engineering | baseline-native | complete | 0.7949 | fail | 68.5 | 1 | 0.1245 | tie |
| 2026-10-23-surface-forge-harness | engineering | forge-harness | complete | 0.7949 | fail | 79.0 | 1 | 0.1365 | tie |
| 2026-10-23-surface-tausik-core | handoff | baseline-native | complete | 0.7359 | fail | 131.1 | 2 | 0.1079 | tie |
| 2026-10-23-surface-tausik-core | handoff | tausik-core | complete | 0.7359 | fail | 87.0 | 1 | 0.1432 | tie |
| 2026-10-23-surface-tausik-core | engineering | baseline-native | complete | 0.7949 | fail | 78.5 | 1 | 0.0865 | tie |
| 2026-10-23-surface-tausik-core | engineering | tausik-core | complete | 0.7949 | fail | 77.4 | 1 | 0.1053 | tie |
| 2026-10-23-surface-harness-engineering | handoff | baseline-native | complete | 0.7359 | fail | 64.0 | 1 | 0.1331 | tie |
| 2026-10-23-surface-harness-engineering | handoff | harness-engineering | complete | 0.7359 | fail | 69.4 | 1 | 0.1047 | tie |
| 2026-10-23-surface-harness-engineering | engineering | baseline-native | complete | 0.7949 | fail | 66.1 | 1 | 0.1229 | tie |
| 2026-10-23-surface-harness-engineering | engineering | harness-engineering | complete | 0.7949 | fail | 86.0 | 1 | 0.1309 | tie |
| 2026-10-23-rules-forge-harness | ledger | baseline-native | complete | 1.0000 | pass | 64.6 | 1 | 0.0959 | tie |
| 2026-10-23-rules-forge-harness | ledger | forge-harness | complete | 1.0000 | pass | 54.1 | 1 | 0.0917 | tie |
| 2026-10-23-rules-forge-harness | merge | baseline-native | complete | 1.0000 | pass | 49.2 | 1 | 0.0658 | tie |
| 2026-10-23-rules-forge-harness | merge | forge-harness | complete | 1.0000 | pass | 52.7 | 1 | 0.0761 | tie |
| 2026-10-23-hard-forge-harness | query | baseline-native | complete | 1.0000 | pass | 119.2 | 1 | 0.1439 | tie |
| 2026-10-23-hard-forge-harness | query | forge-harness | complete | 1.0000 | pass | 143.7 | 1 | 0.1851 | tie |
| 2026-10-23-hard-forge-harness | recur | baseline-native | complete | 1.0000 | pass | 73.3 | 1 | 0.0752 | tie |
| 2026-10-23-hard-forge-harness | recur | forge-harness | complete | 1.0000 | pass | 75.3 | 1 | 0.0915 | tie |
| 2026-10-23-sheet-one-shot-forge-harness | sheet | baseline-native | complete | 1.0000 | pass | 195.4 | 1 | 0.1635 | tie |
| 2026-10-23-sheet-one-shot-forge-harness | sheet | forge-harness | complete | 1.0000 | pass | 172.0 | 1 | 0.1916 | tie |
| 2026-10-23-sheet-agentic-forge-harness | sheet | baseline-native | complete | 1.0000 | pass | 118.2 | 1 | 0.1540 | tie |
| 2026-10-23-sheet-agentic-forge-harness | sheet | forge-harness | complete | 1.0000 | pass | 104.9 | 1 | 0.1511 | tie |
| 2026-10-23-sheet-agentic-tausik-core | sheet | baseline-native | complete | 1.0000 | pass | 121.3 | 1 | 0.1303 | tie |
| 2026-10-23-sheet-agentic-tausik-core | sheet | tausik-core | complete | 1.0000 | pass | 138.9 | 1 | 0.1617 | tie |
| 2026-10-23-sheet-agentic-harness-engineering | sheet | baseline-native | complete | 1.0000 | pass | 143.9 | 1 | 0.1468 | tie |
| 2026-10-23-sheet-agentic-harness-engineering | sheet | harness-engineering | complete | 1.0000 | pass | 166.2 | 1 | 0.1557 | tie |

```
BENCH model=gpt-6.1-sol medium standard tier, A=baseline@ac0aba4e, B=harness2@1cfe254e k=1, 1200 active s per task side
BENCH model=gpt-6.1-sol medium standard tier, A=baseline-native@ac0aba4e, B=forge-harness@47a840cd5f2627f5314e34e3d88f5d5fd867e0a4, 1200 active s per task side
BENCH model=gpt-6.1-sol medium standard tier, A=baseline-native@ac0aba4e, B=tausik-core@d7ae661ffdb15001a963260481492da2968f2bf8, 1200 active s per task side
BENCH model=gpt-6.1-sol medium standard tier, A=baseline-native@ac0aba4e, B=harness-engineering@d860b4b4729947eb815445f79ed5dacd5955a1a6, 1200 active s per task side
BENCH model=gpt-6.1-sol medium standard tier, A=baseline-native@ac0aba4e, B=forge-harness@47a840cd5f2627f5314e34e3d88f5d5fd867e0a4, 1200 active s per task side
BENCH model=gpt-6.1-sol medium standard tier, A=baseline-native@ac0aba4e, B=forge-harness@47a840cd5f2627f5314e34e3d88f5d5fd867e0a4, 1200 active s per task side
BENCH model=gpt-6.1-sol medium standard tier, A=baseline-native@ac0aba4e, B=forge-harness@47a840cd5f2627f5314e34e3d88f5d5fd867e0a4 mode=one-shot, 1200 active s per task side
BENCH model=gpt-6.1-sol medium standard tier, A=baseline-native@ac0aba4e, B=forge-harness@47a840cd5f2627f5314e34e3d88f5d5fd867e0a4 mode=agentic, 1200 active s per task side
BENCH model=gpt-6.1-sol medium standard tier, A=baseline-native@ac0aba4e, B=tausik-core@d7ae661ffdb15001a963260481492da2968f2bf8 mode=agentic, 1200 active s per task side
BENCH model=gpt-6.1-sol medium standard tier, A=baseline-native@ac0aba4e, B=harness-engineering@d860b4b4729947eb815445f79ed5dacd5955a1a6 mode=agentic, 1200 active s per task side
```
<!-- results:end -->

Trial notes and provenance are in [RESULTS.md](RESULTS.md).

## Limits and pending work

- The harness2 result is one trial with small sealed sets and a retrieval ceiling.
  There are no repeated-run confidence intervals or broad superiority claims.
- Several synthetic rules tasks saturate at 1.0 for baseline and candidates.
  Harder tasks or calibration may separate them; a queued trial is not evidence.
- Text-surface trials measure added instructions, not a candidate's complete runtime.
- Historical specifications pin old suite revisions. The renamed desktop suite
  needs a new reviewed suite pin and staged dependency metadata before a new run;
  admission refuses mismatched inputs. No desktop result is reported here.
- External pilot code, retrieval assets and offline dependencies are required for
  model runs. This repo provides the runner, specifications and saved measurements,
  not a standalone reproduction bundle.
- Sandbox and host tests provide bounded evidence. They do not prove production,
  native packaging, device, or financial readiness.

## License

[Apache License 2.0](LICENSE).
