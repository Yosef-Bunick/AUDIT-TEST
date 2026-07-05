# Suite Audit — Reference

Answers: **"Is the test suite itself healthy?"** — runs pytest and diagnoses failures, not just counts them.

---

## S1 — Failed/errored tests, classified [HIGH]
Every failing test is re-run solo (`pytest test_file.py::test_name -x`). The verdict is classified:

| Outcome | Classification | Meaning |
|---------|---------------|---------|
| Fails alone | **real** | Genuine engine/test bug |
| Passes alone | **pollution** | Another test leaks state into it (shared DB, module global, env). Fix the ISOLATION, not the test body |

Solo re-runs are capped at `MAX_SOLO_RERUNS` (default: 10) with a timeout of `SOLO_TIMEOUT` seconds each.

## S2 — Suite verdict missing [HIGH]
pytest ran but the terminal summary line (`N passed[, M failed]`) never appeared. Either:
- A plugin crashed mid-report (e.g. rich `MarkupError` from bracketed paths in skip reasons)
- pytest died with exit code >= 2

A green-LOOKING run with no verdict proves nothing.

## S3 — Collection errors [MEDIUM]
Files pytest could not even import. The test file might as well not exist — it's not guarding anything.

## S4 — Import-drift skips [MEDIUM]
Skip reasons matching patterns like `"module 'X' unavailable"` or `"No module named"`. The test file's import path predates a restructure — those tests silently stopped guarding anything.

Example: `_imp("checkpoint")` → `_imp("memory.checkpoint")` — the old import path stopped working after a rename, but the skip message hid it.

## S5 — Skip inventory [INFO]
All skips grouped by reason. Environment-dependent skips (`"firejail not installed"`, `LIVE_ENGINE_TESTS` gates) are expected — this is the audit trail proving they ARE those and not S4 import-drift skips.

---

## Baseline mode (`--baseline`)

Runs the suite in a throwaway git worktree at HEAD and diffs:

| Outcome | Severity | Meaning |
|---------|----------|---------|
| Fails here, passes at HEAD | HIGH | Regression YOU introduced |
| Fails in both | (under S1) | Pre-existing |
| Passes here, fails at HEAD | INFO | You fixed it |

Doubles runtime. Use when the working tree is dirty and you need to know what YOUR changes broke.
