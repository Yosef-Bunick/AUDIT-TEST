# Roadmap

## Done

### CLI & UX
- [x] `pip install audit-test` — three CLI aliases: `audit-test`, `audit-test`, `audit-code`
- [x] Per-module flags: `--phd`, `--wiring`, `--runtime`, `--suite`, `--quality`, `--syntax`, `--python`, `--tests`, `--lint`, `--black`
- [x] `--skip MODULES` — exclude specific modules (space/comma delimited)
- [x] Severity: `-h/--high`, `-m/--medium`, `--info`, `--all` (mutually exclusive)
- [x] Verbosity: `-v/--verbose` — full detail output
- [x] Short flags: `-f/--fix`, `-F/--full`, `-p/--path`, `-s/--skip`, `-H/--help`
- [x] Bare words: `audit-test phd high fix` (no dashes needed)
- [x] `--fix` default: quality-only, fast mode (~1s), inline feedback (black/ruff counts)
- [x] `audit-test gate` — severity + verbose flags, diffs-only, fail-closed
- [x] `--min` mode includes PHD audit

### Core
- [x] `pyproject.toml` + `pip install -e .` + CLI entry points
- [x] `runner.py` — orchestrates all modules, mode logic, severity plumbing
- [x] `models.py` — `AuditResult`, `Finding`, `AuditStatus`, `Severity`, exit codes 0-4
- [x] `project.py` — `find_target_root()` separates package root from target root
- [x] `audit-code gate` — delegates to `audit_gate.py` via subprocess with severity/verbose
- [x] Quality audit: black + ruff + mypy + CVE + coverage + docstring + hygiene + mutation
- [x] Python adapter: standalone scripts callable standalone or via package wrappers
- [x] `audit_shared.py` + `audit_config.py` — shared constants, `.audit-test-ignore` support

### Adapters (9 languages)
- [x] Python, JS/TS, Java, Go, Rust, C#, C++, HTML/CSS, SQL
- [x] All with real `syntax_check()` — native tool or honest SKIP
- [x] `test_command()` for non-Python suites wired into runner

### Profiles & Integrations
- [x] `profiles/agent_engine/` — 4 stub checks, `--profile` flag wired
- [x] 5 integration stubs: semgrep, megalinter, codeql, secret_scan, dependency_scan
- [x] Reporting: JSON, SARIF, JUnit output

### Tests
- [x] `tests/test_cli.py` — CLI exit codes, flag parsing, report writing
- [x] `tests/test_runner.py` — run_suite, adapter loading, result aggregation
- [x] `tests/test_adapters.py` — language detection, syntax checks, test commands
- [x] `tests/test_config.py` — config contract, toml loading
- [x] `tests/test_reporting.py` — SARIF, JUnit output formats
- [x] `tests/test_base.py` — run_tool, source walk, quality tool detection
- [x] `tests/test_coverage.py` — coverage metrics
- [x] 112 tests, 0 failures

### Cleanliness
- [x] Self-audit: 0 HIGH, 0 MEDIUM, 0 INFO, 0 errors
- [x] All mypy type errors fixed
- [x] ruff F841 clean
- [x] black formatting clean
- [x] Q5 coverage: improved error messages (no false positives on parse failures)
- [x] CC BY-NC-ND 4.0 license
- [x] README — full flag reference, install instructions, gate docs

---

## Phase 1 — Package restructure

- [ ] **Import directly** — stop running standalone audit scripts via subprocess, import their functions
- [x] **Package named `audit-test`** — `pip install audit-test`, repo `AUDIT-TEST`
- [x] **CLI entry points** — `audit-test`, `audit-test`, `audit-code` all wired in pyproject.toml

---

## Phase 2 — Deeper lint integrations

- [ ] JavaScript: ESLint/Prettier
- [ ] Java: Checkstyle/PMD
- [ ] Go: `go vet` / `golangci-lint`
- [ ] Rust: `cargo clippy` / `rustfmt`
- [ ] C#: `dotnet format`
- [ ] C++: `clang-tidy` / `cppcheck`
- [ ] HTML/CSS: HTMLHint/Stylelint

---

## Phase 3 — Fill in profiles

- [ ] Agent Engine profile: extract ABE-specific checks from standalone scripts
- [ ] Config-key flow validation
- [ ] Prompt contract validation
- [ ] Tool registry parity checks

---

## Phase 4 — Fill in integrations

- [ ] Semgrep: JSON output parser + `Finding` conversion
- [ ] MegaLinter: comprehensive lint orchestration
- [ ] CodeQL: security query runner
- [ ] Secret scan: truffleHog / gitleaks
- [ ] Dependency scan: OWASP / Snyk

---

## Phase 5 — Polish

- [ ] Quality.py `run()` decomposition — last DG1 god function (`#needs fix` at quality.py:145)
- [ ] `sys.setrecursionlimit(10000)` → iterative Tarjan SCC (`#needs fix` at audit_phd.py:1066)
- [ ] `MAX_PER_FILE_CHECKS` duplicated in adapters/base.py — import from config.py instead (`#needs fix` at adapters/base.py:24)
- [ ] Console reporter — extract from runner.py into `reporting/console.py`
- [ ] Multi-language smoke test — mixed Python/JS project
- [ ] Parallel audit execution
- [ ] `tests/test_gate.py` — G0-G4 gate logic (offline/mocked)
- [ ] `tests/fixtures/` — sample projects for integration testing

---

## Known issues (`#needs fix`)

These are legitimate findings the audit reports that aren't suppressed — they need real code changes, not bandaids. Grep `#needs fix` in the codebase for the current list.

| File | Line | Issue | Fix |
|------|------|-------|-----|
| `quality.py` | 145 | God function 550+ lines | Decompose into sub-audits (Q0-Q8) |
| `audit_phd.py` | 1066 | `sys.setrecursionlimit(10000)` | Iterative Tarjan SCC or Kosaraju |
| `adapters/base.py` | 24 | Duplicated `MAX_PER_FILE_CHECKS` | `from audit_code.config import ...` |

Unlike `# audit: ok` (which silences the finding), `#needs fix` leaves the finding live — the audit still reports it. The annotation is for humans to know the issue is acknowledged and needs real remediation.
