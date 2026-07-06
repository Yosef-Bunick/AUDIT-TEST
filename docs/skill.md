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
- **Wire from the right place** — don't add dead helpers. If you define a function, call it from the correct call site. Self-audit catches dead code fast.
- **Patch large files cautiously** — files >500 lines corrupt under repeated `patch()`. Use `write_file` for full rewrites.

## Post-edit

1. **Parse check**: `python3 -c "import ast; ast.parse(open('file.py').read()); print('OK')\"`
2. **Run the test suite**: find the test command, run it. If any fail, check if they fail on clean `git stash` first. Fix only what YOU broke.
3. **Run audit-test**: after a fix, run the targeted module: `audit-test <module> v`. The full `audit-test` gate (~38s) is for pre-push only.

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

**Verbose always for single-module runs** — `v` shows the actual finding details so you can fix without guessing.

**Quick reference** — every command I might need:

| Task | Command | When |
|------|---------|------|
| Full gate | `audit-test` | pre-push (~38s) |
| Skip slow checks | `audit-test -s "q s" v` | dev (~2s) |
| Fix formatting only | `audit-test fix` | dev (~1s) |
| One module, verbose | `audit-test <module> v` | dev (~2-5s) |
| Surgical line edit | `audit-test surgeon replace file.py 15 "new"` | |
| Insert after line | `audit-test surgeon insert file.py 8 "import x"` | |
| Batch fixes | `audit-test surgeon batch fixes.json` | |
| Copy across files | `audit-test surgeon copy src.py 10:15 dest.py 5` | |
| Cross-file replace | `audit-test surgeon replace-cross src.py 10:15 dest.py 20:25` | |
| Port function+imports | `audit-test surgeon port src.py dest.py func` | |
| Dry-run preview | `audit-test surgeon dry-run file.py 15 "content"` | |
| Context scanner | `audit-test scan file.py 42 +5 -2` | read lines around a finding |
| Context (JSON) | `audit-test scan file.py 42 --json` | machine-readable for agents |
| Dependency scanner | `audit-test deps` | auto-update .requirements |
| Encoding check | `audit-test encoding` or `audit-test check utf-8` | |
| Pre-commit gate | `audit-test gate` (diff vs HEAD, block on new HIGH) | pre-push |
| Strict gate | `audit-test gate medium` (block on HIGH+MEDIUM) | pre-push |
| Profile project | `audit-test profile` or `audit-test profile -p <dir>` | |
| Compare subprojects | `audit-test compare -p <root> --audit` (+wiring+phd HIGH) | |
| Dead-symbol triage | `audit-test deadcode` or `audit-test deadcode -p <dir>` | |
| Focus on files | `audit-test focus fast` (uses #only groups) | |
| Skip a module | `audit-test -s "quality"` | |
| Target project | `audit-test -p /path/to/project` | |
| Manage ignores | `audit-test ignore add/del/info` | |
| Manage focus groups | `audit-test focus add/del/info <name>` | |

**Scan syntax** — `+N` lines after, `-N` before, `--json` for machine output:

```bash
audit-test scan file.py 42           # ±3 (default)
audit-test scan file.py 42 +5        # 5 lines after
audit-test scan file.py 42 -5        # 5 lines before
audit-test scan file.py 42 +5 -2     # 5 after, 2 before
audit-test scan file.py 15:30        # exact range
audit-test scan file.py 42 --json    # {"file":..., "target":42, "lines":{...}}
```

## Project-specific knowledge

### audit-code (C:\AI\audit, /mnt/c/AI/audit)
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
- Per-module: `syntax` `python` `wiring` `phd` `runtime` `suite` `quality` `tests` `lint` `black` `deps`
- Single-letter bare words: `f`=fix, `h`=high, `m`=medium, `v`=verbose, `F`=full, `d`=deps
- Severity: `h`/`high` (default), `m`/`medium`, `info`, `all` (mutually exclusive group)
- Verbosity: `v`/`verbose` (orthogonal, shows raw audit output)
- Fix: `f`/`fix` (defaults to quality-only, implies fast mode ~1s)
- Full: `F`/`full` (complete analysis)
- Path: `-p`/`--path <dir>`
- Skip: `-s`/`--skip "module1 module2"` — skip specific modules, space or comma delimited
- Help: `-H`/`--help` (capital H; `-h` is `--high`)
- Gate: supports `high`, `medium`, `info`, `-v`/`verbose` (controls G1 static regression severity)
- Scan: `scan <file> <line> [+N] [-N] [--json]` — context extractor
- Flow: `_resolve_modules()` → `set[str]|None` → `run_suite(modules=...)` → filters `audit_modules`
- `_resolve_modules` handles `--fix` (→ `{"quality"}`), `--skip` (→ `ALL_MODULES - skip_set`), `--min` (→ fast subset), explicit module flags (→ exact set), None (→ mode logic in runner)

## Pitfalls

- **`--deselect` unreliable; prefer `--ignore`**: `pytest --deselect "path::test"` silently fails on some pytest versions. `pytest --ignore=tests/broken_file.py` always works.
- **Patch substring corruption**: `patch()` with a short `old_string` can match inside a function call, breaking syntax. Always include the full line.
- **`# audit: ok` placement**: must annotate the exact line the audit rule flags. For C2/C6 rules: the flag is on the `except`/`try` line, NOT the `pass` body line.
- **Dead helper functions**: wiring audit flags any function defined but never called. If you add a helper, wire it to a call site immediately.
- **Remove ALL traces when deleting features**: deleting a module means cleaning: the files, CLI flags, WORD_MAP entries, test imports, test functions, coverage lists, and comments referencing it. `grep -r` for the name before committing.
- **xdist slows subprocess-bound suites**: this suite is subprocess-bound. Measured: serial 38s, -n 8 55s, -n auto 72s. Do NOT add xdist.
- **Intuitive UX over flag-heavy design**: the `scan` command started with `-c N` / `-b N` / `-a N` flags — rejected for `+N`/`-N` tokens (`scan file.py 42 +5 -2`). Test syntax with the user before building.
- **Bare-word expansion must skip value-flag arguments**: don't expand words following `--skip`, `--path`, `-p`, `-s`, etc.
- **Module shortcuts inside `--skip` values**: expand `q`→`quality`, `w`→`wiring`, etc. inside `_resolve_modules()`.
- **New CLI handlers need `# audit: ok` annotations**: handlers are: `_handle_focus`, `_handle_ignore`, `_handle_profile`, `_handle_compare`, `_handle_deadcode`, `_handle_scan`.
- **Auto-discovery tests for CLI keywords**: `test_cli_combinatorics.py` has `test_word_map_complete()` and `test_skip_shortcuts()` that parse `WORD_MAP` and `_MODULE_SHORT` from source at test time. Any new entry is automatically tested.
- **Security tool integration pattern**: follow semgrep.py template. Wire into: ALL_MODULES, build_audit_parser(), WORD_MAP, runner's all_audits, _run_one_module().
- **ALWAYS use surgeon for file edits. NEVER use patch/write_file for code changes.** Surgeon is faster, never fails on escaping, and auto-formats.

### Surgeon commands

```bash
audit-test surgeon replace file.py 170 "except (AttributeError, OSError):"
audit-test surgeon insert file.py 8 "import shutil"
audit-test surgeon dry-run file.py 170 "preview content"
audit-test surgeon batch fixes.json
audit-test surgeon copy src.py 10:15 dest.py 5
audit-test surgeon replace-cross src.py 10:15 dest.py 20:25
audit-test surgeon port src.py dest.py func
```

### Standalone scripts

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
