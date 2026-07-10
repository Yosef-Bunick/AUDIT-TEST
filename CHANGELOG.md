# Changelog

## 0.4.0 (current)

Cumulative since 0.1.0 — see git history for per-release detail.

### Added
- Polyglot deep coverage: 63→72 tree-sitter AST rules across 9 languages
  (JS/TS, Rust, Go, Java, C#, Kotlin, Swift, PHP, C/C++), 19 syntax adapters,
  10 graph languages with cross-language subprocess/FFI edge detection
- 30 new polyglot regex rules from the external code-review checklists
  (React hooks, loose equality, Go mutex-by-value, Rust async blocking I/O,
  Java String ==, C# sync-over-async, C malloc checks, Kotlin GlobalScope,
  Swift IUO, PHP SQL interpolation) + a CSS/SCSS/LESS language spec
- Python PhD engine grown to 55 rules (new: SEC6 string-built SQL,
  SEC7 DEBUG=True in settings, B5 validation asserts, R10 double basicConfig)
- 23 linter integrations incl. opt-in MegaLinter (`--megalinter`)
- Profiler, surgeon v2 (cross-file port), focus groups, Q5 coverage cache

### Changed
- `quality.py` decomposed: Q0–Q8 sections extracted from the 550-line
  `run()` god function into per-section helpers
- Tarjan SCC replaced the recursion-limit hack in the graph module

## 0.1.0

### Added
- Real language adapters for all 9 supported languages — actual syntax checks
  (`node --check`, `gofmt -e`, `cargo check`, `javac`, `dotnet build`,
  `-fsyntax-only`/`/Zs`, stdlib tag/brace balance, `sqlfluff parse`) with
  honest `SKIP` + install hint when the toolchain is missing (never a fake PASS)
- Adapter results (per-language syntax audits + native test suites for
  non-Python languages) wired into the runner report and exit code
- Language filter from `audit-code.toml` (`[audit] languages`, empty = auto)
- `--profile` flag and `[reporting]` config defaults wired through the CLI
- Adapter contract tests (`tests/test_adapters.py`), including the
  missing-tool-must-SKIP guarantee
- `audit-code` CLI with `--min`, `--full`, `--fix`, `--path`, `--report-only`
- `audit-code gate` — per-change verification against git HEAD
- Five audit modules: wiring, phd, runtime, suite, quality
- `.audit-test-ignore` — user-configurable skip patterns
- `--min-severity=HIGH` flag on phd audit
- Structured result model (`AuditResult`, `Finding`, `Severity`)
- Auto-format via `--fix` (black + ruff)
- Test coverage (phd T1/T2/T3 satisfaction)
- Package config (`audit_config.py`, `src/audit_code/config.py`)
- Shared constants (`audit_shared.py`)
- Ruff lint integration with configurable ignore list
- Standalone script support (copy into any project, run directly)
- pip-installable via `pip install -e .`

### Added
- Verification test suite (41 tests across 5 files): CLI exit codes, config contract,
  SARIF/JUnit output shape, adapter plumbing (run_tool, source walk, TimeBudget),
  and profile wiring — encoding every manual check run after changes
- Q7 regression guard: quality audit against project with `tests/` dir catches
  hygiene-loop crashes invisible to projects without one
- Adapters now respect project `.audit-test-ignore` — `detect()` and `collect_files()`
  load project-local excludes and pass to `iter_source_files()` for walk pruning

### Fixed
- Quality Q7 hygiene loop `NameError` (`py_file` undefined in loop using `p`) —
  triggered only when `tests/` exists in the target project
- Quality `_py_files` now uses `os.walk` with dirname pruning instead of `rglob` —
  eliminates 30s+ timeouts on projects with large non-Python directories
- Quality `EXCLUDE_DIRS` expanded with common project subdirs (bunick-ai-desktop,
  logs, eval_results, golden_tasks, fixes and info, .vscode, .idea)
- Language detection missed source files at the project root (only
  subdirectories were scanned); Python detection stopped at the first
  non-venv directory regardless of match
- Adapter syntax checks returned unconditional PASS without checking anything;
  their results (and `discover()`'s) were computed and discarded
- `UnicodeEncodeError` crash printing ✓/✗ status glyphs on cp1252 Windows consoles
- Python-specific audits (wiring/phd/runtime/suite/quality) now report one
  honest SKIP row on projects with no Python instead of vacuous passes
- Wiring audit: public methods overriding an externally-defined base class
  (e.g. `HTMLParser.handle_starttag`) are no longer flagged as dead symbols
- Duplicated dead block in `quality._tool()`; config loaded but unused in `cli.py`
