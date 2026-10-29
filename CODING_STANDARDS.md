# Coding standards

Judgement rules for reviewers. Implementers do not need to read this file; reviewers apply it to every diff. Mechanical check: ruff `SIM116` configured in `pyproject.toml`; tests are run with the commands in the README.

## 1. Reuse first

Before writing new code, look in the shared reusable core (`bench/` (`safety.py`, `guard.py`, `seam.py`, `evidence.py`, `report.py`); harness and suite code calls these instead of re-implementing safety, budget or reporting logic) and reuse or extend it. Duplicating logic that already lives there is a review finding.

## 2. Branching

Skipped: the nested-ternary and `else if` rule targets TypeScript, and this repo is Python only. Ruff `SIM116` (dict lookup instead of an `if` chain on one value) is enabled in `pyproject.toml`.

## 3. Validate first, at the boundary

Parse the whole input against a schema once, at the entry point, and fail with one typed validation error. Reject a function that checks fields line by line and returns or throws a different ad hoc error for each.

## 4. Schema libraries

Python uses pydantic. This package keeps its runtime dependencies minimal (none today), so adding pydantic is a decision to record in the PR; until then validate the whole input at one boundary function with one typed error, and never scatter checks.

## 5. Glossary

`GLOSSARY.md` is required and kept current. Every domain term with a collision risk is defined there. In this codebase "contract" means a DTO or schema (a request or response shape), not a smart contract. Reviewers flag any undefined term a diff introduces.

## 6. Unit tests ship with the code

Every added piece of code ships with unit tests, using fixtures or mocks where needed. Python tests live in separate files under `tests/`, mirroring the module name.

A diff that adds logic without a unit test is a review FAIL.
