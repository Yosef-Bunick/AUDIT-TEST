# PHD Audit — Complete Rule Reference

Every check the `audit_phd.py` static analyser performs, organized by dimension.

---

## Dimension 1 — Correctness ("no silent wrong answers")

### C1 — bare `except:` [HIGH]
Bare except catches EVERYTHING — `SystemExit`, `KeyboardInterrupt`, `MemoryError`.
```python
try:                        # C1 flags this
    do_work()
except:                     # <-- no exception type
    pass
```
**Fix**: specify the exception type(s).

### C2 — exception swallowed with `pass` [HIGH]
`except Exception: pass` or `except BaseException: pass` with nothing in the body.
```python
try:                        # C2 flags this
    sys.stdout.reconfigure(...)
except Exception:           # <-- broad + empty handler
    pass
```
**Fix**: narrow the exception type OR add logging. `except (AttributeError, OSError):` for best-effort operations; log at minimum for data-loss paths.

### C2i — best-effort logging swallowed [INFO]
Variant of C2 where the try body only contains logging/emit calls. A swallowed log flush is benign — the finding is informational. **Can suppress** with `# audit: ok` after a human review.

### C3 — silent fallback returns in except handlers [MEDIUM]
Handler catches broadly and `return`s a fallback value without logging.
```python
try:                        # C3 flags this
    return config[key]
except Exception:           # <-- broad catch
    return {}               # <-- silent fallback, never logged
```
**Fix**: log the exception before returning the fallback.

### C4 — open() resource discipline [MEDIUM]
`open()` outside a `with` block, or `open().method()` that isn't `.close()`.
```python
f = open("data.json")       # C4: open() outside `with`
data = open("x").read()     # C4: .read() without .close() — fd leak on error path
open("lock").close()        # OK: touch-file idiom (explicitly exempted)
```
**Fix**: use `with open(...) as f:`.

### C5 — unguarded TOCTOU (time-of-check-time-of-use) [MEDIUM]
`os.path.exists(x)` followed by `os.remove(x)` in the same function, with no try/except OSError guard.
```python
if os.path.exists(path):    # C5: check
    os.remove(path)         # C5: use — race window between these lines
```
**OK** if the remove is wrapped in `try: ... except OSError: pass`.
**Fix**: EAFP — remove directly inside try/except, skip the exists() check.

### C6 — LLM-parsed dicts indexed with bare `[]` [MEDIUM]
A dict produced by `json.loads()` or `parse_json()` of LLM output, then accessed with `d["key"]` instead of `d.get("key")`. A hallucinated/missing field is a mid-run KeyError.
```python
data = json.loads(llm_response)   # C6: LLM-parsed source
name = data["name"]               # C6: bare index — KeyError if "name" missing
```
**Fix**: `data.get("name")` with a safe default, or validate the schema first.

### C7 — shutil.rmtree() without try/except OSError [HIGH]
`shutil.rmtree()` not wrapped in a try/except OSError — a permission error or
locked file crashes the process mid-cleanup.
```python
shutil.rmtree(tmp_dir)          # C7: PermissionError crashes the run
```
**Fix**: wrap in `try: ... except OSError:` and log the failure.

### C8 — except: continue silently discards errors [MEDIUM]
A loop body whose exception handler is just `continue` — every failure is
thrown away without a trace.
```python
for item in items:
    try:
        process(item)
    except Exception:
        continue                # C8: error vanishes, item silently skipped
```
**Fix**: log the exception (at minimum) before continuing.

### C9 — float == comparison [MEDIUM]
Equality comparison where either side is a float literal. Floating-point
rounding makes `==` unreliable (`0.1 + 0.2 != 0.3`).
```python
if ratio == 0.3:                # C9: rounding breaks this
```
**Fix**: `math.isclose(ratio, 0.3)` or compare against a tolerance.

---

## Dimension 2 — Invariant coverage (test evidence)

