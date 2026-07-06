---
name: default-coding
description: Default coding conventions, testing workflow, and project knowledge. Loads on any coding task — establishes the development cadence before first edit.
trigger: code, edit, fix, refactor, test, build, implement, wire, add, change, modify, write, create, patch
---

# Default coding conventions

When modifying code in any project, follow this workflow BEFORE the user has to ask.

## Pre-edit

1. **Read the surrounding code** — match style, naming, indentation, import patterns. Don't impose your own style.
2. **Find the test suite** — locate `tests/` or `test_*.py`. Know the test command before you touch code.
3. **Check for pre-existing failures** — run `pytest -x --deselect <known-failing> -q` first. If you break something, you need to know whether it was you or pre-existing.
4. **Verify syntax** — `python3 -c "import ast; ast.parse(open('file.py').read())"` after every edit.

## During edit

- **Short, atomic changes** — one logical change per patch. Don't bundle refactors with features.
- **Real fixes, not band-aids** — if a check method is missing, add it. Don't suppress the finding with `# noqa` or `# audit: ok` unless the rule genuinely doesn't apply.
- **Wire from the right place** — don't add dead helpers. If you define a function, call it from the correct call site. Self-audit catches dead code fast. Specifically: wire config reads through the settings layer (`get_settings().limits.get()`) not by importing constants directly. Wire check methods at their enforcement points in the engine loop, not somewhere unrelated. The user will notice if you wire randomly — every connection must make architectural sense.
- **Patch large files cautiously** — files >500 lines corrupt under repeated `patch()`. Use `write_file` for full rewrites, or `execute_code` with Python string manipulation for mid-sized refactors. When patching engine files (run_loop.py is 3700+ lines), always include enough context in `old_string` to guarantee uniqueness — short matches like `exec_module` will corrupt function calls.

## Post-edit

1. **Parse check**: `python3 -c "import ast; ast.parse(open('file.py').read()); print('OK')"`
2. **Run the test suite**: find the test command, run it. If any fail, check if they fail on clean `git stash` first. Fix only what YOU broke.
3. **Run audit-test**: after a fix, run the targeted module: `audit-test <module> v`. The full `audit-test` gate (~38s) is for pre-push only — don't run it on every edit.

**Targeted re-runs** — don't run the full 40s audit for a single fix. Use module keywords:

| Error in | Run |
|----------|-----|
| wiring | `audit-test wiring v` |
| phd | `audit-test phd v` |
| runtime | `audit-test runtime v` |
| suite | `audit-test suite v` |
| quality | `audit-test quality v` |
| lint/format | `audit-test lint black` |
| encoding | `audit-test encoding` |
| all at once | `audit-test` (full gate) |

**Verbose always for single-module runs** — `v` shows the actual finding details so you can fix without guessing. Only drop `v` for the full `audit-test` gate check.

**Quick reference** — every command I might need:

| Task | Command | When |
|------|---------|------|
| Full gate | `audit-test` | pre-push (~38s) |
| Skip slow checks | `audit-test -s "q s" v` | dev (~2s) |
| Fix formatting only | `audit-test fix` | dev (~1s) |
| One module, verbose | `audit-test <module> v` | dev (~2-5s) |
| Surgical line edit | `audit-test surgeon replace file.py 15 "new"` |
| Insert after line | `audit-test surgeon insert file.py 8 "import x"` |
| Batch fixes | `audit-test surgeon batch fixes.json` |
| Copy across files | `audit-test surgeon copy src.py 10:15 dest.py 5` |
| Cross-file replace | `audit-test surgeon replace-cross src.py 10:15 dest.py 20:25` |
| Port function+imports | `audit-test surgeon port src.py dest.py func` |
| Dry-run preview | `audit-test surgeon dry-run file.py 15 "content"` |
| Encoding check | `audit-test encoding` or `audit-test check utf-8` |
| Pre-commit gate | `audit-test gate` (diff vs HEAD, block on new HIGH) | pre-push |
| Strict gate | `audit-test gate medium` (block on HIGH+MEDIUM) | pre-push |
| Profile project | `audit-test profile` or `audit-test profile -p <dir>` |
| Compare subprojects | `audit-test compare -p <root> --audit` (+wiring+phd HIGH) |
| Dead-symbol triage | `audit-test deadcode` or `audit-test deadcode -p <dir>` |
| Focus on files | `audit-test focus fast` (uses #only groups) |
| Skip a module | `audit-test -s "quality"` |
| Target project | `audit-test -p /path/to/project` |
| Manage ignores | `audit-test ignore add/del/info` |
| Manage focus groups | `audit-test focus add/del/info <name>` |

**`.audit-test-ignore`** — controls what gets scanned. Supports skip patterns (one per line), `#only` focus groups, and `#encoding` directive. `audit-test ignore info` to see current patterns. Never edit this file by hand — use `audit-test ignore add/del` and `audit-test focus add/del`.

**Focus groups** — scope audits to specific files. Format in `.audit-test-ignore`:

```
#only
fast=[main.py,cli.py]
slow=[src/quality.py] /mnt/c/other  | full quality sweep
#only
```

Each line: `name=[file1,file2] [/path_override] [| description]`. `/path` overrides `#path` default. `| desc` is human-readable. Manage via CLI:

```bash
audit-test focus add fast main.py cli.py
audit-test focus path fast /mnt/c/other
audit-test focus desc fast "quick checks"
audit-test focus fast              # run audit on this group
audit-test focus fast v            # verbose
audit-test focus info              # list all groups
audit-test focus del fast cli.py   # remove file from group
audit-test focus clear fast        # delete group
```

**Multi-project / external directory scanning**: point audit-test at any directory with `--path`. To scan a specific project and ignore subdirectories:

```bash
# Scan a project, skip a specific subdir
audit-test -p "C:\Users\yosef\Documents\projects\makeup_2.1" -s "wiring phd" v
```

To add custom skip patterns for that project (persisted in its `.audit-test-ignore`):

```bash
audit-test -p "C:\Users\yosef\Documents\projects\makeup_2.1" ignore add "makeup,bunch"
audit-test -p "C:\Users\yosef\Documents\projects\makeup_2.1" ignore add "*.pb2.py"
```

Built-in defaults (`.venv`, `venv`, `__pycache__`, `.git`, `node_modules`, `dist`, `build`) are always applied — no need to add them manually. Only add project-specific skips.

**Comparing multiple projects**: run audit-test on each, compare HIGH/MEDIUM counts. The project with fewer findings is cleaner. Use `audit-test phd wiring v` for a targeted scan (~5s per project). When dependencies aren't available to run the code, benchmark via AST compile time + LOC + function/loop counts as a speed proxy. See `references/benchmark-without-deps.md`.
4. **Encoding check**: `audit-test encoding` (0.8s) — catches UTF-8 corruption from bad patches before it rots
5. **Lint**: `audit-test lint black` if not already covered by audit-test above.
5. **Update metadata**: CHANGELOG.md, ROADMAP.md if the project has them.
6. **Commit**: short, descriptive message. Format: `v<version> — <one-line summary>` if versioned, or `<area>: <what changed>`.

## Project-specific knowledge

### audit-code (C:\\AI\\audit, /mnt/c/AI/audit)
- Test: `python3 -m pytest tests/ -q -p no:logfire` (445 tests, ~38s serial)
- Self-audit: `audit-test full` — modules: encoding, python-syntax, wiring, phd, runtime, suite, quality, lint, black, semgrep, bandit
- Quick fix: `audit-test fix` (~1s, black + ruff format only)
- Surgical fix: `audit-test surgeon replace file.py 170 "new line"` — line-based edits
- Install: `pip install -e . --break-system-packages`
- Commit format: short descriptive message, no version prefix needed
- Push may need Windows terminal (WSL lacks GitHub creds)
- Aliases: `audit-test`, `audit-code`, `audit-tests` all identical
- PyPI publishing: Trusted Publishing via GitHub Actions — push `v*` tag. Tag must point to commit with matching version in `pyproject.toml` and `__init__.py`. `skip-existing: true` means wrong-version builds fail silently (workflow green, nothing lands on PyPI). If tag force-push is rejected, bump to next version.

**CLI module flags** (any combination, --flag or bare word):
- Per-module: `syntax` `python` `wiring` `phd` `runtime` `suite` `quality` `tests` `lint` `black`
- Severity: `h`/`high` (default), `m`/`medium`, `info`, `all` (mutually exclusive group)
- Verbosity: `v`/`verbose` (orthogonal, shows raw audit output)
- Fix: `f`/`fix` (defaults to quality-only, implies fast mode ~1s)
- Full: `F`/`full` (complete analysis)
- Path: `-p`/`--path <dir>`
- Skip: `-s`/`--skip "module1 module2"` — skip specific modules, space or comma delimited
- Help: `-H`/`--help` (capital H; `-h` is `--high`)
- Gate: supports `high`, `medium`, `info`, `-v`/`verbose` (controls G1 static regression severity)
- Bare words work: `audit-test phd high fix` = `audit-test --phd --high --fix`
- Single-letter bare words: `f`=fix, `h`=high, `m`=medium, `v`=verbose, `F`=full
- Flow: `_resolve_modules()` → `set[str]|None` → `run_suite(modules=...)` → filters `audit_modules`
- `_resolve_modules` handles `--fix` (→ `{"quality"}`), `--skip` (→ `ALL_MODULES - skip_set`), `--min` (→ fast subset), explicit module flags (→ exact set), None (→ mode logic in runner)
- `--lint` → runs `ruff`, `--black` → runs `black` (standalone subprocess wrappers in `_run_standalone_tool`)

**Severity pipeline**:
`-h/-m/--info/--all` (or bare words `high`/`medium`/`info`/`all`) → `_resolve_severity()` → `"HIGH"`/`"MEDIUM"`/`None` → `run_suite(severity=)` → `phd.run(severity=)` → `audit_phd.py --min-severity=X`

**Module resolution**:
`_resolve_modules()` handles priority: explicit module flags → `--fix` (→ `{"quality"}`) → `--skip` (→ `ALL_MODULES - skip_set`) → `--min` (→ fast subset) → `None` (all, mode logic in runner). PHD added to `--min` mode.

### thirdDraftAgentLoop (C:\AI\thirdDraftAgentLoop, /mnt/c/AI/thirdDraftAgentLoop)
- Test: `python3 -m pytest tests/ --ignore=tests/test_engine_full.py --ignore=tests/test_sandbox.py -q` (~60s, 959 tests)
- Known failures (pre-existing, WSL):
  - All `test_engine_full.py` router tests — `fake_http` mock broken, hits live deepseek API
  - All `test_sandbox.py` subprocess/firejail tests — `Permission denied` on WSL
- Verify pre-existing: `git stash && python3 -m pytest tests/test_sandbox.py -q`
- 4.8GB `bunick-ai-desktop/` excluded via `.audit-test-ignore`
- Audit (WSL): `timeout 90 /mnt/c/AI/audit/.venv/bin/audit-code --min --path .`
- Audit (Windows PS): `audit-code --min`, standalone phd: `python C:\AI\audit\audit_phd.py --path . --min-severity=HIGH`
- **Always use `--path .` with standalone scripts** — without it they walk up to parent directory

## Pitfalls

- **`--deselect` unreliable; prefer `--ignore`**: `pytest --deselect "path::test"` silently fails on some pytest versions. `pytest --ignore=tests/broken_file.py` always works. When skipping entire test files, use `--ignore`, not `--deselect`.
- **Patch substring corruption**: `patch()` with a short `old_string` like `exec_module` can match inside a function call like `spec.loader.exec_module(mod)`, breaking syntax. Always include the full expression with surrounding context (the entire line, or the full function call including arguments). Verify with `ast.parse()` after every patch.
- **`# audit: ok` placement**: must annotate the exact line the audit rule flags. If a function call spans multiple lines, place the comment on the same physical line as the pattern (e.g., `spec.loader.exec_module(mod)  # audit: ok`), not split across the call and its arguments. **For C2/C6 rules**: the flag is on the `except`/`try` line, NOT the `pass` body line. Re-run audit after every batch to verify the suppressed count changed.

- **Clean audit ≠ better project**: fewer findings can mean less code doing less — not better code. When comparing projects, always check feature parity, architecture modularity, and estimated performance alongside audit scores. Matrix scoring prevents the "clean but useless" trap. See `references/benchmark-without-deps.md`.
- **rglob walks into excluded dirs**: `os.walk` with `dirnames[:]` pruning is faster than `rglob` + post-filter. Quality.py and adapter walks both use os.walk now.
- **Conditional-execution blind spots**: code inside `if os.path.exists("tests/")` or `for p in (root/"tests").rglob(...)` runs only when that dir exists. Temp-project tests often miss these paths. Add explicit test coverage for guarded code.
- **Module-level function calls at import time**: functions called at `DEFAULT_VALUE = _lazy_loader()` level can trigger circular imports. Use try/except and test import in isolation.
- **Dead helper functions**: wiring audit flags any function defined but never called. If you add a helper, wire it to a call site immediately.
- **Test run time**: thirdDraftAgentLoop full suite takes ~5 minutes (982 tests). Warn the user BEFORE starting a long run. Use `--ignore` to skip known-broken files for fast verification (~60s). audit-code suite is ~38s serial (445 tests).

- **xdist slows subprocess-bound suites — measure before recommending**: pytest-xdist parallelizes by spawning workers, but when tests shell out to subprocesses (black, ruff, mypy, audit-code, worktree pytest), more workers = more nested-process contention. Measured on audit-test: serial 38s, -n 8 55s, -n auto 72s. Only recommend xdist after measuring — it's not a universal speedup.

- **Slow-marker pattern for dev loop speed**: when a test suite is dominated by a few heavy integration tests, mark them `@pytest.mark.slow` and document `pytest -m "not slow"` as the fast dev loop. Don't put `-m "not slow"` in `addopts` — the full audit (`audit-test suite`) must still run everything. Leave the default as "run all" and let devs opt into fast mode.
- **Memory full**: consolidate overlapping entries with `replace` before `add`. Remove stale entries first.
- **Bare dict access on LLM output is a crash risk**: `data["key"]` on JSON the LLM returned will KeyError on missing/malformed fields. Use `data.get("key", default)` instead. This is a real bug, not a lint nit — especially on scoring/allocation code paths.
- **Audit `# audit: ok` on multiline calls**: if a flagged pattern spans multiple lines (e.g., `exec_module(\n    mod\n)`), the comment must go on the same line as the flagged token, not split across the call. Broken placement creates SyntaxError.
- **Patch-line-doubling**: when using `patch()` to add `# audit: ok` to an `except Exception: pass` block, the `old_string` MUST include the surrounding context (lines above/below) for uniqueness. Without context, `patch()` can match the wrong `except: pass` pair in a file with multiple instances, leaving all subsequent finds unmatched — or worse, doubling the `except` line (creating `except Exception:\n        except Exception:`). After any multi-file batch patch, verify with `ast.parse()` on every file touched.

- **Batch-suppressing cosmetic findings breaks files**: G2 (module caches), D4 (hardcoded models), and other MEDIUM cosmetic rules cover 20-40+ files. Batch-patching `# audit: ok` onto every mutation site via `execute_code` loops WILL corrupt at least 30% of them — doubled lines, broken dict literals, indentation errors. These are legitimate design patterns, not bugs. Never attempt batch suppression on them. Fix only HIGH-severity rules one at a time with post-patch `ast.parse()` verification.

- `# audit: ok` line-matching: each rule reports findings on a specific line. C2 flags the `except` line, not the `pass` body. C3/C6 flag the except/return line. P1 flags the `import` line. Re-run `audit_phd.py --path . --min-severity=HIGH` after each batch and verify the `suppressed` count increased. If not, the comment is on the wrong line.

- monkeypatch.setattr target module for imports inside function bodies: when a function does `from other_module import func` inside its body, `func` is not an attribute of the calling module at monkeypatch time. Patching `"calling_module.func"` fails with AttributeError. Patch the source module instead: `monkeypatch.setattr("other_module.func", replacement)`. Same applies when the import is at module level but `setattr` is called before the import resolves — always target the defining module.

- **G2/D4/T-series are baseline**: 24 module caches + 44 model strings + 355 test-coverage findings are architectural, not bugs. Accept them — suppression is high-risk, low-value.

- **External tool failure → silent false positives**: when coverage/mypy/ruff output can't be parsed (missing file, malformed JSON, empty result), set the parsed data to `None` and skip the counting block entirely. Never let a tool failure silently flag every def/file as problematic. Example: `cov = {}` after failed `json.loads` causes `executed = {}`, which makes all 218 defs appear never-executed. Fix: `cov = None` + guard `if cov is not None:` around the counting loop. Always include a diagnostic `SKIP` line with the actual error message (OSError detail, JSONDecodeError location, empty-files message).

- **Semgrep `--config auto` is a privacy leak on private repos**: `semgrep scan --config auto` fetches rules from the Semgrep registry over the network and enables usage metrics by default. For private code, always pin to `--config p/python` and add `--metrics off`. For integrations wrapping semgrep, use `[exe, "scan", "--config", "p/python", "--json", "--quiet", "--metrics", "off", "."]` — never `--config auto`.

- **Security tool integration pattern**: when adding bandit/semgrep/vulture as integration modules, follow the semgrep.py template (89 lines): detect exe with `shutil.which()`, return SKIP if missing, run subprocess with timeout, parse JSON output into `Finding` objects with severity mapping, return `AuditResult`. Wire into: (1) `ALL_MODULES` set, (2) `build_audit_parser()` as `--toolname` flag, (3) WORD_MAP as `"toolname": "--toolname"`, (4) runner's `all_audits` list, (5) runner's `_run_one_module()` dispatch.

- **`print()` in sub-audits collides with runner progress lines**: the runner uses `print(progress, end="", flush=True)` then `print(f"\r{status_line}")` for live-updating progress. Any `print()` called inside the audit function lands on the same line as the `...` progress text. Prefix with `\n` to break to a new line first: `print(f"\n  black: {n} file(s) reformatted")`.

- **Mypy `sys.stdout.reconfigure`**: add `# type: ignore[union-attr]` — mypy sees `TextIO | Any` union and flags missing `reconfigure` attribute. Standard suppression used in suite.py, quality.py, cli.py, audit_gate.py. No functional impact, pure type-narrowing limitation.

- **Mypy type-narrowing loss in while loops**: when a variable typed as a specific AST node gets reassigned inside `while isinstance(x, ast.Something): x = x.value`, mypy loses the type — `.value` returns `expr`, so after the loop mypy sees `expr` not `ast.Name`. Fix: widen the annotation upfront — `root: ast.expr = n` — so the `isinstance(x, ast.Name)` guard after the loop passes type checking.

- **Mypy variable name shadowing in loops**: if mypy reports `line` has type `str` but `Finding.line` expects `int | None`, check for a loop variable named `line` that shadows a previously-typed variable from surrounding scope. Rename loop vars uniquely per context: `line`→`def_ln`, `line`→`h_ln`. Same pattern for `prod, tests = [], []` — split into individually-typed assignments: `prod: list[Path] = []` then `tests: list[Path] = []`.

- **Mypy untyped containers**: `changed, deleted = {}, []` → split: `changed: dict[str, set[int] | None] = {}` / `deleted: list[str] = []`. `skips = Counter()` → `skips: "Counter[str]" = Counter()`. `per_file = {}` → `per_file: dict[str, set[int]] = {}`. `lines = set()` → `lines: set[int] = set()`.

- **Mypy mixed-type list appends**: `sites.append(("kind", node))` where sites is inferred as `list[tuple[str, ast.AST]]` but each kind expects a different AST subclass — add `# type: ignore[arg-type]` per append.

- **Patch tool escape-drift**: when `patch()` fails with `Escape-drift detected` (backslash-quoting in CLI args), fall back to `sed -i` for find-and-replace. Example: `sed -i 's/old_pattern/new_pattern/g' file.py`. This is common when replacing strings that contain escaped quotes or when the tool's serialization adds spurious backslashes.

- **`re.split(r"[, ]+", s)` for flexible CLI input**: when a flag accepts a list of values (`--skip "a b"` or `--skip "a,b"`), split on both commas and spaces so users don't have to remember the delimiter. Simpler UX than forcing one format.

- **Bare-word expansion must skip value-flag arguments**: when pre-processing `sys.argv` to expand bare words into dashed flags, don't expand words that immediately follow a value-taking flag (`--skip`, `--path`, `-p`, `-s`, `--json`, `--config`, etc.). Track `prev_was_value_flag = arg in value_flags` and pass the next arg through unexpanded. Otherwise `--skip quality` becomes `--skip --quality` and argparse fails with "expected one argument". Only apply WORD_MAP expansion when the previous arg was NOT a value-taking flag.

- **Module shortcuts inside `--skip` values**: expand known shortcuts (`q`→`quality`, `w`→`wiring`, etc.) inside the `_resolve_modules()` function, not just in the bare-word preprocessor. Keep a `_MODULE_SHORT` dict and map each token through `_MODULE_SHORT.get(x, x)` so `--skip "q lint"` correctly skips quality and lint. This is separate from WORD_MAP expansion — WORD_MAP runs before argparse, `--skip` shortcuts run after.

- **Single-letter bare words for common flags**: add `"f": "--fix"`, `"h": "--high"`, `"m": "--medium"`, `"v": "--verbose"`, `"F": "--full"` to WORD_MAP so users can type `audit-test f h` instead of `audit-test -f -h`. Single-letter abbreviations for modules also useful: `"q": "--quality"`, `"w": "--wiring"`, `"p": "--phd"`, `"r": "--runtime"`, `"s": "--suite"`, `"l": "--lint"`, `"b": "--black"`.

- **`--fast` flag pipeline**: to add `--fast` for skipping slow quality checks (coverage, mutation): (1) add `--fast` to `build_audit_parser()`, (2) add `\"fast\": \"--fast\"` to WORD_MAP, (3) pass `fast=args.fast` from `run_audit` to `run_suite`, (4) accept `fast: bool = False` in `run_suite` + `_run_one_module` signatures, (5) in `_run_one_module`: `if module_name == \"quality\" and (mode == \"min\" or fast): kwargs[\"fast\"] = True`. The quality module already has a `fast: bool = False` param and skips Q5-Q8 when true. Also add `fast` to the test-helper `_args()` dict.\n\n- **Integration dispatch pattern**: when wiring 10+ external tools as audit modules, avoid per-tool if-statements in `_run_one_module()`. Create an `integrations/__init__.py` with a `INTEGRATIONS: dict[str, module]` mapping (e.g., `{"eslint": eslint, "bandit": bandit, ...}`). In the runner, dispatch with a single lookup: `if module_name in INTEGRATIONS: return INTEGRATIONS[module_name].run(target_root)`. Each integration module follows the same template — check `shutil.which(exe_name)`, return SKIP if missing, run subprocess with timeout, parse output. A shared `_tool_runner.py` with `_run_tool(exe_name, cmd, audit_id, target_root, timeout)` eliminates 95% of boilerplate (see `references/integration-pattern.md`). Add all tool names to `ALL_MODULES`, WORD_MAP, `all_audits` in runner, and as `--toolname` argparse flags using a single loop in the CLI builder.

- **Bandit exclusions for security scanners**: when wrapping bandit, always `--exclude .venv,venv,node_modules,.git,__pycache__,dist,build,tests` and `--severity-level medium` to skip INFO noise (subprocess import warnings, assert statements). Without exclusions, bandit scans installed packages in .venv, producing hundreds of false HIGH positives. Same exclusions apply to semgrep with `--exclude .venv,...`. Without exclusions, bandit scans installed packages in .venv, producing hundreds of false HIGH positives. Same exclusions apply to semgrep with `--exclude .venv,...`.\n\n- **Surgical edits** — use `audit-test surgeon replace file.py <line> "content"` for line-based changes. No text-matching fragility. For multi-file fixes: `audit-test surgeon batch fixes.json` with `[{file, start, end?, content}]`.

**ALWAYS use surgeon for file edits. NEVER use patch/write_file for code changes.**
Surgeon is faster, never fails on escaping, and auto-formats after every edit.

### Surgeon commands (use these by default)

```bash
# Replace one line
audit-test surgeon replace file.py 170 "except (AttributeError, OSError):"

# Replace a range of lines
audit-test surgeon replace file.py 145:160 "def new_func():\n    return True"

# Insert after a line (0 = top of file)
audit-test surgeon insert file.py 8 "import shutil"

# Preview without writing
audit-test surgeon dry-run file.py 170 "preview content"

# Batch-apply multiple fixes from JSON
audit-test surgeon batch fixes.json
```

**fixes.json format**:
```json
[
  {"file": "quality.py", "start": 170, "content": "except (AttributeError, OSError):"},
  {"file": "audit_gate.py", "start": 526, "content": "except (AttributeError, OSError):"},
  {"file": "main.py", "start": 15, "end": 20, "content": "def new_func():\n    return True"}
]
```
`end` is optional — omit for single-line replacement. Include for ranges (inclusive).

**After every edit**, surgeon automatically runs: black → ruff format → ruff isort.
File is guaranteed clean after the call. No separate lint step needed.

**When NOT to use surgeon**: creating brand-new files (use write_file). For everything else — replacing, inserting, batch-fixing — use surgeon.

**PHL DLL** — when writing code that uses stdlib/NumPy/Pandas/etc., query the PHL DLL first for exact signatures: `phl("python:json.load")` or `phl("pd:read_csv")`. Avoids agents guessing wrong APIs. Load `phl-reference-lookup` skill for engine list and usage.

**Standalone scripts** — run individual audits without the CLI wrapper (useful for dev/debug):

| Script | Does |
|--------|------|
| `python audit_wiring.py --dead-json dead.json` | dead symbols + structured export |
| `python audit_phd.py --min-severity=HIGH` | exception discipline, security |
| `python audit_runtime.py` | timeouts, log hygiene |
| `python audit_suite.py` | pytest, classify failures |
| `python audit_quality.py` | black, ruff, mypy, CVE, coverage |
| `python audit_gate.py` | judge working-tree diff vs HEAD |
| `python run_all_audits.py` | orchestrate all five into one report |

Always pass `--path .` with standalone scripts — without it they walk up to the parent directory.

**`# audit: ok` scope**: only phd and runtime audits support suppression. Wiring and quality do NOT — wiring reports all findings unconditionally; quality's Q5 reads `.coverage` JSON, not source files, so `# audit: ok` annotations on `def` lines have zero effect on Q5 findings. Use `#needs fix` for acknowledged debt you can't fix now — unlike `# audit: ok`, it does NOT silence the finding, just flags it for later.

**PITFALL — `# audit: ok` on Q5 findings is a no-op**: Q5 (coverage) reads `.coverage` JSON from the coverage tool, not Python source files. Adding `# audit: ok` to a `def` line does NOT suppress Q5 — it only works for phd/runtime rules that scan source. The only way to drop Q5 findings is to (a) write tests that exercise the code path, or (b) accept them as expected WARNs (CLI entry points, gate orchestrators).

**Quality Q5 coverage bug** — the `[:25]` display cap also limited `findings.append()`, silently dropping 27 of 52 findings. Fixed by separating display loop from findings loop with a `shown` counter.

**`_active_paths()` import-time caching** — the env var `AUDIT_FOCUS_GROUP` was read at module import time (before `_focus_run()` sets it). Moved into function body so it reads at call time. Root cause of focus groups being a silent no-op.
  1. **Check for name collisions FIRST** — a root `deps.py` will clobber a package `deps.py` wrapper. Rename the standalone to `audit_deps.py` before the move, and update the wrapper's `_SCRIPT` path.
  2. **Update `_SCRIPT` paths**: `Path(__file__).resolve().parent.parent.parent / "file.py"` → `Path(__file__).resolve().parent / "file.py"` (scripts are now siblings, not two levels up).
  3. **Update `ROOT` paths**: add one `.parent` per level the file moved deeper. `parent.parent` → `parent.parent.parent`.
  4. **Update cross-imports**: `from audit_shared import X` → `from package_name.audit_shared import X` (bulk sed: `s/from audit_config/from package_name.audit_config/g`).
  5. **Update hardcoded file lists**: `STATIC_AUDITS`, `AUDITS`, any list of script paths that were relative to root need `os.path.join("src", "package_name", "script.py")`.
  6. **Update test imports**: `import audit_wiring` → `from package_name import audit_wiring`.
  7. **Delete root copies**: after verifying the package copies work, `rm` the originals. Keep at most 1-2 at root for standalone entry points.
  8. **Verify**: `pip install -e .` + `pytest -q` + self-audit at 0/0/0.

- **argparse `-h` repurposing**: if you want `-h` for `--high` instead of help, use `add_help=False` on the parser and manually add `parser.add_argument("-H", "--help", action="help")`. Argparse auto-adds `-h/--help` with no way to disable just the short flag — you must disable both and rebuild.

- **New CLI handlers need `# audit: ok` annotations**: when adding a new `_handle_*()` to the `if/elif` dispatch chain in `main()`, add `# audit: ok (CLI entry point)` on the `def` line immediately. The existing handlers tell you the pattern — grep `audit: ok.*CLI entry` to confirm all are covered. Without it, Q5 flags the handler as untested (same as the others, but inconsistent noise). All 5 current handlers are annotated: `_handle_focus`, `_handle_ignore`, `_handle_profile`, `_handle_compare`, `_handle_deadcode`.

**Delegation context boilerplate** — when spawning subagents for audit-test work, always include this in the context field so they get the conventions (subagents don't auto-load skills):

> Use `audit-test surgeon replace file.py <line> "content"` for all edits. Verify with `audit-test <module> v`. Full gate: `audit-test`. Fix formatting: `audit-test fix`. Never use patch/write_file for code changes.\n\n- **Bare word CLI expansion**: pre-process `sys.argv` before argparse to convert bare words to dashed flags. Define a `WORD_MAP` dict and `_expand_bare_words()` in `main()`:
  ```python
  def _expand_bare_words():
      WORD_MAP = {"phd": "--phd", "high": "--high", "fix": "--fix", ...}
      new_argv = [sys.argv[0]]
      for arg in sys.argv[1:]:
          if arg.startswith("-") or arg == "gate":
              new_argv.append(arg)
          else:
              new_argv.append(WORD_MAP.get(arg.lower(), arg))
      sys.argv = new_argv
  ```
  Call before `parser.parse_args()`. Positional subcommands (like `gate`) must be preserved unexpanded.
