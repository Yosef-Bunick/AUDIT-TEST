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
audit-test                     # full audit (HIGH only)
audit-test v                   # full audit, verbose
audit-test f                   # fix: auto-format (lint+black)
audit-test min                 # min: fast wiring + phd + quality
audit-test F                   # full: checks + raw output
audit-test w r h               # wiring+runtime high only
audit-test -p <dir>            # audit a specific project
audit-test -s "s q"            # skip whats in next value "suite + quality"
```

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

### Focus groups

Save file sets in `.audit-test-ignore` and run audits against them:

```powershell
audit-test focus add fast main.py cli.py   # create group
audit-test focus info                        # list all groups
audit-test focus fast                        # audit the 'fast' group
audit-test focus fast v /mnt/c/other         # verbose, from other path
audit-test focus path fast /mnt/c/other      # set group path
audit-test focus desc fast "quick checks"    # set description
audit-test focus del fast cli.py             # remove file from group
audit-test focus clear fast                  # delete group
```

### Ignore patterns

Manage `.audit-test-ignore` skip patterns directly:

```powershell
audit-test ignore add generated/             # add skip pattern
audit-test ignore info                       # list patterns
audit-test ignore del generated/             # remove pattern
audit-test ignore clear                      # remove all custom patterns
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
One pattern per line, `#` for comments. Built-in defaults are always applied
(`.venv`, `node_modules`, `.git`, `__pycache__`, `dist`, `build`, etc.).

Use `#only` blocks to focus audits on specific files:

```
# .audit-test-ignore
generated/
third_party/
*.pb2.py

#only
fast=[src/main.py,src/cli.py]
slow=[src/quality.py] /mnt/c/other  | full sweep
#only
```

Group format: `name=[file1,file2] [/path_override] [| description]`

Patterns match directory/file name parts (exact match, not substring).

### `# audit: ok`

Add `# audit: ok` to the end of any line to suppress a finding on that line.
Use sparingly — every suppression is counted in the summary.

Applies to `phd` and `runtime` audits (the two modules that carry Sink/SUPPRESS_RE
machinery). The `wiring` audit has no suppression mechanism — it reports all
findings unconditionally.

Only suppress when the audit is **wrong**: env-var-gated code paths, CLI entry
points that can't be covered, and parse-time helpers wiring can't detect through
its import-graph walk.

### `#needs fix`

For known issues you can't fix right now but don't want to suppress permanently.
The annotation flags intent without silencing the finding:

```python
except Exception:         #needs fix (broad except — use AttributeError, OSError)
sys.setrecursionlimit(10000)  #needs fix (iterative Tarjan SCC instead of recursion hack)
```

Unlike `# audit: ok`, this does NOT suppress the finding. The audit still reports
it — the annotation is for humans (and future you) to know the issue is acknowledged
and needs real remediation, not a suppressive bandaid.

## Requirements

| Tool | Required | Used by |
|------|----------|---------|
| Python 3.10+ | ✓ | all modules |
| git | ✓ | gate, suite baseline, wiring (repo root detection) |
| pytest | ✓ | suite, quality (Q5 coverage) |
| `coverage` | — | quality Q5 (def execution proof) |
| `black` | — | quality Q1 / `fix` mode |
| `ruff` | — | quality Q2 / `lint` / `fix` mode |
| `mypy` | — | quality Q3 (type checking) |
| `pip-audit` or `safety` | — | quality Q4 (CVE scan) |
| `mutmut` | — | quality Q8 / gate G4 (mutation testing) |
| `semgrep` | — | security integration (structural) |
| `bandit` | — | security integration (Python SAST) |

**Native linters** (auto-detected per-language; honest SKIP if tool not installed):

| Language | Tools |
|----------|-------|
| JS / TS | `eslint`, `prettier` |
| Java | `checkstyle`, `pmd` |
| Go | `go vet`, `golangci-lint` |
| Rust | `cargo clippy`, `rustfmt` |
| C# | `dotnet format` |
| C / C++ | `clang-tidy`, `cppcheck` |
| HTML / CSS | `htmlhint`, `stylelint` |
| SQL | `sqlfluff` |

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
