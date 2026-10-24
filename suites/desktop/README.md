# Desktop task

One multi-file Tauri 2 / Rust / React queue application. `task.stage` writes the
starter, contract and one public smoke check, never `grade.py`. The trusted
scorer imports 21 sealed behavior checks only after the worker has stopped.
The cross-layer check passes a Rust list response through the TypeScript
invoke bridge/reducer and server-rendered React view. Native packaging and
visible windows are out of scope.

Dependencies are pinned and staged before the timed trial; both arms get the
same immutable inputs and offline toolchain. Rust and TypeScript compilation
and submitted-code execution must happen in the device/network-denying runner
sandbox. Scorer callbacks are trusted infrastructure, never worker callbacks.
Fixture tests: `python3 -m unittest discover -s tests -p test_desktop.py -v`.

Rollback boundary: remove this directory and `tests/test_desktop.py`; no existing
trial or schedule behavior changes. The runner and candidate adapter follow in
separate dependent PRs. This slice starts at main and ends at the sealed task
contract and callback-based scorer; it does not start model or candidate code.

```text
main -> desktop task (current) -> protected transport -> app comparison
```
