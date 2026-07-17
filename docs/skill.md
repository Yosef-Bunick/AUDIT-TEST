# Default Coding Conventions

> Auto-synced from the default-coding skill. The skill is authoritative — this is a human-readable snapshot.

## Pre-edit

1. **Read the surrounding code** — match style, naming, indentation, import patterns.
2. **Find the test suite** — locate `tests/` or `test_*.py`. Know the test command before you touch code.
3. **Check for pre-existing failures** — run `pytest -x -q -p no:logfire --deselect <known-failing>` first.
4. **Verify syntax** — `python3 -c "import ast; ast.parse(open('file.py').read())"` after every edit.

## Quick Reference

| Task | Command | When |
|------|---------|------|
| Full gate | `audit-test` | pre-push |
| Fast local gate | `audit-test min` (wiring + phd + quality + deps) | dev |
| Change gate vs HEAD | `audit-test gate` (`gate fast` skips mutation) | pre-commit |
| Fix formatting | `audit-test fix` | dev |
| One module | `audit-test <module> v` | dev |
| Machine-readable report | `audit-test --json out.json` / `--sarif out.sarif` / `--junit out.xml` | CI |
| Encoding check | `audit-test check utf-8` | |
| Surgical edit | `audit-test surgeon replace file.py 15 "new"` (or `15:20`) | |
| Preview edit | `audit-test surgeon dry-run file.py 15:20 "new"` | |
| Insert | `audit-test surgeon insert file.py 8 "import x"` | |
| Batch fixes | `audit-test surgeon batch fixes.json` | |
| Copy across files | `audit-test surgeon copy src.py 10:15 dest.py 5` | |
| Cross-file replace | `audit-test surgeon replace-cross src.py 10:15 dest.py 20:25` | |
| Port function | `audit-test surgeon port src.py dest.py func` | |
| Context scanner | `audit-test scan file.py 42 +5 -2` | |
| Context JSON | `audit-test scan file.py 42 --json` | |
| Dependency scanner | `audit-test deps` | |
| Dependency graph | `audit-test graph cli.py +2 -1` | |
| Graph JSON | `audit-test graph cli.py +2 -1 --json` | |
| Profile project | `audit-test profile` | |
| Compare projects | `audit-test compare -p <root> --audit` | |
| Dead-symbol triage | `audit-test deadcode` | |
| Focus group | `audit-test focus add fast a.py b.py` → `audit-test focus fast` | |
| Skip patterns | `audit-test ignore add generated/` | |
| MegaLinter sweep | `audit-test megalinter` (opt-in, slow) | |

## Scan Syntax

```bash
audit-test scan file.py 42           # ±3 (default)
audit-test scan file.py 42 +5        # 5 lines after
audit-test scan file.py 42 -5        # 5 lines before
audit-test scan file.py 42 +5 -2     # 5 after, 2 before
audit-test scan file.py 15:30        # exact range
audit-test scan file.py 42 --json    # machine-readable
```

## Graph Syntax

```bash
audit-test graph cli.py              # ±2 (default)
audit-test graph cli.py +5            # 5 downstream
audit-test graph cli.py -3            # 3 upstream
audit-test graph cli.py +5 -3         # both directions
audit-test graph cli.py +5 -3 --json  # machine-readable
```

Graph walks 10 languages (Python, JS/TS, Rust, Go, Java, Kotlin, Swift, PHP,
C#, C/C++) and reports cross-language edges (subprocess calls, FFI bindings).

## Project Knowledge

### audit-code
- Test: `python -m pytest -q -p no:logfire` (~725 tests; the broken logfire
  plugin blocks collection without that flag)
- Self-audit (finish every change with it):
  `python -m black … && python -m ruff check … && python -m pytest -q -p no:logfire && python -m audit_code --phd --wiring --quality --path src/audit_code`
  — wiring/phd must be clean, 0 HIGH
- Install: `pip install -e . --break-system-packages`
- Push from Windows (WSL lacks GitHub creds)
- PyPI: push `v*` tag, version must match pyproject.toml + `__init__.py`

### Coverage (v0.4.0)
- 21 languages / 19 adapters; Python runs the full five-audit stack
- Python phd: 55 `ast` rules; polyglot: 76 phd + 32 runtime regex rules
- Deep tree-sitter AST pass (72 rules) for JS/TS, Rust, Go, Java, C#, Kotlin,
  Swift, PHP, C/C++ — grammars are core deps in pyproject.toml
- 23 native linters; the 10 newer ones (phpstan, rubocop, swiftlint, detekt,
  dart-analyze, scalafix, credo, zig-fmt, luacheck, hlint) have **no CLI
  flag** — they auto-dispatch per detected language

### CLI Module Flags
- Modules: `syntax` `python` `encoding` `wiring` `phd` `runtime` `suite`
  `quality` `tests` `lint` `black` `deps` `semgrep` `bandit` `megalinter`
- Shortcuts: `f`=fix `h`=high `m`=medium `v`=verbose `F`=full `d`=deps `p`=phd
  `w`=wiring `r`=runtime `s`=suite `q`=quality `l`=lint `b`=black
- Modes: `min` `fast` `strict` (default) `report` (=`--report-only`, exit 0)
- Reports: `--json FILE` `--sarif FILE` `--junit FILE`; also `--profile NAME`
  `--config FILE`

## Key Pitfalls

- **ALWAYS use surgeon for edits, never patch/write_file**
- **pytest without `-p no:logfire` fails at collection** — broken plugin
- **`os.walk(Path(...))` silent no-op** — use `os.walk(os.fspath(root))`
- **File discovery** — `rglob("*.py")` + `should_audit()` + `SKIP_PARTS` from
  `audit_shared.py`; never hand-roll a skip list
- **Encoding** — read via `configured_encoding(root)` + `errors="replace"`
- **No domain hardcoding** — vocab lives in `audit-code.toml` (`[profile]`)
- **Remove ALL traces when deleting features** — grep for the name
- **Intuitive UX over flags** — `+N/-N` pattern for scan and graph
- **Auto-discovery tests** — WORD_MAP and _MODULE_SHORT are auto-tested
- **New CLI handlers need `# audit: ok` annotations** — 10 handlers annotated
- **Wiring honors `# audit: ok`** — on the `def`/`class` line, or the closing
  `):` line of a multi-line signature
- **Missing tree-sitter grammar → explicit SKIP note; broken pack → raise** —
  never silently degrade; follow this pattern when adding AST languages
- **xdist slows subprocess-bound suites** — do NOT add xdist to audit-test
