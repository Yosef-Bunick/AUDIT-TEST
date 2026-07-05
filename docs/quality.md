# Quality Audit — Reference

Answers: **"Do external tools agree?"** — formatting, linting, type-checking, CVE scanning, execution coverage, docstrings, test hygiene, mutation testing.

---

## Q0 — Syntax [HIGH]
Every `.py` file must parse cleanly. `ast.parse()` on each file.
A syntax error means the file is broken — nothing else can be trusted.

## Q1 — black formatting [MEDIUM]
`black --check .` — formatting drift. Files that `would reformat`.
In `--fix` mode: runs `black .` to auto-format.

## Q2 — ruff lint [MEDIUM/HIGH]
`ruff check .` with rules: `E, F, W, I, B, S`.
- `S*` rules → HIGH (security: bandit-equivalent checks)
- All others → MEDIUM
- Ignores: `S101, S105, S110, S112, S603, S607, B007, B023, B905, E501`

In `--fix` mode: runs `ruff check --fix` first, then re-checks remaining issues.

## Q3 — mypy [MEDIUM]
`mypy . --ignore-missing-imports --no-error-summary --follow-imports=silent`.
With `--strict-mypy`: adds `--strict`.

SKIP if mypy not installed.

## Q4 — CVE scan [HIGH]
Tries `pip-audit` first, falls back to `safety check`. Counts vulnerability signals.
SKIP if neither tool installed.

## Q5 — Per-def execution coverage [MEDIUM]
Runs the full test suite under `coverage.py`, maps executed lines onto every function/class def body via AST span analysis.

A def whose body **never executed** under the test suite is invisible to every assertion — no matter how many tests MENTION its name (the PHD T2 known limit). Q5 closes this gap with line-level evidence.

Flags defs where **every line** in the body is absent from the executed-lines set. Minimum body size: `MIN_FLAG_BODY_LINES` (default: 2 lines).

**Reuses the suite audit's coverage run** when available (one test run for both audits instead of two).

SKIP if `coverage` not installed or `--fast`.

## Q6 — Docstring coverage [MEDIUM]
Public functions and classes (not `_private`) with docstrings. Threshold: `DOC_THRESHOLD_PCT` (default: 0%).

## Q7 — Test hygiene [MEDIUM]
Static AST scan of test files for:
- `time.sleep()` in tests — flaky AND slow
- `@pytest.mark.skip` with no reason string — rots silently with no audit trail

## Q8 — Mutation testing [INFO]
`mutmut run` (opt-in via `--mutation`). The only true "do the tests DETECT bugs" measure — everything else is proxy.

Reports killed vs survived mutants. SKIP if `mutmut` not installed or `--mutation` not passed.

---

## `--fix` mode
Runs `black .` + `ruff check --fix`. Skips coverage, mypy, CVE. Implies `--fast`.

## `--fast` mode
Skips Q5 (coverage run), keeps everything else.
