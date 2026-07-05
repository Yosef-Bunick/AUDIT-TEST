# Runtime Audit — Reference

Answers: **"Will it hang or crash at 2am?"** — operational failure modes that only surface during a run.

---

## R1 — Unbounded loop [HIGH]
`while True:` with no break at the loop's own level, no return/raise, no `sys.exit` anywhere in the body. Nested function/class defs are excluded — their exits don't terminate the outer loop.

For an autonomous engine this is the runaway-run class: the loop the brakes can't reach.
```python
while True:                    # R1: no escape path
    result = poll()
    if result.done:
        pass                   # <-- no break, no return, no raise
```

## R2 — Subprocess without timeout [HIGH]
`subprocess.run()` / `call()` / `check_call()` / `check_output()` without `timeout=`. The sandbox runs agent-written code; a hung child hangs the entire round.
```python
subprocess.run(["npm", "test"])    # R2: no timeout
```
**Fix**: `subprocess.run(..., timeout=300)`.

## R3 — Blocking wait without timeout [MEDIUM]
`.communicate()` / `.wait()` / `.join()` called with NO arguments. `Event.wait()` and `thread.join()` without timeout block forever. Calls passing any argument are assumed bounded.
```python
proc.wait()                        # R3: blocks forever
thread.join()                      # R3: blocks forever
```
**Fix**: `proc.wait(timeout=60)` / `thread.join(timeout=30)`.

## R4 — Hardcoded absolute path [MEDIUM]
String literals like `C:\\AI\\...` or `/home/...` in production code. Breaks when the project moves machines.
```python
CONFIG = "C:\\AI\\config.json"     # R4: hardcoded absolute
```
**Fix**: read from settings/paths or env.

## R5 — CWD-relative path [MEDIUM]
Relative paths that resolve against `os.getcwd()` — unpredictable when the engine runs as a subprocess or from a different working directory.
```python
open("data/config.json")           # R5: CWD-relative — where does this resolve?
```
**Fix**: resolve against a known base path.

## R6 — Text I/O without encoding= [MEDIUM]
`open()` / `read_text()` / `write_text()` without explicit `encoding=` argument. Defaults to system locale (cp1252 on Windows, utf-8 on Linux) — cross-platform data corruption.
```python
text = open("log.txt").read()      # R6: no encoding
text = path.read_text()            # R6: no encoding
```
**Fix**: always pass `encoding="utf-8"`.

## R7 — Secrets reaching logs [HIGH]
Sensitive values (API keys, tokens, passwords) passed to `log()` / `print()` / `write()` / emit functions. The variable name pattern (`*key*`, `*secret*`, `*token*`, `*password*`) is the detection signal.
```python
log(f"using key: {api_key}")       # R7: secret in log output
```
**Fix**: redact before logging.

## R8 — Stackless logging [MEDIUM]
`log()` / `error()` / `exception()` calls with no `exc_info=True` or `traceback` in exception handlers. The error is logged without its traceback — impossible to debug.
```python
except Exception as e:
    log(f"failed: {e}")           # R8: no stack trace
```
**Fix**: `log(f"failed", exc_info=True)`.

## R9 — TOOL_DEFINITIONS vs run_tool parity [MEDIUM]
Tool names registered in `TOOL_DEFINITIONS` must match the handler names in `run_tool()`. A tool defined but not handled = broken tool. A tool handled but not defined = hidden surface.

## R10 — Prompt file contracts [MEDIUM]
- Every `.md` prompt file in the `prompts/` directory must be loaded by at least one `prompt_for_role()` call
- `HOOK_ROLE` role names must match `register_raw_prompt()` hook names
- Unloaded prompt files = dead documentation
- Unregistered hook names = runtime crash

## R11 — Third-party dependency audit [INFO]
Every third-party import (`import requests`, `from openai import ...`) must appear in `requirements.txt` or `pyproject.toml`. Unlisted imports = install-bombs on fresh machines.

## R12 — Prompt JSON schema vs parsed-response reads [MEDIUM]
Every field mentioned in a prompt's JSON schema must be read somewhere in the code that parses the LLM response. Schema fields never consumed = the LLM is producing data nobody uses. Response fields read but not in the schema = the LLM might not return them.

## R13 — Advisory check results ignored [MEDIUM]
`check_*()` / `validate_*()` / `verify_*()` calls whose return value is never used. A gate that runs but whose result is thrown away is a fake safety check.
```python
check_llm_call(tokens)             # R13: return value ignored
# ... continue with LLM call anyway
```
