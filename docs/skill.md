# Default Coding Conventions

> Auto-synced from the default-coding skill. The skill is authoritative — this is a human-readable snapshot.

## Pre-edit

1. **Read the surrounding code** — match style, naming, indentation, import patterns.
2. **Find the test suite** — locate `tests/` or `test_*.py`. Know the test command before you touch code.
3. **Check for pre-existing failures** — run `pytest -x --deselect <known-failing> -q` first.
4. **Verify syntax** — `python3 -c "import ast; ast.parse(open('file.py').read())"` after every edit.

## Quick Reference

| Task | Command | When |
|------|---------|------|
| Full gate | `audit-test` | pre-push |
| Fix formatting | `audit-test fix` | dev |
| One module | `audit-test <module> v` | dev |
| Surgical edit | `audit-test surgeon replace file.py 15 "new"` | |
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

## Project Knowledge

### audit-code
- Test: `python3 -m pytest tests/ -q` (~38s serial)
- Self-audit: `audit-test --min`
- Install: `pip install -e . --break-system-packages`
- Push from Windows (WSL lacks GitHub creds)
- PyPI: push `v*` tag, version must match pyproject.toml + `__init__.py`

### CLI Module Flags
- Modules: `syntax` `python` `wiring` `phd` `runtime` `suite` `quality` `tests` `lint` `black` `deps`
- Shortcuts: `f`=fix `h`=high `m`=medium `v`=verbose `F`=full `d`=deps `p`=phd `w`=wiring `r`=runtime `s`=suite `q`=quality `l`=lint `b`=black

## Key Pitfalls

- **ALWAYS use surgeon for edits, never patch/write_file**
- **`os.walk(Path(...))` silent no-op** — use `os.walk(os.fspath(root))`
- **Remove ALL traces when deleting features** — grep for the name
- **Intuitive UX over flags** — `+N/-N` pattern for scan and graph
- **Auto-discovery tests** — WORD_MAP and _MODULE_SHORT are auto-tested
- **New CLI handlers need `# audit: ok` annotations** — 7 handlers annotated
- **xdist slows subprocess-bound suites** — do NOT add xdist to audit-test
