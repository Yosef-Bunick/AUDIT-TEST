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

This pulls the core Python linters automatically (**ruff, bandit, mypy, black,
coverage**) — so the Python audit works out of the box, no separate setup.

Want the heavier / niche scanners too (semgrep, pip-audit CVE scan, mutmut
mutation testing, sqlfluff)?

```powershell
pip install audit-test[all]
```

> Non-Python linters (ESLint, Prettier, clippy, go vet, clang-tidy, …) are
> external toolchains pip can't install. audit-test detects them at runtime and
> cleanly **SKIP**s any that are missing — it never fakes a pass.

Or from source:

```powershell
git clone https://github.com/Yosef-Bunick/AUDIT-TEST.git
cd AUDIT-TEST
pip install -e .          # or  pip install -e ".[all]"
```

## Usage

Three commands — all identical:

```powershell
audit-test
audit-code
audit-tests
```
Bare words or flags — both work:

```powershell
audit-test                     # full audit
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
| `d` | deps | dependency scanner |

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
| `min` | | `--min` | fast: wiring + phd + quality + deps |
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
| `encoding` | | `--encoding` | source-encoding check |
| `syntax` | | `--syntax` | all language syntax checks |
| `python` | | `--python` | Python syntax only |
| `tests` | | `--tests` | non-Python test suites |
| `lint` `l` | | `--lint` | ruff lint |
| `black` `b` | | `--black` | black format |
| `deps` `d` | | `--deps` | dependency scanner |
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

### Encoding check

Verify every text file decodes cleanly under a chosen encoding (strict — no
silent replacement). Binary files are skipped automatically.

```powershell
audit-test check utf-8         # every file must be valid UTF-8
audit-test check ascii         # pure ASCII only
audit-test check UTF-16        # names are case/space-insensitive
audit-test check GB 18030      # multi-word names → gb18030
audit-test check               # use the project's configured encoding
audit-test check utf-8 -p <dir>   # check another project
```

Set a project's expected encoding once in its `.audit-test-ignore` — then a
bare `check` (or a scan of that project) uses it:

```
#encoding utf-8
```

Precedence: explicit argument → the target's `#encoding` → `utf-8`. Exit code
is `0` when every file matches, `1` when any file doesn't.

The check also runs automatically inside a full/default audit (using the
configured encoding) — so `audit-test full` already includes it. Use the
standalone `check` command when you want to test a specific encoding on demand.

> `check utf-8` / `ascii` / `gb18030` are strict. `check utf-16` / `utf-32` are
> weaker — those decoders accept almost any even-length byte run, so they mainly
> catch truncated files rather than proving the encoding.

### Change gate

```powershell
audit-test gate               # judge working-tree diff vs HEAD
audit-test gate high           # block on new HIGH findings (default)
audit-test gate medium         # block on new HIGH+MEDIUM
audit-test gate fast           # skip mutation (G4)
audit-test gate -p <dir>      # gate a specific project
```

### Project profiling

Read and AST-parse every source file **once** to size up a project, or rank
sibling projects side by side:

```powershell
audit-test profile                    # structure/cost/features of the cwd
audit-test profile -p <dir>           # profile another project
audit-test profile --json out.json    # full profile as JSON

audit-test compare -p <root>          # rank every subproject under <root>
audit-test compare -p <root> --audit  # add wiring+phd HIGH counts (slower)
audit-test compare --skip a,b         # ignore named subdirs
```

`profile` reports structure (LOC, functions, classes, imports), parse cost,
architecture (monolith vs modular + pipeline stages), runtime/performance
signals, and — when configured — capability keyword buckets. `compare` lays
that out as one row per subproject; `--audit` adds a HIGH-findings column.

Domain vocabulary is **config-driven, never hardcoded**. With no config only
generic, language-level signals are reported. To specialise, add a `[profile]`
table to `audit-code.toml`:

```toml
[profile]
pipeline_verbs  = ["render", "apply", "process"]
heavy_libs      = ["numpy", "cv2", "torch"]
utility_markers = ["lerp", "clamp"]
capabilities    = { products = ["foo", "bar"] }
```

### Dead-symbol triage

Wiring tells you a symbol is dead; `deadcode` tells you whether it matters —
sorting each into **critical** (a pipeline/feature function that never runs),
**utility** (a harmless helper), or **other** (verify manually):

```powershell
audit-test deadcode                   # classify dead symbols in the cwd
audit-test deadcode -p <dir>          # another project
audit-test deadcode --json out.json
```

Buckets are driven by the same `[profile] pipeline_verbs` / `utility_markers`
config as profiling.

### Surgeon — surgical & cross-file edits

Precise, line-numbered edits for agent workflows — no text matching, no
escaping fragility. Every edit auto-runs black + ruff (`--no-format` to skip):

