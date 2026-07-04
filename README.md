# audit-code

**One command. One policy. One report. One fail-closed verdict.**

Interrogates the quality of a repo's code *and* its tests — answers the hard
question: **does the code work, do the tests prove it, and did my change
break anything?**

## Install

```powershell
pip install audit-code
```

Or from source:

```powershell
git clone https://github.com/Yosef-Bunick/AUDIT_TESTING_TESTS-CODE.git
cd AUDIT_TESTING_TESTS-CODE
pip install -e .
```

## Usage

```powershell
audit-code                    # full audit on current directory
audit-code --min              # fast: wiring + phd + quality (seconds)
audit-code --full             # complete: all checks + raw output
audit-code --fix              # auto-format: black + ruff --fix (~1s)
audit-code --path <dir>       # audit a specific project
audit-code --report-only      # print findings, always exit 0
audit-code --json results.json   # write JSON report
audit-code --sarif results.sarif # write SARIF (GitHub code scanning)
audit-code --junit results.xml   # write JUnit (CI dashboards)
audit-code --profile agent-engine  # enable Agent Engine profile
audit-code --config audit-code.toml  # use custom config file
audit-code --help             # show all options
```

Aliases: `audit-test` and `audit-tests` work identically to `audit-code`.

### Severity filtering

```powershell
audit-code --high             # only HIGH severity (default)
audit-code --medium           # HIGH + MEDIUM severity
audit-code --info             # HIGH + MEDIUM + INFO
audit-code --all              # all findings (same as --info)
```

### Verbosity

```powershell
audit-code --verbose          # full detail output for every audit step
audit-code --phd --high -v    # PHD only, HIGH only, full detail
```

### Per-module selection

Any combination works:

```powershell
audit-code --phd              # PHD static audit only
audit-code --wiring           # wiring audit only
audit-code --runtime          # runtime audit only
audit-code --suite            # test suite audit only
audit-code --quality          # quality gates only
audit-code --python           # Python syntax check only
audit-code --syntax           # all language syntax checks
audit-code --tests            # non-Python test suites
audit-code --lint             # ruff check only
audit-code --lint --fix       # ruff --fix only
audit-code --black            # black --check only
audit-code --black --fix      # black format only
audit-code --phd --wiring --medium   # PHD + wiring, HIGH+MEDIUM
audit-code --suite --quality         # test suite + quality gates
```

### Change gate

```powershell
audit-code gate               # judge working-tree diff vs HEAD
audit-code gate --fast        # skip mutation (G4)
audit-code gate --kill 80     # raise mutation bar (default 60%)
audit-code gate --path <dir>  # gate a specific project
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

`audit-code gate` judges **only your working-tree diff vs HEAD**, inside a
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