### T1 — modules referenced by no test [MEDIUM]
A production `.py` file whose module name or stem string never appears in any test file — not in imports, not in string literals, not in qualified references. If no test even mentions the module, no invariant is being proven.
**Fix**: add an import in at least one test, or write a test.

### T2 — public defs referenced by no test [MEDIUM]
A public function/class whose NAME never appears in any test file. Name-level check only — a def can be "covered" by T2 even if its body never executes (Q5 catches that gap).
**Fix**: write a test that references the def.

### T3 — defs tested happy-path only [MEDIUM]
The def's name appears in tests, but no referencing test function uses `pytest.raises`, `assertRaises`, or feeds None/empty/negative/boundary input. Happy-path-only coverage: the test proves the function works when everything is perfect — not when it isn't.
**Fix**: add edge-case / failure-path test inputs.

### T4 — assertion-free tests [MEDIUM]
A test function with zero `assert` statements, `pytest.raises`, `mock.assert_*`, or same-file asserting helper calls. Passes green forever, proves nothing. Load-bearing for T2/T3 — a name reference from an assertion-free test still counts as "covered" by T2.
**Fix**: add at least one assertion.

### T5 — monkeypatch/patch targets missing from target module [MEDIUM]
`monkeypatch.setattr(module, "attr", ...)` or `mock.patch("module.attr")` targeting a name that doesn't exist at the module's top level. The patch silently does nothing — the test passes but isn't testing what you think. Resolves import aliases and `_imp()`/`import_module()` wrappers.
**Fix**: verify the attribute actually exists in the target module.

### T7 — mock.patch targets missing from the patched module [MEDIUM]
`mock.patch("module.attr")` where `module` doesn't exist in production code,
or `attr` isn't defined in that module. Complements T5: T5 resolves
monkeypatch/setattr targets, T7 resolves string-path `mock.patch` targets.
```python
@mock.patch("app.services.fetch")   # T7 if app.services has no `fetch`
def test_x(mock_fetch): ...
```
**Fix**: patch the name where it's actually looked up.

---

## Dimension 3 — Failure handling / concurrency

### F1 — locks defined but never acquired [HIGH]
`Lock()` or `RLock()` assigned to a variable, but `with lock:` or `lock.acquire()` never appears in the same file.
```python
self._lock = threading.Lock()   # F1: defined
# ... no `with self._lock:` anywhere in this file
```
**Fix**: acquire the lock in mutation methods, or delete the unused lock.

### F2 — `global` shared mutable state [MEDIUM]
`global` statement in a function body. Module-level mutable state shared across threads/imports.
```python
def update():
    global _cache              # F2: shared mutable state
    _cache["key"] = value
```
**Fix**: use thread-local storage, pass state explicitly, or accept the risk with documentation.

### F2i — lazy-init cache globals [INFO]
Variant of F2 where the global is guarded by `if X is None:` (lazy-init pattern). Idiomatic for module-level caches; review for thread safety.
**Fix**: add a thread-safety comment or use `threading.local()`.

### F3 — import-time side effects [MEDIUM]
Module-level code (outside `if __name__ == "__main__"`) that calls I/O functions: `open()`, `mkdir()`, `connect()`, `Popen()`, `subscribe()`, `read_text()`/`write_text()`, etc. Runs at import — slows startup, breaks import hooks.
```python
# module top-level (not under __main__ guard)
_data = json.loads(Path("config.json").read_text())   # F3: import-time I/O
```
**Fix**: defer to first use (lazy init), or move under `if __name__ == "__main__"`.

### F4 — bare `cfg[...]` / `environ[...]` indexing [MEDIUM]
Dict subscript access on `cfg` or `environ` without `.get()`. A missing key is a mid-run crash.
```python
timeout = cfg["timeout"]           # F4: KeyError if "timeout" missing
path = os.environ["API_KEY"]      # F4: KeyError if env var unset
```
**Fix**: `cfg.get("timeout", default)` / `os.environ.get("API_KEY")`.

