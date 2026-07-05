# Wiring Audit — Reference

Answers: **"Is it connected?"** — dead symbols, test-only code, config key drift.

---

## CHECK 1 — Dead symbols
A function/method/class flagged only if its NAME appears nowhere else in the codebase in ANY form:
- Direct call: `foo()`
- Attribute call/load: `x.foo`
- Bare reference: `foo` (callbacks, decorators, aiohttp routes)
- String literal: `"foo"` (dynamic dispatch via registries / getattr / import)

**Any appearance = alive.** A DEAD flag is high-confidence because the check is conservative — shared names produce false negatives (hidden dead code), never false positives.

Names `run`, `main`, `cli` are exempted (legitimate entry-point duplication across modules). Private names (`_private`) are skipped entirely.

## CHECK 2 — Test-only symbols
Referenced ONLY from `tests/`. The component is built and unit-tested green, but no production line ever calls it. Unit tests make these *look* covered — that's the trap.

Examples found in the ABE engine: `check_mcp_call`, `record_mcp_call`, `add_read_path`, `mark_done`, `Settings.agent_config`.

## CHECK 3 — Dead config keys
For each key in `limits.json`, `model_rules.json`, `agent_models.json`, `providers.json`, `hook_rules.json`: a consumer is a production `.py` file containing the key AS A QUOTED STRING (`"KEY"` or `'KEY'`).

Quoted matching prevents the `_MAX_FILE_READ_BYTES` contains `MAX_FILE_READ_BYTES` substring false-positive.

For `limits.json`: only the `_default_limits()` span of `settings.py` is excluded (that function mirrors the JSON — defining a default is not consuming). The rest of the file is checked normally, so genuine consumption via `Settings.agent_config()` is counted.

Dead keys are grouped by blast radius: approval gates and limits that cannot fire are a **safety hole**, not config hygiene.

## CHECK 4 — cfg-KEY flow (lowercase run-default dialect)
The engine has a second config dialect: lowercase keys read via `cfg.get("benchmark_target")`.

Two directions:
- **defined-but-never-read** → dead default (e.g. `worker_hard_role`)
- **read-but-never-defined** → hidden knob with a hardcoded fallback (e.g. `premium_exec_role`) that the settings UI and switch_rules cannot see

## CHECK 5 — Pair symmetry
Conventional pairs defined in the same module where one side has production references and the other has none:
- `record_X` / `check_X`
- `save` / `load`
- `set_X` / `get_X`
- `bind` / `unbind`
- `subscribe` / `unsubscribe`

Catches the "counter is read by the gate but nothing ever increments it" class.

## CHECK 6 — Imported-but-dead symbols
A symbol imported into a module but never referenced inside that module. Imported for side effects only, or a stale import left after refactoring.

## CHECK 7 — Shadowed config
A config file key defined identically in two files (e.g. `limits.json` and `model_rules.json`). One shadows the other — which one wins depends on load order, which is fragile.

## CHECK 8 — Transitively dead config
A config key that IS consumed, but its consumer is dead (from CHECK 1/2). The key appears wired, but the code that reads it never runs — so the config is effectively dead.

## CHECK 9 — stdout protocol
`print()` and `sys.stdout.write()` calls in production code. The engine communicates with Tauri via `__EVENT__` markers on stdout — stray prints corrupt the protocol.
