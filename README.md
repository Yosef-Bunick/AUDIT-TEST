# 🐇 audit-test

**One command. One policy. One fail-closed verdict.**

Polyglot code auditor — syntax, wiring, PHD static analysis, runtime checks,
test suite health, quality gates, and 13+ native linters across 9 languages.
All in one shot. Zero config needed.

<p align="center">
  <a href="#install"><img src="https://img.shields.io/badge/%F0%9F%9B%A0%EF%B8%8F-Install-f7a800" alt="Install"></a>
  <a href="#usage"><img src="https://img.shields.io/badge/%F0%9F%93%90-Usage-7dd3a8" alt="Usage"></a>
  <a href="#the-stack"><img src="https://img.shields.io/badge/%E2%9A%99%EF%B8%8F-How_It_Works-7ab3ff" alt="How It Works"></a>
  <a href="#license"><img src="https://img.shields.io/badge/%F0%9F%93%9C-CC_BY--NC--ND_4.0-d4d4d4" alt="License"></a>
</p>


## Install

```powershell
pip install audit-test
```

Three commands — all identical:

```powershell
audit-test high
audit-code high
audit-tests high
```

Or from source:

```powershell
git clone https://github.com/Yosef-Bunick/AUDIT-TEST.git
cd AUDIT-TEST
pip install -e .
```

## Usage

Bare words or flags — both work:

```powershell
audit-test                    # full audit
audit-test min                # fast: wiring + phd + quality
audit-test full               # complete: all checks + raw output
audit-test fix                # auto-format (~1s)
audit-test phd high           # PHD only, HIGH only
audit-test phd wiring medium  # PHD + wiring, HIGH+MEDIUM
audit-test -p <dir>           # audit a specific project
audit-test -s "suite lint"    # skip suite + lint
```

### Full keyword reference

All forms work — bare words, `-short`, or `--long`:

| Bare | Short | Long | Does |
|---|---|---|---|
| **Severity** |
| `high` | `-h` | `--high` | HIGH only (default) |
| `medium` | `-m` | `--medium` | HIGH + MEDIUM |
| `info` | | `--info` | HIGH + MEDIUM + INFO |
| `all` | | `--all` | all findings |
| **Modes** |
| `fix` | `-f` | `--fix` | auto-format (quality) |
| `full` | `-F` | `--full` | complete analysis |
| `fast` | | `--fast` | skip slow checks |
| `verbose` | `-v` | `--verbose` | full detail output |
| `min` | | `--min` | fast: wiring + phd + quality |
| **Options** |
| | `-p` | `--path PATH` | project directory |
| `skip` | `-s` | `--skip MODULES` | skip modules |
| | `-H` | `--help` | show help |
| **Core modules** |
| `phd` `p` | | `--phd` | PHD static audit |
| `wiring` `w` | | `--wiring` | wiring audit |
| `runtime` `r` | | `--runtime` | runtime audit |
| `suite` `s` | | `--suite` | test suite audit |
| `quality` `q` | | `--quality` | quality gates |
| `syntax` | | `--syntax` | all language syntax checks |
| `python` | | `--python` | Python syntax only |
| `tests` | | `--tests` | non-Python test suites |
| `lint` `l` | | `--lint` | ruff lint |
| `black` `b` | | `--black` | black format |
| **Security integrations** |
| `semgrep` | | `--semgrep` | semgrep (structural) |
| `bandit` | | `--bandit` | bandit (Python security) |
| **Language integrations** — SKIP if tool not installed |
| `eslint` | | `--eslint` | ESLint (JS/TS) |
| `prettier` | | `--prettier` | Prettier (JS/TS/CSS) |
| `checkstyle` | | `--checkstyle` | Checkstyle (Java) |
| `pmd` | | `--pmd` | PMD (Java) |
| `go-vet` | | `--go-vet` | go vet (Go) |
| `golangci-lint` | | `--golangci-lint` | golangci-lint (Go) |
| `clippy` | | `--clippy` | cargo clippy (Rust) |
| `rustfmt` | | `--rustfmt` | rustfmt (Rust) |
| `dotnet-format` | | `--dotnet-format` | dotnet format (C#) |
| `clang-tidy` | | `--clang-tidy` | clang-tidy (C++) |
| `cppcheck` | | `--cppcheck` | cppcheck (C++) |
| `htmlhint` | | `--htmlhint` | HTMLHint (HTML) |
| `stylelint` | | `--stylelint` | Stylelint (CSS/SCSS) |

### Quick keys

| Key | Module | Runs |
|-----|--------|------|
| `p` | phd | exception discipline, security patterns, state bugs |
| `w` | wiring | dead symbols, test-only code, config drift |
| `r` | runtime | unbounded loops, timeouts, secrets in logs |
| `s` | suite | pytest, solo reruns, HEAD diff baseline |
| `q` | quality | black, ruff, mypy, CVE, coverage |
| `l` | lint | ruff lint |
| `b` | black | black format |

**Mode shortcuts:**

| Key | Does |
|-----|------|
| `h` | HIGH severity (default) |
| `m` | HIGH + MEDIUM severity |
| `v` | verbose output |
| `f` | auto-format (~1s) |
| `F` | full analysis |
| `fast` | skip slow checks |







```powershell
audit-test phd                # PHD static audit
audit-test wiring             # wiring audit
audit-test runtime            # runtime audit
audit-test suite              # test suite audit
audit-test quality            # quality gates
audit-test syntax             # all language syntax checks
audit-test python             # Python syntax only
audit-test tests              # non-Python test suites
audit-test lint               # ruff check
audit-test black              # black format
audit-test semgrep            # semgrep security scan
audit-test bandit             # bandit security scan
audit-test lint fix           # ruff --fix
audit-test black fix          # black format
audit-test phd wiring medium  # mix any modules + severity
```

### Change gate

```powershell
audit-test gate               # judge working-tree diff vs HEAD
audit-test gate high           # block on new HIGH findings (default)
audit-test gate medium         # block on new HIGH+MEDIUM
audit-test gate fast           # skip mutation (G4)
audit-test gate -p <dir>      # gate a specific project
```

### Standalone scripts

The original audit scripts also work standalone — no pip install needed.
Copy them into any project and run directly:

```powershell
python audit_wiring.py         # dead symbols, config drift
python audit_phd.py            # exception discipline, security patterns
python audit_phd.py --min-severity=HIGH   # HIGH findings only
python audit_runtime.py        # timeouts, log hygiene, prompt contracts
python audit_suite.py          # run pytest, classify failures
python audit_quality.py        # black, ruff, mypy, CVE, coverage
python audit_gate.py           # judge working-tree diff vs HEAD
python run_all_audits.py       # orchestrate all five into one report
```

## The stack

| Audit | Question it answers |
|---|---|
| wiring | **Is it connected?** Dead symbols, test-only code, config key drift |
| phd | **Does it meet the bar?** Exception discipline, security patterns, state bugs |
| runtime | **Will it hang or crash?** Unbounded loops, missing timeouts, secrets in logs |
| suite | **Is the test suite healthy?** Runs pytest, classifies real vs pollution failures |
| quality | **External gates + execution truth.** Black, ruff, mypy, CVE scan, coverage |

## Languages

Auto-detects 9 languages (marker files or source files anywhere in the tree,
root included). Python runs the full five-audit stack. Every other language
gets a **real** syntax check plus its native test suite — and when the
required toolchain is missing, the result is an honest `SKIP` with the
install hint, never a fake pass:

| Language | Detection | Syntax check | Test suite |
|---|---|---|---|
| Python | `pyproject.toml`, `setup.py`, `*.py` | `ast.parse` per file (built-in) | pytest (via `suite` audit) |
| JS / TS | `package.json`, `*.js`, `*.ts` | `node --check`; TS via `tsc --noEmit` (TS1xxx only) | `npm test` (real script only) |
| Java | `pom.xml`, `build.gradle`, `*.java` | `javac -proc:none` (parse errors only; classpath noise not judged) | `mvn test` / `gradlew test` |
| Go | `go.mod`, `*.go` | `gofmt -l -e` (parse + format drift) | `go test ./...` |
| Rust | `Cargo.toml`, `*.rs` | `cargo check` | `cargo test` |
| C# | `*.cs` | `dotnet build` (SKIP if restore fails) | `dotnet test` |
| C / C++ | `CMakeLists.txt`, `Makefile`, `*.c(pp)` | `gcc/clang -fsyntax-only` or `cl /Zs` per unit | `ctest` (if `build/` exists) |
| HTML / CSS | `*.html`, `*.css`, `*.scss` | tag-balance / brace-balance (structural, stdlib) | — |
| SQL | `*.sql` | `sqlfluff parse` (ANSI; SKIP if not installed) | — |

Restrict detection with `[audit] languages = ["python", "go"]` in
`audit-code.toml` (empty list = auto-detect all).

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Audits completed, passed |
| 1 | Completed but blocking problems found |
| 2 | Setup or configuration error |
| 3 | Required audit or tool crashed |
| 4 | No supported language detected |

### Severity levels

Every finding has a severity: **HIGH**, **MEDIUM**, or **INFO**. Default reports
HIGH only. Use `--medium`, `--info`, or `--all` to expand. The `phd` audit
supports `--min-severity=HIGH` when run standalone.

## The gate

`audit-test gate` judges **only your working-tree diff vs HEAD**, inside a
disposable git worktree:

- **G0** syntax — changed files must parse
- **G1** static regression — no new HIGH findings vs HEAD
- **G2** suite green — full test suite passes
- **G3** execution proof — every changed def + line executes under tests
- **G4** mutation kill — injected bugs in changed lines must be caught

## Design

- **Fail-closed.** Crash, missing summary, unparseable file — all failures, never passes.
- **Name-level vs execution-level.** "Test mentions this" and "body ran" are different facts.
- **Judge the diff, not the history.** Legacy findings are baseline; only regressions fail.
- **Honest limits.** No static tool promises semantic correctness — this stack narrows the gap.

## Configuration

### `.audit-test-ignore`

Skip directories or files from all scans. Drop this file in your project root.
One pattern per line, `#` for comments. Patterns are merged with built-in defaults
(`.venv`, `node_modules`, `.git`, `__pycache__`, `dist`, `build`, etc.):

```
# .audit-test-ignore
generated/
third_party/
*.pb2.py
```

Patterns match directory/file name parts (exact match, not substring).

### `# audit: ok`

Add `# audit: ok` to the end of any line to suppress a finding on that line.
Use sparingly — every suppression is counted in the summary.

Applies to `wiring`, `phd`, and `runtime` audits. Example:

```python
except Exception:         # audit: ok  (intentional swallow — benign)
TOOL_TIMEOUT = 600        # audit: ok  (tool config, not a tuning knob)
subprocess.run(cmd)       # audit: ok  (audit tools ARE subprocess runners)
```

## Requirements

Python 3.10+, git, pytest
Optional (auto-detected): `coverage`, `black`, `ruff`, `mypy`, `pip-audit`, `mutmut`

## License

This work is licensed under the Creative Commons Attribution-NonCommercial-NoDerivatives 4.0 International License (CC BY-NC-ND 4.0).

© Yosef Bunick. All rights reserved.

You are free to:

Share — copy and redistribute the material in any medium or format

Under the following terms:

Attribution — You must give appropriate credit, provide a link to the license, and indicate if changes were made.
NonCommercial — You may not use the material for commercial purposes.
NoDerivatives — If you remix, transform, or build upon the material, you may not distribute the modified material.

License details: https://creativecommons.org/licenses/by-nc-nd/4.0/

This license applies unless otherwise explicitly stated within specific files or directories of this repository.

For permission to monetize, distribute modified versions, remix, sublicense, or commercially use this repository, please contact the creator directly.