### F5 — lock ordering inconsistency [MEDIUM]
Two locks acquired in the order A→B in one function and B→A in another —
the classic deadlock recipe.
```python
def f():
    with lock_a:
        with lock_b: ...        # A→B

def g():
    with lock_b:
        with lock_a: ...        # F5: B→A — potential deadlock with f()
```
**Fix**: define one global lock-acquisition order and stick to it.

---

## Dimension 4 — Design quality / drift

### D1 — duplicate function implementations [MEDIUM]

Two checks:

**D1a — same name, identical bodies.** Two functions with the same name in different files, with identical AST bodies. Copy-paste drift: one gets fixed, the other rots. Names `main` and `run` are exempted.
```python
# utils.py
def compute(x):               # D1a: identical body
    return x * 2 + 1

# helpers.py
def compute(x):               # D1a: identical body — shared code, not duplicated
    return x * 2 + 1
```

**D1b — different names, identical logic.** Two functions with different names but the same body (variable names normalized). Catches copy-paste-with-rename.
```python
# a.py
def foo(x):                   # D1b: same logic as bar()
    result = x + 1
    return result

# b.py
def bar(y):                   # D1b: same logic as foo()
    output = y + 1
    return output
```
**Fix**: extract to a shared module and import from both places.

### D2 — circular module imports [HIGH]
Module-level import cycles detected via Tarjan's SCC algorithm. A imports B, B imports A.
```python
# a.py
from b import helper          # D2: cycle

# b.py
from a import config          # D2: cycle
```
**Fix**: break the cycle — extract shared types to a third module, use lazy imports, or restructure.

### D3 — flat sys.path-dependent imports [MEDIUM]
`import foo` when `foo` is actually a submodule (`pkg.foo`). The flat import works iff the parent package is on sys.path — fragile and environment-dependent.
```python
import utils                  # D3: should be `from pkg import utils` or `import pkg.utils`
```
**Fix**: use fully-qualified dotted imports.

### D4 — hardcoded model strings [MEDIUM]
Literal model name strings (`"deepseek-v4-pro"`, `"claude-sonnet-4"`) outside config-layer files (`providers.py`, `tracker.py`, `settings.py`). Hardcoded model names across multiple files are a drift hazard — change the model in one place, forget the others.
**Fix**: reference model by config key, not by literal string.

### D5 — scattered env reads [MEDIUM]
`os.getenv()` or `os.environ[...]` outside config-layer files. Environment reads scattered across the codebase are impossible to audit — you can't answer "what env vars does this process read?" without grepping the whole repo.
**Fix**: centralize env reads in `settings.py` or a dedicated config module.

### DG1 — god functions / classes / files [MEDIUM]
- Functions >120 lines
- Classes >25 methods
- Files >900 lines

Monolithic units are hard to test, review, and reason about.
**Fix**: decompose into smaller, focused units.

---

## State & hygiene

### G1 — hardcoded tuning knobs outside config [MEDIUM]
A module-level `NAME = <number>` assignment (non-bool) outside the config
layer — a tuning knob that should live in settings.
```python
MAX_RETRIES = 7                 # G1: move to settings
```
**Fix**: move the constant to the config/settings module.

### G2 — module-level mutable state mutated from code [MEDIUM]
A module-level `dict`/`list`/`set` that code mutates (`.append`, `[k] = v`,
`.add`). Tagged `[unbounded growth?]` when nothing ever evicts from it.
```python
_CACHE = {}                     # module level

def remember(k, v):
    _CACHE[k] = v               # G2: shared mutable state, never evicted
```
**Fix**: encapsulate in a class, use an LRU cache, or document the eviction story.

### G3 — __init__ returning non-None [HIGH]
An `__init__` with a `return <value>` statement — raises `TypeError` the
moment the class is instantiated.
```python
def __init__(self):
    return self                 # G3: TypeError at instantiation
```
**Fix**: drop the return value (`return` alone is fine).