```powershell
audit-test surgeon replace <file> <start>:<end> <content>      # overwrite lines
audit-test surgeon insert <file> <line> <content>              # insert after line
audit-test surgeon dry-run <file> <start>:<end> <content>      # preview without writing
audit-test surgeon batch <fixes.json>                          # apply many fixes
audit-test surgeon copy <src> <s>:<e> <dest> <after>           # copy lines across files
audit-test surgeon replace-cross <src> <s>:<e> <dest> <s>:<e>  # overwrite dest range with src
audit-test surgeon port <src> <dest> <function>                # move a function + its imports
```

### Context scanner

Quickly grab lines around a finding for context before fixing:

```powershell
audit-test scan file.py 42               # ±3 lines around line 42
audit-test scan file.py 42 +5            # 5 lines after
audit-test scan file.py 42 -5            # 5 lines before
audit-test scan file.py 42 +5 -2         # 5 after, 2 before
audit-test scan file.py 15:30            # exact range
audit-test scan file.py 42 --json        # machine-readable output
```

### Dependency graph

Trace import relationships — same vybe as scan:

```powershell
audit-test graph cli.py              # ±2 steps (default)
audit-test graph cli.py +5            # 5 downstream (what it imports)
audit-test graph cli.py -3            # 3 upstream (what imports it)
audit-test graph cli.py +5 -3         # 5 forward, 3 back
audit-test graph cli.py +5 -3 --json  # machine-readable for agents
```

`port` finds a function in `<src>` (a file or a project directory), copies it
into `<dest>`, and brings along only the imports it actually references that
`<dest>` is missing.

### Speed

A full `audit-test` runs the test suite under coverage **once**: the `suite`
audit produces the data and quality's Q5 (per-def execution proof) reuses it —
no second run. Quality-only mode (`audit-test q v`) has no suite to share with,
so Q5 **caches the coverage** keyed by a byte-fingerprint of every source and
test file. Re-running `q` with unchanged code reuses that cache (≈20× faster);
any edit busts the fingerprint and reruns. Force a fresh run with:

```powershell
$env:AUDIT_NO_Q5_CACHE = "1"    # PowerShell — disable the Q5 coverage cache
```

Other fast paths: `audit-test fast` skips the slow checks (Q3 mypy, Q5 coverage,
mutation); `audit-test min` runs only wiring + phd + quality. Test parallelism
(`pytest -n`) is **not** used — this suite is subprocess-bound, so extra workers
run slower, not faster.

### Standalone scripts

The original audit scripts also work standalone — no pip install needed.
Copy them into any project and run directly:

```powershell
python audit_wiring.py         # dead symbols, config drift
python audit_wiring.py --dead-json dead.json   # + structured dead-symbol export
python audit_phd.py            # exception discipline, security patterns
python audit_phd.py --min-severity=HIGH   # HIGH findings only
python audit_runtime.py        # timeouts, log hygiene, prompt contracts
python audit_suite.py          # run pytest, classify failures
python audit_quality.py        # black, ruff, mypy, CVE, coverage
python audit_gate.py           # judge working-tree diff vs HEAD
python run_all_audits.py       # orchestrate all five into one report
```

## The stack

| Audit | Question it answers | Full rules |
|---|---|---|
| wiring | **Is it connected?** Dead symbols, test-only code, config key drift | [docs/wiring.md](docs/wiring.md) |
| phd | **Does it meet the bar?** Exception discipline, security patterns, state bugs | [docs/phd.md](docs/phd.md) — 37 rules |
| runtime | **Will it hang or crash?** Unbounded loops, missing timeouts, secrets in logs | [docs/runtime.md](docs/runtime.md) — 13 checks |
| suite | **Is the test suite healthy?** Runs pytest, classifies real vs pollution failures | [docs/suite.md](docs/suite.md) |
| quality | **External gates + execution truth.** Black, ruff, mypy, CVE scan, coverage | [docs/quality.md](docs/quality.md) — Q0-Q8 |
| encoding | **Is the source the right encoding?** Strict-decodes every text file (UTF-8 by default; configurable) | `#encoding` + `check` |
| integrations | **External tools.** semgrep, bandit, +14 native linters across 9 languages | [docs/integrations.md](docs/integrations.md) |

