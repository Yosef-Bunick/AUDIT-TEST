# Integrations — Reference

Lightweight wrappers around external tools. Every check degrades to a SKIP when its tool is absent — never a fake pass.

---

## python-syntax
`ast.parse()` on every `.py` file. Built-in, always available. No tool install needed.

The AST parser catches parse errors only — not import errors, not type errors, not runtime bugs. It's the first gate: if a file won't parse, nothing else can be trusted.

## lint (ruff)
`ruff check .` with rules: `E, F, W, I, B, S`. Same as quality Q2 but run standalone.
SKIP if ruff not installed.

## black (formatting)
`black --check .` — formatting drift check.
In `--fix` mode: `black .` auto-formats.
SKIP if black not installed.

## semgrep
`semgrep scan --config p/python --json --quiet --metrics off` with exclude list: `.venv, venv, node_modules, .git, __pycache__, dist, build`.

Structural pattern matching — catches patterns AST-walkers miss (e.g. specific library misuse, framework anti-patterns).

SKIP if semgrep not installed.

## bandit
`bandit -r . -f json -q --severity-level medium` with exclude list: `.venv, venv, node_modules, .git, __pycache__, dist, build, tests`.

Python-specific security scanner. Uses `--severity-level medium` to skip INFO-level noise (subprocess, assert) — expected for an audit tool that IS a subprocess orchestrator.

Excludes `tests/` to avoid JUnit XML parsing false MEDIUMs.

SKIP if bandit not installed.

---

## Native language linters

Auto-detected per-language. Only run when the language is detected in the project. SKIP if the linter tool is not installed.

| Language | Linter | What it runs |
|----------|--------|-------------|
| JavaScript | eslint | `eslint .` |
| JavaScript | prettier | `prettier --check .` |
| Java | checkstyle | Checkstyle XML config |
| Java | pmd | PMD ruleset |
| Go | go-vet | `go vet ./...` |
| Go | golangci-lint | `golangci-lint run` |
| Rust | clippy | `cargo clippy` |
| Rust | rustfmt | `cargo fmt --check` |
| C# | dotnet-format | `dotnet format` |
| C/C++ | clang-tidy | `clang-tidy` per file |
| C/C++ | cppcheck | `cppcheck` per file |
| HTML | htmlhint | `htmlhint` |
| CSS/SCSS | stylelint | `stylelint` |
| SQL | sqlfluff | `sqlfluff parse` (ANSI) |

Language detection: marker files at project root OR any source file with the language's extension. Detection is gated on actual files present — `audit-test -s quality` won't fire clippy on a Python-only repo just because cargo happens to be installed on the box.