---

## Security

### SEC1 — subprocess shell=True [HIGH]
`subprocess.run(..., shell=True)` — shell injection surface. Commands are interpreted by the shell, allowing chaining (`cmd && rm -rf /`).
```python
subprocess.run(f"git log {user_input}", shell=True)   # SEC1: injection surface
```
**Fix**: pass args as a list with `shell=False`, or sanitize input.

### SEC2 — dynamic code execution [HIGH]
`eval()`, `exec()`, `exec_module()`, or `pickle.load()`. Arbitrary code execution from untrusted input.
```python
eval(user_input)               # SEC2
exec(compile(code, ...))       # SEC2
pickle.load(untrusted_file)    # SEC2
```
**Fix**: use `ast.literal_eval()` for data, `json.loads()` for structured data, never deserialize untrusted pickles.

### SEC3 — hardcoded credentials in source [HIGH]
Token-shaped literals (`sk-...`, `ghp_...`, `xoxb-...`, `AKIA...`, `AIza...`) or variable names containing `api_key`/`secret`/`password`/`token` assigned to literal string values. R7 covers secrets reaching logs; SEC3 covers secrets living in the source tree.
**Fix**: read from env vars or a secrets manager. Never commit credentials.

### SEC4 — yaml.load() without SafeLoader [HIGH]
`yaml.load()` with no `Loader=yaml.SafeLoader` — arbitrary object
deserialization, an RCE risk on untrusted input.
```python
cfg = yaml.load(f)              # SEC4: can construct arbitrary objects
```
**Fix**: `yaml.safe_load(f)` or `yaml.load(f, Loader=yaml.SafeLoader)`.

### SEC5 — SQLite engine without PRAGMA foreign_keys=ON [HIGH]
A file that creates a SQLite engine (`create_engine`) but never sets
`PRAGMA foreign_keys` — SQLite silently ignores FK constraints by default,
so orphaned rows accumulate without errors.
**Fix**: set `PRAGMA foreign_keys=ON` on every connection (e.g. an SQLAlchemy
`connect` event listener).

### SEC6 — SQL built with f-string/format/concat in execute() [HIGH]
A string containing an SQL keyword, built via f-string, `%`, `.format()` or
concatenation, passed to an `execute()`-style call — SQL injection.
```python
cur.execute(f"SELECT * FROM users WHERE id = {uid}")   # SEC6
```
**Fix**: parameterized queries — `execute("... WHERE id = ?", (uid,))`.

### SEC7 — DEBUG = True in a settings module [MEDIUM]
`DEBUG = True` at module level in a settings file — exposes stack traces and
secrets if it reaches production.
**Fix**: default to `False`; enable debug via an environment variable.

---

## Bug patterns

### B1 — mutable default arguments [HIGH]
Function default argument that is a mutable literal (`[]`, `{}`, `set()`) or mutable constructor (`dict()`, `list()`). The default is evaluated ONCE at definition time — all callers share the same object.
```python
def add(item, items=[]):       # B1: shared list across all calls
    items.append(item)
    return items
```
**Fix**: `def add(item, items=None): items = items or []`.

### B2 — HTTP calls without timeout= [MEDIUM]
`requests.get()` or `session.post()` with no `timeout=` kwarg. The call hangs forever on a dead server.
```python
requests.get(url)              # B2: no timeout
```
**Fix**: `requests.get(url, timeout=30)`.

### B3 — daemon threads [INFO]
`Thread(daemon=True)`. Daemon threads are killed abruptly on process exit — no cleanup, no resource release. Need cooperative cancellation instead.
**Fix**: use non-daemon threads with a stop event, or accept the risk.

### B4 — tempfile.mktemp() / os.tempnam() [MEDIUM]
Race-prone temp-file APIs: the name is generated first and the file created
later, leaving a TOCTOU window an attacker can win.
```python
path = tempfile.mktemp()        # B4: race between name and creation
```
**Fix**: `tempfile.mkstemp()` or `tempfile.TemporaryFile()`.

