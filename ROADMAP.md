# Roadmap

## Done

- [x] `pyproject.toml` + `pip install -e .` + `audit-code` CLI entry point
- [x] `cli.py` — argparse: `--min`, `--full`, `--fix`, `--path`, `--report-only`, `--help`, `gate`, `--json`, `--sarif`, `--junit`, `--profile`, `--config`
- [x] `runner.py` — orchestrates wiring/phd/runtime/suite/quality, loads adapters at startup
- [x] `models.py` — `AuditResult`, `Finding`, `AuditStatus`, `Severity`, exit codes 0-4
- [x] `project.py` — `find_target_root()` separates package root from target root
- [x] `audit-code gate` — delegates to `audit_gate.py` via subprocess
- [x] `audit-code --fix` — auto-formats with black + ruff
- [x] `--min-severity=HIGH` flag on phd audit + docs in README
- [x] `.audit-test-ignore` — user-configurable skip patterns, merged with built-in defaults
- [x] `audit_shared.py` — single source of truth for SKIP_PARTS + EXCLUDE_DIRS, reads `.audit-test-ignore`
- [x] `audit_config.py` + `src/audit_code/config.py` — shared constants + TOML reader, no hardcoded tuning knobs
- [x] Python adapter: existing audit scripts callable standalone or via package
- [x] `tests/` — coverage tests satisfying phd T1/T2/T3
- [x] Quality audit runs black + ruff (with smart ignore list)
- [x] ruff clean on all code
- [x] Self-audit at 0 HIGH, 0 MEDIUM, 0 INFO, 0 errors, 0 `# noqa` band-aids
- [x] **Adapters** — 9 languages: Python, JS/TS, Java, Go, Rust, C#, C++, HTML/CSS, SQL
  - [x] All with working `detect()` (marker files OR source files anywhere, root included; build dirs pruned)
  - [x] All with REAL `syntax_check()` — native tool, or honest SKIP when missing (see Phase 2)
  - [x] `test_command()` for Python, JS, Go, Rust, Java, C#, C++ — non-Python suites executed by the runner
  - [x] Adapter results wired into the runner report, exit code, and JSON/SARIF/JUnit output
  - [x] Wiring audit sees all adapter methods as live (real calls, no noqa)
- [x] **Profiles** — `profiles/agent_engine/` with 4 stub checks
  - [x] `profile.py`, `config_checks.py`, `prompt_checks.py`, `tool_registry_checks.py`, `stdout_checks.py`
  - [x] `--profile agent-engine` flag + `profiles.load()` wired
- [x] **Integrations** — 5 tool stubs: semgrep, megalinter, codeql, secret_scan, dependency_scan
  - [x] All with `run()` returning `AuditResult`, detect missing tools, distinguish SKIP vs ERROR
- [x] **Reporting** — `json_report.py`, `sarif.py`, `junit.py` + `--json`, `--sarif`, `--junit` flags
- [x] **Exit codes** — 0=PASS, 1=FAIL, 2=SETUP, 3=CRASH, 4=NO_ADAPTER
- [x] **Examples** — `audit-code.toml`, `github-actions.yml`, `agent-engine-config.toml`
- [x] **LICENSE** (MIT) + **CHANGELOG.md**
- [x] **Decomposition** — `audit_gate.py main()`, `audit_suite.py main()`, `suite.py run()` all under 120 lines
- [x] **D1 duplicate detection** — only flags identical bodies, skips `run`/`main`
- [x] **Verification tests** — every manual check from the adapter overhaul encoded as automated tests (the repo passes its own suite + self-audit):
  - [x] `tests/test_adapters.py` — detection (root-level + subdir files, excluded dirs), the anti-bandaid guarantee (source files + missing toolchain ⇒ SKIP, never PASS), real syntax checks (Python/HTML/CSS always; node/gofmt/javac-backed when installed), `test_command()` honesty (npm placeholder script rejected, bare `.rs` SKIP reason)
  - [x] `tests/test_runner.py` — end-to-end `run_suite` on broken vs clean multi-language projects (FAIL/WARN rows + fail-closed propagation)
  - [x] `tests/test_runner.py` — `[audit] languages` config filter restricts adapters and skips the Python stack
  - [x] `tests/test_runner.py` — python-audits SKIP row on Python-less projects (no vacuous passes)
  - [x] `tests/test_runner.py` — native test-suite rows: PASS/FAIL propagation + missing-runner SKIP
  - [x] `tests/test_runner.py` — JSON report carries per-language adapter findings (id/status/file/line/severity/language)
  - [x] `tests/test_runner.py` — cp1252 Windows console: `_force_utf8_output()` makes ✓/✗ glyphs printable
  - [x] `tests/test_runner.py` — wiring heuristic: framework callbacks (external base class, e.g. `HTMLParser.handle_*`) are wired, private/local-base methods stay eligible for dead-symbol flagging
  - [x] `tests/test_runner.py` — profile wiring: `--profile agent-engine` adds its row; an unknown profile fails closed with an ERROR row
  - [x] `tests/test_cli.py` — CLI exit codes (broken ⇒ 1, clean ⇒ 0, `--report-only` ⇒ 0), `--json/--sarif/--junit` write files, `[reporting]` toml defaults used when flags absent (flag wins), gate-mode argv detection (with the documented `--path gate` quirk as xfail), `find_target_root` exits 2 on missing/non-dir paths
  - [x] `tests/test_config.py` — config contract: defaults without toml, deep merge preserving sibling/untouched keys, malformed toml falls back (never crashes), explicit `--config` path wins, loads are isolated deep copies; `AuditResult` severity counting + `is_failure` truth table
  - [x] `tests/test_reporting.py` — SARIF severity mapping (HIGH→error/MEDIUM→warning/INFO→note), forward-slash URIs, region lines, no bogus locations; JUnit XML parses with correct failure/skip counts, `<failure>` only on HIGH/MEDIUM
  - [x] `tests/test_base.py` — `run_tool` never raises (timeout ⇒ -1 and actually kills, unlaunchable ⇒ -2), source walk prunes build dirs (`node_modules`/`target`/`bin`/`obj`/`vendor`) while including root-level files, `TimeBudget`, `rel()` outside-root fallback, `quality._tool` prefers `python -m` for installed packages and returns None when missing everywhere
  - [x] `tests/test_base.py` — quality audit on a project WITH a `tests/` dir: Q7 hygiene walk runs without crashing and flags `time.sleep()` + reason-less `skip()` (regression guard — a `NameError` in that loop was invisible to projects without `tests/` and was caught by the self-audit, not the suite)