> **wiring, phd and runtime aren't Python-only.** Python gets the full
> `ast`-based rules; every other language gets a **portable, dependency-free
> subset** (dead symbols, swallowed errors, dynamic `eval`, hardcoded secrets,
> debt markers, unbounded loops) — see [Polyglot audits](#polyglot-audits).

## Languages

Auto-detects 9 languages (marker files or source files anywhere in the tree,
root included). Python runs the full five-audit stack. Every other language
gets a **real** syntax check, its native test suite, **and a portable
wiring/phd/runtime pass** — and when a required toolchain is missing, the
result is an honest `SKIP` with the install hint, never a fake pass:

| Language | Detection | Syntax check | Test suite | Semantic¹ |
|---|---|---|---|---|
| Python | `pyproject.toml`, `setup.py`, `*.py` | `ast.parse` per file (built-in) | pytest (via `suite` audit) | full `ast` |
| JS / TS | `package.json`, `*.js`, `*.ts` | `node --check`; TS via `tsc --noEmit` (TS1xxx only) | `npm test` (real script only) | ✓ |
| Java | `pom.xml`, `build.gradle`, `*.java` | `javac -proc:none` (parse errors only; classpath noise not judged) | `mvn test` / `gradlew test` | ✓ |
| Go | `go.mod`, `*.go` | `gofmt -l -e` (parse + format drift) | `go test ./...` | ✓ |
| Rust | `Cargo.toml`, `*.rs` | `cargo check` | `cargo test` | ✓ |
| C# | `*.cs` | `dotnet build` (SKIP if restore fails) | `dotnet test` | ✓ |
| C / C++ | `CMakeLists.txt`, `Makefile`, `*.c(pp)` | `gcc/clang -fsyntax-only` or `cl /Zs` per unit | `ctest` (if `build/` exists) | phd/runtime¹ |
| HTML / CSS | `*.html`, `*.css`, `*.scss` | tag-balance / brace-balance (structural, stdlib) | — | — |
| SQL | `*.sql` | `sqlfluff parse` (ANSI; SKIP if not installed) | — | — |

¹ Portable wiring/phd/runtime via the [Polyglot audits](#polyglot-audits)
engine. C/C++ gets phd + runtime but not wiring (linkage/headers make
dead-symbol detection unreliable).

Restrict detection with `[audit] languages = ["python", "go"]` in
`audit-code.toml` (empty list = auto-detect all).

### Polyglot audits

The deep audits go multi-language without a parser per language. Python keeps
its full `ast` rules; every other language gets a **dependency-free, portable
subset** driven by a per-language `LangSpec` (file extensions, how a definition
looks, which rules apply). Detection is by extension in a single tree walk, so a
language needs **no toolchain and no adapter** — pure-Kotlin or pure-Elixir
repos are scanned out of the box.

**17 languages** carry rules today:

| | Languages |
|---|---|
| Full adapter (syntax + tests + semantic) | JavaScript/TypeScript, Java, Go, Rust, C#, C/C++ |
| Semantic-only (wiring/phd/runtime) | Kotlin, Swift, Dart, Ruby, PHP, Zig, Scala, Lua, Haskell, Elixir, SQL |

What each portable audit looks for:

- **wiring** — dead symbols: a definition (function/class/method) whose name is
  never referenced anywhere else. Conservative — exported/`pub`/capitalised API
  and entry points are never flagged, so it under-reports rather than cries wolf.
  (Disabled for C/C++, Haskell and SQL, where linkage/exports make it unreliable.)
- **phd** — swallowed errors (empty `catch`), catch-all handlers, dynamic code
  execution (`eval`, `new Function`, `Code.eval`, `load`), hardcoded secrets,
  Go `if err != nil {}`, Rust `.unwrap()`/`panic!`, Swift `try!`/`fatalError`,
  Kotlin `!!`, unsafe C (`gets`/`strcpy`), Haskell `unsafePerformIO`, SQL
  `DELETE`/`UPDATE` without a `WHERE`.
- **runtime** — debug leftovers (`console.log`, `System.out.println`,
  `var_dump`, `IO.inspect`), debt markers (TODO/FIXME/XXX/HACK), unbounded loops,
  `SELECT *`.

Findings surface per language as `javascript-wiring`, `go-phd`, `rust-runtime`,
`kotlin-phd`, etc. A language with no rule set returns an honest `SKIP`. The
`[audit] languages = [...]` allow-list and `--wiring`/`--phd`/`--runtime` module
flags apply to these exactly as they do to the Python audits.

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

Declare the project's expected source encoding with `#encoding` — used by
`audit-test check` (see [Encoding check](#encoding-check)):

```
#encoding utf-8
```

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

For known issues you can't fix right now but don't want to suppress. Unlike `# audit: ok`,
this does NOT silence the finding — the annotation flags acknowledged debt.
Grep the codebase for the current list, or see [ROADMAP.md](ROADMAP.md#known-issues-needs-fix).

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

**AI Training Restriction.** No part of this work may be used as training data to train, fine-tune, or otherwise build machine learning models (including large language models, code generation models, or embeddings) without explicit written permission. This restriction applies regardless of whether the output is distributed or kept private. Using this tool to evaluate or benchmark model output is permitted.