### B5 — assert used for validation [MEDIUM]
`assert` guarding runtime input/state. Under `python -O` asserts are stripped
— the validation silently vanishes. (Type-narrowing asserts are exempt.)
```python
assert user_id is not None, "missing user"   # B5: gone under -O
```
**Fix**: `raise ValueError/TypeError` for runtime validation; keep `assert`
for internal invariants and tests.

---

## Logging

### R9 — broken structured logging [HIGH]
`log.info(...)` (or debug/warning/error) called with a keyword argument the
stdlib logger doesn't accept — raises `TypeError` at runtime, i.e. the log
line itself is a crash site.
```python
log.info("saved", user_id=uid)   # R9: TypeError — stdlib logging has no user_id kwarg
```
**Fix**: `log.info("saved", extra={"user_id": uid})` — or use a structured
logging library end-to-end.

### R10 — logging.basicConfig() called more than once [MEDIUM]
`logging.basicConfig()` at multiple sites across the project — only the first
call takes effect; the rest are silent no-ops, so half the configuration
never applies.
**Fix**: configure logging once, at the entry point.

---

## Performance

### P1 — imports inside loops [HIGH]
`import` or `from ... import` inside a `for`/`while` loop body. Re-executed every iteration.
```python
for item in items:
    import heavy_module        # P1: imported N times
```
**Fix**: move import to module level.

### P2 — imports inside functions [INFO]
Imports inside function bodies (not in loops). Anti-circular-import idiom — listed for review, not a defect.
**No fix needed** — informational. Suppress with `# audit: ok` if intentional.

### P3 — re.compile() inside functions [INFO]
`re.compile()` called inside a function body. The compiled regex is discarded and re-compiled on every call.
**Fix**: move `re.compile()` to module level.

### P4 — settings lookups in loops [MEDIUM]
`get_settings()` or `load_sandbox_config()` called inside a loop. Expensive config reads re-executed.
**Fix**: read once before the loop.

---

## Engine regressions (ABE-specific)

### E1 — prompts frozen at import [HIGH]
Module-level assignment calling `prompt_for_role()`, `prompt()`, or `_load_prompt()`. The prompt is captured once at import time — editing the `.md` file mid-run has no effect.
```python
MANAGER_PROMPT = prompt_for_role("manager")    # E1: frozen forever
```
**Fix**: call prompt getters lazily, not at module level.

### E1i — @cache prompt getters [INFO]
`@lru_cache` / `@cache` on a function that calls `prompt_for_role()`. After the first call the result is cached for the process lifetime — hot-reload never re-fires.
**Fix**: add mtime-based cache invalidation, or don't cache prompts.

### E2 — hook prompts missing {task} [HIGH]
`register_raw_prompt()` with a prompt string that doesn't contain `{task}`. The hook prompt is static — it can't inject the task-specific context.
**Fix**: add `{task}` placeholder to hook prompts.

---

## Supplemental: DOC [INFO]

Public functions and classes (not `_private`) without docstrings. Reported as a summary, not per-finding.
```python
def compute(x):               # DOC: no docstring
    return x * 2
```

---

## Severity summary

| Severity | Count | Rules |
|----------|-------|-------|
| HIGH | 17 | C1, C2, C7, SEC1, SEC2, SEC3, SEC4, SEC5, SEC6, B1, F1, R9, P1, E1, E2, D2, G3 |
| MEDIUM | 29 | C3, C4, C5, C6, C8, C9, SEC7, B2, B4, B5, F2, F3, F4, F5, R10, G1, G2, D1, D3, D4, D5, P4, T1, T2, T3, T4, T5, T7, DG1 |
| INFO | 6 | C2i, E1i, F2i, B3, P2, P3 |
| INFO (summary) | 1 | DOC |

(D1 counts once here but carries two checks, D1a and D1b.)