---

## Phase 1 — Package restructure

- [ ] **Move audit scripts into `src/audit_code/audits/`** — stop running via subprocess, import directly
- [ ] **Rename package**: `audit_code` → `audit_testing_tests` to match repo name
- [ ] **Update `pyproject.toml`** entry point to match new package name

---

## Phase 2 — Fill in adapters

Done (real syntax checks + native test suites, wired into the runner):

- [x] Shared adapter base — detection incl. root-level files, honest SKIP when toolchain missing (never fake PASS)
- [x] JavaScript/TS: `node --check` + `tsc --noEmit` (TS1xxx) + `npm test`
- [x] Java: `javac -proc:none` parse-error whitelist + `mvn`/`gradlew test`
- [x] Go: `gofmt -l -e` + `go test ./...`
- [x] Rust: `cargo check` + `cargo test`
- [x] C#: `dotnet build` + `dotnet test`
- [x] C++: `gcc/clang -fsyntax-only` / `cl /Zs` per translation unit + `ctest`
- [x] HTML/CSS: stdlib tag-balance / brace-balance structural checks
- [x] SQL: `sqlfluff parse` (ANSI)

Deeper lint integrations still open:

- [ ] JavaScript: ESLint/Prettier integration
- [ ] Java: Checkstyle/PMD integration
- [ ] Go: `go vet` / `golangci-lint` integration
- [ ] Rust: `cargo clippy` / `rustfmt` integration
- [ ] C#: `dotnet format` integration
- [ ] C++: `clang-tidy` / `cppcheck` integration
- [ ] HTML/CSS: HTMLHint/Stylelint integration

---

## Phase 3 — Fill in profiles

- [ ] Agent Engine profile: extract ABE-specific checks from `audit_wiring.py` / `audit_phd.py`
- [ ] Config-key flow validation
- [ ] Prompt contract validation
- [ ] Tool registry parity checks

---

## Phase 4 — Fill in integrations

- [ ] Semgrep: JSON output parser + `Finding` conversion
- [ ] MegaLinter: comprehensive lint orchestration
- [ ] CodeQL: security query runner
- [ ] Secret scan: truffleHog / gitleaks integration
- [ ] Dependency scan: OWASP / Snyk integration

---

## Phase 5 — Tests

- [ ] `tests/test_cli.py` — arg parsing, gate subcommand, path resolution
- [ ] `tests/test_runner.py` — mode selection, adapter loading, result aggregation
- [ ] `tests/test_project_detection.py` — `find_target_root()` edge cases
- [ ] `tests/test_python_adapter.py` — adapter wiring, audit dispatch
- [ ] `tests/test_gate.py` — G0-G4 gate logic (offline/mocked)
- [ ] `tests/test_reporting.py` — JSON/SARIF/JUnit output formats
- [ ] `tests/fixtures/` — sample projects for integration testing

---

## Phase 6 — Polish

- [ ] **Quality.py `run()` decomposition** — last DG1 god function (537 lines)
- [ ] **Console reporter** — extract from runner.py into `reporting/console.py`
- [ ] **Multi-language smoke test** — point at a mixed Python/JS project
- [ ] **Performance** — parallel audit execution where possible
- [ ] **Release to PyPI** — `pip install audit-testing-tests`
