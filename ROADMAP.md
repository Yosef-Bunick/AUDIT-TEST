# ROADMAP

## PHD planned rules

| Rule | What | Severity | Value |
|------|------|----------|-------|
| F6 — async without await | `async def` with no `await` inside | MEDIUM | catches broken async refactors |
| C7 — rmtree without guard | `shutil.rmtree` without try/except | HIGH | crashes on missing dirs |
| C9 — float == | `==` on floats | MEDIUM | classic floating-point bug |
| F5 — lock ordering | two locks acquired in different order | HIGH | deadlock detector |
| T7 — mock signature mismatch | mock target vs actual function signature | MEDIUM | tests testing the wrong thing |

## `#needs fix` inventory

Grep: `grep -rn "#needs fix" src/`

Known technical debt items not yet addressed. Each is an acknowledged
gap — the audit still reports it but won't suppress it like `# audit: ok`.


## Future

- polyglot audit completion (Swift, Dart, Ruby, PHP, Zig, Lua, Elixir)
- Megalinter integration (#considering)
- HTML radial graph visualization (hub-spoke)
- Runtime audit expansion (memory, GPU, disk watchdogs)
