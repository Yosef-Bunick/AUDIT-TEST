# ROADMAP

## PHD planned rules

### Priority build order (effort ÷ value)

| # | Rule | What | Severity | LOC | Notes |
|---|------|------|----------|-----|-------|
| 1 | C7 — rmtree without guard | `shutil.rmtree` without try/except OSError | HIGH | 20 | Reuses existing `guarded_against_oserror()` helper. No tool covers this. |
| 2 | C9 — float == | `==` on floats (0.1+0.2==0.3 is False) | MEDIUM | 25 | Unique gap — no tool in the stack detects float equality. |
| 3 | T7 — mock signature mismatch | mock.patch target vs actual function signature | MEDIUM | 50 | Builds on existing T5 infrastructure. Catches tests testing wrong things. |
| 4 | F5 — lock ordering | two locks acquired in different order → deadlock | HIGH | 65 | Highest value but complex cross-function analysis. No tool covers this. |
| 5 | F6 — async without await | `async def` with no `await` inside | MEDIUM | 15 | **Skip** — ruff RUF029 already covers this. Build only if standalone `p` mode is critical. |

### Quick wins (15 LOC each)

| Rule | What | Severity | Similar to |
|------|------|----------|-----------|
| C8  | `except: continue` in loops silently discards errors | MEDIUM | C2 (except:pass handler check) |
| SEC4 | `yaml.load()` without SafeLoader | HIGH | SEC2 (eval/exec check) |
| B4  | `tempfile.mktemp()` / `os.tempnam()` race-prone | MEDIUM | SEC1 (kwarg check) |
| G3  | `__init__` returning non-None (TypeError at runtime) | HIGH | B1 (function body walk) |


## `#needs fix` inventory

Grep: `grep -rn "# needs fix" src/`

| File:Line | Reason |
|-----------|--------|
| `src/audit_code/audit_phd.py:1097` | Recursion hack — replace recursive Tarjan SCC with iterative |

## Future

- polyglot audit completion (Swift, Dart, Ruby, PHP, Zig, Lua, Elixir)
- Megalinter integration (#considering)
- HTML radial graph visualization (hub-spoke)
- Runtime audit expansion (memory, GPU, disk watchdogs)
