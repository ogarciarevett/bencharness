# bencharness

The language of comparing agent harnesses on the same tasks, model and time budget.

## Language

**Harness**: The agent runner under test (it drives a model through tools). Not a test harness.
_Avoid_: framework.

**Suite**: A named set of tasks with sealed items.

**Task**: One unit of work in a suite that a harness attempts and a check scores.

**Trial**: One recorded run fixing suite, harness revisions, model settings and memory backend. Not a retry.
_Avoid_: run (a trial can hold many task runs).

**Seam**: The only path by which a non-pinned harness reaches a model: a local socket service that enforces the host rules and budget.

**Guard**: The start gate that checks host load, swap and free memory before a trial starts or pauses it.

**Sealed item**: A held-out task item the harness never sees before scoring.

**Evidence**: The recorded files (`results/trials.jsonl`, per-run result files) from which every report regenerates without calling a model.

**Host pause**: A trial paused by the guard; recorded separately from completed scores and from errors.

**Contract**: A DTO or schema: the shape of a request, response, record or message that crosses a boundary. Not a smart contract.
_Avoid_: using "contract" for an on-chain program, or for a promise of behavior (say "guarantee"). Trial specifications and result records are contracts.
