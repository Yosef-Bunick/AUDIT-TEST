# ROADMAP — AUDIT-TEST

## Feature coverage matrix

| Language | Syntax | PhD | Runtime | Wiring | AST rules | Linter | Graph | **Coverage** |
|---|---|---|---|---|---|---|---|---|
| **python** | ✓ | ✓ 55 deep | ✓ deep | ✓ deep | ✓ deep | ✓ 5 tools | ✓ imports | **7/7** |
| **javascript** | ✓ | ✓ 15+9 | ✓ 2 | ✓ | ✓ 9 | ✓ eslint,prettier | ✓ imports | **7/7** |
| **typescript** | ✓ | ✓ 15+9 | ✓ 2 | ✓ | ✓ 9 | ✓ eslint | ✓ imports | **7/7** |
| **rust** | ✓ | ✓ 5+10 | ✓ 2 | ✓ | ✓ 10 | ✓ clippy,rustfmt | ✓ imports | **7/7** |
| **go** | ✓ | ✓ 5+9 | ✓ 2 | ✓ | ✓ 9 | ✓ go-vet,golangci | ✓ imports | **7/7** |
| **java** | ✓ | ✓ 8+9 | ✓ 2 | ✓ | ✓ 9 | ✓ checkstyle,pmd | ✓ imports | **7/7** |
| **csharp** | ✓ | ✓ 7+9 | ✓ 2 | ✓ | ✓ 9 | ✓ dotnet-format | ✓ imports | **7/7** |
| **kotlin** | ✓ | ✓ 5+9 | ✓ 2 | ✓ | ✓ 9 | ✓ detekt | ✓ imports | **7/7** |
| **swift** | ✓ | ✓ 4+9 | ✓ 1 | ✓ | ✓ 9 | ✓ swiftlint | ✓ imports | **7/7** |
| **php** | ✓ | ✓ 6+9 | ✓ 2 | ✓ | ✓ 9 | ✓ phpstan | ✓ imports | **7/7** |
| **cpp** | ✓ | ✓ 6+9 | ✓ 1 | — | ✓ 9 | ✓ clang-tidy,cppcheck | ✓ includes | **6/7** |
| **ruby** | ✓ | ✓ 3 | ✓ 2 | ✓ | — | ✓ rubocop | — | **5/7** |
| **dart** | ✓ | ✓ 1 | ✓ 2 | ✓ | — | ✓ dart-analyze | — | **5/7** |
| **scala** | ✓ | ✓ 2 | ✓ 2 | ✓ | — | ✓ scalafix | — | **5/7** |
| **elixir** | ✓ | ✓ 2 | ✓ 1 | ✓ | — | ✓ credo | — | **5/7** |
| **zig** | ✓ | ✓ 1 | ✓ 1 | ✓ | — | ✓ zig-fmt | — | **5/7** |
| **lua** | ✓ | ✓ 2 | ✓ 2 | ✓ | — | ✓ luacheck | — | **5/7** |
| **haskell** | ✓ | ✓ 2 | ✓ 2 | — | — | ✓ hlint | — | **4/7** |
| **sql** | ✓ | ✓ 3 | ✓ 1 | — | — | — | — | **3/7** |
| **html** | ✓ | — | — | — | — | ✓ htmlhint,stylelint | — | **2/7** |
| **css** | — | — | ✓ 2 | — | — | ✓ stylelint | — | **2/7** |

PhD counts: regex rules + AST rules. Python uses its own deep 55-rule AST engine (not polyglot).

---

## Gaps: real effort to match Python's depth

Python's deep audits (PhD 49 rules, wiring call-graph, runtime) use `ast.walk()` — structural
pattern matching on syntax trees. Tree-sitter gives the **exact same capability** for every
language. No compiler needed — just knowing each language's def syntax, import syntax, and
call-site patterns.

| Audit | Python uses | Per-language polyglot equivalent | LOC |
|---|---|---|---|
| PhD structural rules | `ast.walk()` + node-type matching | tree-sitter walk + node-type matching | same pattern, ~30 LOC per rule |
| Import extraction | `ast.Import`/`ImportFrom` nodes | tree-sitter `import_statement` / regex | ~50 LOC |
| Import→file resolution | dotted name → fs path | language-specific path convention | ~40 LOC |
| Call-site + def detection | `ast.Call` + `ast.FunctionDef` | tree-sitter node types per language | ~60 LOC |
| Wiring (dead symbols) | cross-file ref counting | same — extract defs+calls, cross-reference | ~150 LOC |

**Per-language total to match Python PhD+wiring: ~350 LOC.** Across 12 remaining languages: ~4,200 LOC.

### What actually needs a compiler (and Python's PhD doesn't do either)

- Type inference — "what type is `x`?"
- Control-flow analysis — "does this path ever execute?"
- Cross-function data flow — "where did this value come from?"

None of Python's 49 PhD rules use these. They're all structural. Tree-sitter can replicate every single one.

---

## ✅ Completed this session (2026-07-14)

Triggered by running `audit-code --min` against an external project
(`ledger-core`/`ledger-ui` monorepo) and manually verifying every FAIL/WARN
against the source. Four tool bugs found and fixed, all with regression tests:

| Item | What | Impact |
|---|---|---|
| Wiring: FastAPI decorator detection was `router.*`-only | `_collect_decorator_wired` now matches route/lifecycle decorators (`get/post/put/delete/patch/options/head/websocket/middleware/on_event/exception_handler`) on **any** object name, not just literally `router` — `@app.get`, `@app.middleware` on the app object itself now count | Fixes false "dead" on `main.py`-style FastAPI apps that decorate `app` directly instead of an `APIRouter` |
| Wiring: Alembic `upgrade`/`downgrade` detection never actually matched | It checked for a literal `@upgrade`/`@downgrade` **decorator**, which doesn't exist — real Alembic migrations call these by name, undecorated. New `_is_alembic_migration()` recognizes the `revision = ...` module marker Alembic writes into every migration file instead | Every migration's `upgrade`/`downgrade` was being flagged dead; now correctly recognized as framework-invoked |
| Wiring: CHECK 10 flagged every Alembic migration file as a dead module | Same `revision = ...` marker now excludes migration files from `find_dead_modules`, alongside the existing `__init__`/`__main__`/test/`if __name__` guards | No more false "dead module" per migration file |
| Wiring: `# audit: ok` suppression didn't exist in `audit_wiring.py` | `audit_phd.py`/`audit_runtime.py` have always supported inline `# audit: ok` suppression; wiring never did. Added `_is_suppressed()` (CHECK 1 + CHECK 2), scanning forward through a multi-line def/class signature so a trailing comment on the closing `):` line — the natural place to put it — is honored, not just the `def` line itself | On the external project, developers had already annotated 13 reviewed findings with `# audit: ok` that the tool was silently ignoring every run |
| Polyglot wiring: named IIFEs flagged dead | `(function foo(){...})()` invokes `foo` via the surrounding parens, not a second name reference. `wiring_scan` now skips any definition whose keyword is immediately preceded by `(` (a function *expression*, not a statement) | Fixes false positives on the common `(function name() {...})();` self-invoking pattern |
| JS-AST: `useState` "unused in JSX" required the read to be inside a JSX node | Rewrote `_check_usestate_unused_in_jsx` to flag state that's never *read* anywhere in the function body, not just inside JSX — catches both conditional early-return (`if (loading) return <Spinner/>` — the read is in the `if` test) and custom hooks that return state to a caller instead of rendering JSX (no JSX node exists in the function at all) | Was ~80% of a real project's JS-PHD MEDIUM noise (31 of 38 findings); both false-positive classes were already flagged as a known limitation below but not fixed until now |

All five fixes are regression-tested (`tests/test_audit_wiring.py`, `tests/test_polyglot.py`) and pass the wiring/phd self-audit gate (0 HIGH) on `src/audit_code` itself.

---

## ✅ Completed this session (2026-07-10)

| Item | What | Impact |
|---|---|---|
| Polyglot regex rules | 30 new rules from the external checklists (see Status column below) | 76 PhD + 32 runtime regex rules |
| CSS language spec | .css/.scss/.less/.sass detection + `!important`/z-index rules | 21st language |
| Python PhD rules | SEC6 string-built SQL in execute(), SEC7 DEBUG=True in settings, B5 validation asserts, R10 double basicConfig | 55 total |
| MegaLinter integration | Real `_run_tool` implementation, opt-in via `--megalinter` | 23 integrations; stub-SKIP issue fixed |
| quality.py decomposition | Q0–Q8 extracted from the 550-line `run()` into per-section helpers | `#needs fix` inventory empty again |
| PhD rule test coverage | 32 new tests: all 7 untested HIGH rules (SEC3, SEC5, F1, E1, E2, P1, D2) + 17 MEDIUM/INFO rules | 708 total tests |
| Stale artifacts | README stray first line + broken ROADMAP anchor fixed, CHANGELOG 0.4.0 entry added | docs match v0.4.0 |

---

## `#needs fix` inventory (empty)

```
grep -rn "# needs fix" src/
```

All clear.

---

## Known tree-sitter JSX parser limitations

These are NOT bugs in user code — they're tree-sitter JSX grammar gaps.
Files build/pass with Vite, ESLint, and node just fine.

| Pattern | Symptom | Workaround |
|---|---|---|
| `&` in JSX text (`Match & Confirm`) | parse error | Use `and` instead of `&` |
| Unicode arrows in JSX (`▾` / `▸`) | parse error | Use Unicode escapes `\u25BE` / `\u25B8` |
| `<Collapse in={var}>` | parse error near `tingOpen}>` | Use spread: `<Collapse {...{in: var}}>` |
| `&` inside a plain string *nested in an object literal* inside a JSX attribute expression (e.g. `sx={cond ? ({ '& td': {...} }) : ({})}`) | parse error reported at the file's first line, unrelated to the actual `&` location | No clean workaround found yet — avoid `&`-keyed CSS-in-JS selectors inside conditional `sx` props, or hoist the object to a named constant outside the JSX |
| ~~`useState` flags as "unused in JSX"~~ | ~~MEDIUM false positive~~ | ✅ fixed 2026-07-14 — `js-ast-usestate-unused-in-jsx` now checks for a read anywhere in the function body, not just inside a JSX node |

Discovered 2026-07-09 wiring ledger-ui. Fixed 4 via text replacement, 1 via spread syntax.
The `&`-in-nested-string variant was found 2026-07-14 auditing a different project
(`ledger-ui/src/views/Reports.jsx`) — same grammar-gap family, not yet a tool fix.

Additionally, tree-sitter can't parse TypeScript generics in `.tsx` files:
- `useState<Receipt[]>([])` — type parameter on hook
- `<Record<string, string>>()` — generic type in JSX
- Lambda type annotations: `(c: number) =>`
These are all valid TypeScript that builds clean. Known from receipt-pipeline-pkg sweep (2026-07-07).

---

## Known fragility: JS AST rules module

`src/audit_code/adapters/javascript/ast_rules.py` is prone to file corruption from repeated `patch()` edits. When adding new tree-sitter JS rules, prefer:
1. Write the full file with `write_file()` instead of patching
2. Add polyglot regex rules instead of tree-sitter rules when possible
3. After any edit, verify with `ast.parse()` and `pytest tests/test_jsx_fallback.py`

Discovered 2026-07-07 when trying to add React hook rules — 3 corruption-recovery cycles.

---

## Future (ordered by effort ÷ value)

| # | Item | Effort | Notes |
|---|---|---|---|
| 1 | **Graph: remaining languages** | ✅ done | Rust, Go, Java, C#, C/C++ import extraction + resolution |
| 2 | **Graph G4**: edge-type labels, `--lang` filter | ✅ done | JSON output has edge types: import/subprocess/ffi |
| 3 | **Deep PhD for Rust** | ✅ done | 10 rules: unsafe, Command, panic, unwrap/expect, empty err, fs-remove, consts, inline-use, god-func, side-effects |
| 4 | **Syntax adapters** for missing langs | ✅ done | 10 new: Kotlin, Swift, PHP, Ruby, Dart, Scala, Elixir, Zig, Lua, Haskell. 19 total |
| 5 | **Python PhD**: T7 mock-signature | ✅ done | Cross-file: checks mock.patch('mod.func') targets exist in production |
| 6 | **Python PhD**: F5 lock-ordering | ✅ done | Detects locks acquired in inconsistent order within same function |
| 7 | **JS AST**: J1.9 setState-in-render | ~40 LOC | Control-flow within component |
| 8 | **JS AST**: J2.1 prop drilling, J2.2 MUI barrel | ~80 LOC each | Cross-file component tree |
| 9 | Megalinter integration | ✅ done | Real integration via `_run_tool`, opt-in `--megalinter` (Docker-based, slow) |
| 11 | **Runtime: hardcoded URL detection** | ✅ done | `poly-js-hardcoded-url` flags deploy-host URLs in JS |
| 12 | **Wiring: FastAPI decorator detection** | ✅ done | `@router.get/post` functions no longer flagged as dead |
| 13 | **Python: broken structured logging** | ✅ done | R9: invalid kwargs to `log.info()` flagged HIGH |
| 14 | **Python: SQLite FK not enabled** | ✅ done | SEC5: `create_engine(sqlite://)` without `PRAGMA foreign_keys=ON` |
| 15 | **N1-N9: New ledger-core check rules** | ~200 LOC | Needs the gap-analysis spec — the 9 rules (path traversal, missing auth, race conditions, session leaks) aren't defined precisely enough to implement yet; hardcoded secrets already = SEC3/poly-secret |
| 16 | **`audit-test rules` — rule catalog + docs-drift check** | ~80 LOC | Dogfooding gap found 2026-07-16: brought docs/phd.md up to date by hand — grepping `sink.add("ID", ...)` across `audit_phd.py`/`audit_runtime.py`/`polyglot.py`, then diffing against `### ID —` headings in `docs/*.md`. 16 rule IDs (C7-C9, T7, F5, G1-G3, SEC4-SEC7, B4-B5, R9-R10) had drifted silently out of the doc. A `rules` subcommand that walks the SECTIONS/rule tables already in each audit module and reports any ID with no matching doc heading (and vice versa) would catch this automatically instead of needing a manual audit every few releases |
| 17 | **`deps`: declared-vs-imported package check** | ~60 LOC | Dogfooding gap found 2026-07-16: `adapters/cpp/phd.py` does `import tree_sitter_cpp` at module top, but `tree-sitter-cpp` was never added to `pyproject.toml` — every `pip install audit-test` silently SKIPped the whole C/C++ deep AST pass (`polyglot.py`'s `try/except ImportError` masked it as a clean SKIP note, not a crash). `audit-test deps` already parses imports for its scan; extending it to flag "imported but not declared in `[project.dependencies]`" (and the inverse, declared-but-unused) would catch this class of bug before a release ships with a dead subsystem |
| 18 | **Version-sync check** (`pyproject.toml` vs `__init__.py` vs latest CHANGELOG entry) | ~20 LOC | Found `__version__ = "0.3.9"` in `src/audit_code/__init__.py` while `pyproject.toml` said `0.4.0` (session 2026-07-16, fixed same session). A one-file check — part of `gate` or a standalone `audit-test release-check` — comparing the three version strings would turn this from a manual `grep` into an automatic HIGH finding |
| 19 | **Wiring: FastAPI decorator detection — residual false positive** | needs repro | The 07-14 fix (item 12) covered `@router.get/post/...` and `@app.get/...` on the app object, dropping ledger-core's wiring HIGHs from 42 to ~0. But a session on 2026-07-16 (against the same project, post-fix) still reported "the wiring HIGH is the known FastAPI-decorator false positive" — singular, so the fix isn't fully closed. Needs the actual file/decorator pattern from that project to identify what's still uncovered (candidates: `@router.websocket`, a decorator via an aliased/reassigned router variable, or a decorator factory call like `@router.get(path, **kwargs)` with a non-literal first arg) |
| 20 | **Investigate: does any audit-test mode delete untracked files?** | investigation | A session on 2026-07-15 followed a remembered precaution — "`git add -A` first (audit-test wipes untracked files)" — before running the tool. Code review of `audit_gate.py`'s shadow-worktree (`git worktree add --detach <tmp> HEAD`) shows it operates in an isolated temp directory and never touches the real working tree, and no other module calls `rmtree`/`unlink`/`git clean` outside its own tmp dirs. Either this is a stale/mistaken belief worth correcting in the user's own workflow notes, or there's a real code path (possibly `deps`'s `.requirements` generation, or `fix` mode) that does something surprising to untracked files — needs a deliberate repro (untracked file + `audit-test fix`/`deps`/`gate` + check survival) to confirm one way or the other |
| 21 | **Q2 (ruff): hardcoded `--select`/`--ignore` overrides the target project's own ruff config** | ~15 LOC | Confirmed in source: `quality.py:276-282` — `_RUFF_CHECK_ARGS` hardcodes `--select E,F,W,I,B,S --ignore S101,S105,...`. A session on 2026-07-08 added a `ruff.toml` to ledger-core specifically to make bare `ruff check` pass, then found audit-test's Q2 still flagged the same findings — "your lint module runs ruff isolated, so the repo `ruff.toml` I added ... is ignored." Explicit CLI `--select`/`--ignore` flags win over a project's `[tool.ruff]`/`ruff.toml` config, so a project's own considered rule choices (line-length, per-file-ignores, extra ignores) are silently discarded every run. Fix: only pass audit-test's `--select`/`--ignore` defaults when the target has no `ruff.toml`/`[tool.ruff]` in its `pyproject.toml`; otherwise defer to the project's config (maybe still force-include `S` security codes so `--select` additions layer on top rather than replace) |
| 22 | **New PHD rule: insecure default value for a secret/key env var** | ~30 LOC | Confirmed in source: SEC3's `SECRET_ASSIGN_RE` (`audit_phd.py:174-177`) only matches a secret-named variable assigned a **direct** string literal (`SECRET = "..."`) — it requires a quote immediately after `=`. It does not match `SECRET = os.getenv("JWT_SECRET", "change-me-in-production")`, the exact pattern found by hand (not by audit-test) in a 2026-07-14 session: `auth.py:22` falls back to a hardcoded, recognizable-placeholder default and only logs a warning — never refuses to boot — if the real env var is unset in production. This is a distinct, common, and dangerous class SEC3 doesn't cover at all: `os.getenv`/`os.environ.get`/`config.get("X_SECRET"/"X_KEY"/"X_PASSWORD"/"X_TOKEN", "<non-empty literal>")`. Add as SEC8 — flag any `getenv`/`environ.get` call whose first arg is secret-shaped (same name pattern as SEC3) and whose second arg is a non-empty string literal, regardless of how placeholder-ish it looks (the placeholder-ish ones are the most dangerous, since they're the most likely to reach production unnoticed) |
| 23 | **Promote missing-tool SKIPs (Q4 CVE scan, Q3 mypy, etc.) into the top-level summary** | ~25 LOC | Confirmed in source: `_q4_cves` (`quality.py:397-430`) appends `"  SKIP: neither pip-audit nor safety installed"` into quality's internal `stdout_lines` — only visible in verbose/full mode — while `runner.py`'s top-level `AUDIT RESULTS` table (`runner.py:288-292`) prints exactly one line per top-level module (`quality`, `wiring`, …), showing only its aggregate HIGH/MEDIUM/INFO count. A HIGH-severity sub-check silently not running produces **no visible signal** in the default report. This is exactly what happened in a real project: a 2026-07-15 session found, while combing through unrelated notes, that `pip-audit` had never been installed and **CVE scanning had been silently skipped in every audit run** for the life of the project — nobody caught it because the default report looked identical to a clean pass. Fix: have `quality` (and any other multi-tool module) return which of its optional external tools were missing, and have the top-level summary print one extra line whenever any sub-check SKIPped for a missing tool — e.g. `[WARN ] quality  ... (Q4 CVE scan SKIPped: pip-audit not installed)` — so a missing scanner is exactly as visible as a real finding, not something you only notice by reading verbose output line-by-line |
| 24 | **`audit-test refs <symbol>` — symbol lookup from wiring's existing index** | ~60 LOC | Mined 9 days of agent session transcripts (2,692 shell commands): ~600 of ~870 grep invocations were symbol lookups — "where is `X` defined" (`def _node_key\|def _walk`), "who uses `X`" (`handleExportCSV`, `_maybe_write_dead_json`), "who imports module `Y`" (`import audit_phd\|import audit_wiring`). The wiring audit already builds exactly this def/reference index on every run to detect dead symbols — it's computed and thrown away. Expose it: `audit-test refs <name>` prints the definition site(s), every reference site with file:line, and the dead/test-only verdict; `--json` for agents. Would replace the single most common manual command category outright |
| 25 | **`audit-test test <selector>` — project-aware pytest runner + `[suite]` config** | ~40 LOC | Same transcript mining: 236 raw pytest invocations, and every single one hand-carried the same project-specific incantation — `-p no:logfire` everywhere, plus per-project `-p no:deepeval`, `PYTHONPATH=src`, `PYDANTIC_DISABLE_PLUGINS=1` env prefixes — usually piped to `tail`. The knowledge of "how this project's tests must be run" lives in agent memory files instead of the project. Add `[suite] pytest_args = [...]` / `env = {...}` to `audit-code.toml` (Q5's `pytest_extra` plumbing already threads extra args — it's just not config-fed), have the `suite` audit use it, and add `audit-test test <file-or-node-or--k-expr>` that runs pytest with those settings applied so one-off test runs get the right flags for free |
| 26 | **`audit-test report <report.json> [module]` — slice saved JSON reports** | ~30 LOC | Same mining: 10+ hand-written `python -c "import json; d=json.load(open('audit_vN.json')); [print(a['stdout']) for a in d['audits'] if a['id']=='wiring']"` one-liners across sessions — the workflow was: run the full audit once with `--json` (it's slow), then repeatedly slice individual modules' stdout/findings out of the saved file while fixing. A `report` subcommand that pretty-prints one module (or lists modules, or filters findings by severity) from a saved report file would replace all of them |
| 27 | **Single-file syntax check: let `syntax` take a file path** | ~20 LOC | Same mining: repeated `python -c "import ast; ast.parse(open('file.py').read())"` one-liners to verify one just-edited file parses — and `docs/skill.md`'s own pre-edit checklist literally prescribes that raw one-liner because the tool has no equivalent (`--syntax` is repo-wide only). `audit-test syntax <file>` routing through the matching language adapter (any of the 19, not just Python) makes the post-edit parse check one consistent command |
| 28 | **`--changed` — incremental audit of working-tree-modified files** | ~50 LOC | 60-day transcript mining: agents re-ran `audit-test min` / `audit-test f` ~49 times in fix loops — full-tree rescan after every batch of edits, even when only 2-3 files changed. Add `--changed`: build the graph/indexes over the whole tree as usual (wiring needs global refs anyway) but **report only findings in files that differ from HEAD** (`git diff --name-only` + untracked). Different niche from `gate` — gate is the heavyweight pre-commit judge (worktree, suite, mutation); `--changed` is the 2-second inner-loop signal "did my last edit introduce anything" that agents currently approximate by running the full audit and eyeballing which findings moved |
| 29 | **`audit-test baseline` — accepted-findings file, report only new ones** | ~60 LOC | Recurring pattern in nearly every external-project session: the audit reports 40+ HIGHs, and the agent (or user) re-derives by hand which are "pre-existing / known false positives" — e.g. "every HIGH finding is pre-existing in files this change never touched" (2026-07-16), "wiring's 42 HIGHs are your known false positives; quality's 2 HIGHs are pre-existing" (2026-07-13). That triage knowledge evaporates at session end and gets redone next session. `audit-test baseline` snapshots current findings (rule id + file + symbol, not line numbers — they shift) into `.audit-test-baseline.json`; subsequent runs report only findings NOT in the baseline, plus one summary line (`42 baselined findings suppressed — audit-test baseline --show to list`). Same model as semgrep/ESLint baselines. Complements `# audit: ok` (per-line, requires editing the file) for the bulk legacy case |
| 30 | **`build` module — does the frontend/project actually build?** | ~40 LOC | 60-day mining: 68 hand-run build-verification commands (`npm run build` ×48, `npx tsc` ×14, `vite build` ×6) — sessions repeatedly ended with a manual "frontend builds clean" check because no module answers it: JS syntax is `node --check` per file, which can't see JSX transforms, bundler resolution, or TS type errors across files. Opt-in `--build` (like `--megalinter`): per detected language run the real build — `npm run build` when package.json has the script, `tsc --noEmit` when tsconfig.json exists (already partially there for TS syntax), `cargo build`/`go build`/`mvn compile` where the syntax adapters currently stop at parse-level. Honest SKIP when the toolchain is missing. Slow, so never default — but it's the one question ("will this deploy?") the current stack can't answer |
| 31 | **`audit-test doctor` — external-tool health check** | ~40 LOC | Rare-command mining (v2 miner, segment-level): sessions probed tool availability by hand — bare `mypy`, `pip-audit`, `coverage`, `mutmut`, `python -m safety --version`, `gh --version`, plus a grep over `quality.py` for its own `shutil.which` calls to figure out *what audit-test even looks for*. There's no way to ask the tool "what can you run on this machine?" — you find out one honest SKIP at a time, run by run (and per item 23, some SKIPs are buried in verbose output). `doctor` prints every external tool across all modules and integrations — found (with version) / missing (with install hint) — plus which detected languages' checks are degraded by the gaps. One command answers what currently takes a dozen probes |
| 32 | **JS/JSX syntax: esbuild fallback for tree-sitter JSX gaps** | ~30 LOC | Rare-command mining: a session hand-built a JSX syntax checker with node + esbuild (`esbuild.buildSync({entryPoints:['SettingsPage.jsx'], bundle:false, loader:{'.jsx':'jsx'}})` → `SYNTAX OK`) specifically because tree-sitter's JSX grammar false-flags valid files — the exact parse gaps already documented in "Known tree-sitter JSX parser limitations" above (`&` in JSX text, generics in .tsx, etc.). esbuild parses real-world JSX correctly and is already present in most Vite projects' node_modules. When a `.jsx`/`.tsx` file fails the tree-sitter parse, re-check it with esbuild (project-local `node_modules/.bin/esbuild`, else global) before reporting — tree-sitter stays the zero-dependency default, esbuild demotes its known false positives to SKIP-with-note. Would eliminate the whole "Known tree-sitter JSX parser limitations" workaround table for projects that have node |
| 33 | **`deps --installed` — verify declared deps are actually importable** | ~50 LOC | Hermes session mining (30 files, v2 segment-level miner): 10 ERR from `pip list`/`apt list`/`dpkg -l`/`.venv/bin/pip` probes — sessions repeatedly checked "did `pip install` actually work?" by hand. Same pattern as item 17 (declared-vs-imported) but the inverse direction: a dep declared in pyproject.toml that fails to import at runtime is dead weight, and a dep imported but NOT declared (item 17) silently skipped by `try/except ImportError`. `deps --installed` imports every declared dependency (`importlib.import_module()`) and flags import failures as HIGH. Together with item 17, closes the full circle: declared→importable AND imported→declared. Also catches the C/C++ tree-sitter case from item 17: `tree-sitter-cpp` was `pip install`-ed but never added to pyproject.toml — the import worked, but it was undeclared |
| 34 | **MongoDB `$where`/`$eval` injection detection** | ✅ done | JS: `poly-mongo-injection` regex in polyglot.py. Python: SEC8 AST rule in audit_phd.py (catches `$where`/`$eval` in dict and `db.eval()`). 4 PhD + 3 polyglot tests |
| 35 | **T-SQL `EXEC`/`sp_executesql` injection** | ✅ done | SQL: `poly-sql-exec-injection` regex in polyglot.py. Python: extended SEC6 with T-SQL keywords (`exec`, `sp_executesql`, `merge`). 2 PhD + 3 polyglot tests |
| 36 | **FastAPI auth guard detection (AUTH1)** | ✅ done | AUTH1 AST rule in audit_phd.py. Detects `@router.get/post/...` without `dependencies=[Depends(...)]` or auth-like function params. Skips test dirs and `no_auth`-named files. 5 tests |
| 37 | **Okta: extend SEC3 secret pattern** | ✅ done | Added `okta_` token shapes to SECRET_TOKEN_RE and `okta_token`/`okta_secret`/`okta_client_id` to SECRET_ASSIGN_RE in audit_phd.py. 2 tests |
| 38 | **LangChain: `temperature=0` missing** | ✅ done | LANG1 AST rule in audit_phd.py. Flags `ChatOpenAI()`, `AzureChatOpenAI()`, `ChatAnthropic()`, `ChatGoogleGenerativeAI()`, `ChatVertexAI()` without `temperature=` kwarg. MEDIUM severity. 3 tests |
| 39 | **Azure Blob: path traversal via user input** | ~40 LOC | Deferred — needs AST to distinguish user_param from string literal in `get_blob_client()` calls |
| 40 | **MCP: tool not registered** | ~50 LOC | Deferred — needs AST call-graph across files to verify `@server.tool()` functions are in Server() constructor |
| 41 | **LangGraph: graph node unreachable** | ~40 LOC | Deferred — needs AST state-machine analysis for StateGraph edge connectivity |
| 42 | **K8s: container running as root** | ✅ done | `poly-k8s-run-as-root` regex in polyglot.py YAML spec. Flags containers without `securityContext.runAsNonRoot: true`. HIGH. 3 polyglot tests |
| 43 | **K8s: `:latest` image tag** | ✅ done | `poly-k8s-latest-tag` regex in polyglot.py YAML spec. Flags `:latest` or missing tag. MEDIUM. Also: `poly-k8s-privileged` (HIGH). 5 polyglot tests |
| 44 | **YAML language spec** | ✅ done | `_YAML` LangSpec added to polyglot.py (`.yaml`, `.yml`, `supports_wiring=False`). Included in `_ALL_SPECS`. Prerequisite for K8s rules |
| 45 | **BOTTLE1/BOTTLE2 — async bottleneck detection** | ✅ done | BOTTLE1: `await` in loop without `asyncio.gather()` (MEDIUM). BOTTLE2: sync blocking I/O (`requests.get`, `time.sleep`) inside `async def` (HIGH). 8 tests |
| 46 | **Q9 Scalene profiler integration** | ✅ done | `_q9_scalene` in quality.py. Checks `scalene --version`, reports availability. SKIP if not installed. Full profiling run (scalene against project) deferred to `--bottleneck` CLI flag |

## Agent mining — Hermes session analysis (2026-07-16)

Mined 30 Hermes session files (11 parent sessions, May 18-20 2026, 1,523 tool calls)
alongside the Claude mining above. Hermes uses continuation sessions — multiple
JSON files share the same `session_id` (e.g. session `20260519_125404_e35ca5`
spans 11 continuation files). The miner counts by filename to capture per-file
breakdowns.

Mirrored toolkit at `C:\AI\mining\hermes_mining\` (same scripts, different session source).

### Tool distribution (Hermes vs Claude — same needs, different tools)

| Need | Claude tool | Hermes tool | Hermes count |
|------|------------|-------------|-------------|
| Read file | Bash `cat`/`head` | `read_file` | 626 (41%) |
| Search/grep | Grep tool + shell `grep`/`rg` | `search_files` | 285 (19%) |
| Edit file | Bash `sed`/write | `patch` | 220 (14%) |
| Shell command | Bash/PowerShell | `terminal` | 143 (9%) |

### Agent antipatterns found (not audit-test, but agent hygiene)

| Pattern | Count | Fix applied |
|---------|-------|-------------|
| `sed -n '517,522p' file` — line extraction | 6 | → `read_file(path, offset=517, limit=6)`. Added to `default-coding` skill pitfalls |
| `cp xlam → /tmp/zip` repeated 11× | 11 | First copy suffices; reuse the zip. Agent should cache/reuse intermediate artifacts |
| `python -c` for multi-line zipfile inspect | 10 | Could use `execute_code` tool (cleaner, gets stdout back structured) |

### search_files intent classification (285 calls)

Classified every `search_files` pattern using `grep_intents.py`:

| Category | Count | % | Same as Claude? |
|----------|-------|---|-----------------|
| other/complex | 122 | 43% | — (mostly `*` dir listing) |
| find usages (alternation) | 77 | 27% | ✓ matches Claude grep |
| find definition (def/class) | 37 | 13% | ✓ matches Claude grep |
| find usages (single symbol) | 27 | 9% | ✓ matches Claude grep |
| file extension search | 16 | 6% | ✓ matches Claude grep |
| find imports | 6 | 2% | ✓ matches Claude grep |

**Cross-tool agreement**: ~57% of Hermes search_files calls and ~69% of Claude
grep calls are definition/usages/import lookups. Both agents spend most search
time on "where is X / who uses X" — validating items 24-27 (refs, report,
syntax, changed) apply regardless of which agent the developer uses.

## Operational gotchas (ledger-core specific)

These affect anyone running audit-test on the accounting project. Not audit-code bugs, but environment constraints.

| # | Gotcha | Workaround |
|---|---|---|
| G1 | **logfire pytest plugin crashes** — `ImportError: cannot import name 'ReadableLogRecord'` | `pytest -p no:logfire` (opentelemetry version mismatch in local env) |
| G2 | **`.requirements` files auto-generated** by `audit-test deps` | Never commit them. `rm ledger-ui/.requirements` after deps scans |
| G3 | **SQLite FK enforcement breaks tests** — enabling `PRAGMA foreign_keys=ON` causes IntegrityError in fixtures that don't seed FK references | Fix fixtures first, THEN enable FK pragma |

## Duplicate module debt

Two parallel implementations exist for some modules — one used by the runner, one for self-audit. Changing one leaves the other stale.

| Module | Runner | Self-audit | Lines |
|---|---|---|---|
| Config | `src/audit_code/config.py` | `src/audit_code/audit_config.py` | Same constants duplicated |
| Quality | `src/audit_code/quality.py` (850L) | `src/audit_code/audit_quality.py` (573L) | Potentially divergent |
| Suite | `src/audit_code/suite.py` | `src/audit_code/audit_suite.py` | Potentially divergent |

Suggests incomplete migration. Should be deduplicated — self-audit should import from production modules.

---

## Undocumented known limitations

These are acknowledged in source docstrings but have no roadmap entries:

| Source | Limitation |
|---|---|
| `audit_wiring.py:106-118` | Name-global resolution — two defs sharing a name shadow each other |
| `audit_phd.py:109-113` | Name-level analysis — renamed handles fool F1 lock detection |
| ~~`megalinter.py`~~ | ~~Stub returns SKIP even when installed~~ — ✅ fixed 2026-07-10: real integration, runs `mega-linter-runner`/`megalinter` when present |

---

## Test coverage gaps

### PHD rules — ✅ closed 2026-07-10

All 7 previously untested HIGH rules (SEC3, SEC5, F1, E1, E2, P1, D2) and the
key MEDIUM/INFO rules (C3–C6, B2, B3, F2–F4, G1, D1, D4, D5, P2–P4, DG1, T1,
T4) now have positive + negative tests in `tests/test_phd_new_rules.py`.
Remaining without a dedicated test: D3 (flat imports), T2/T3/T5 (covered
indirectly by the T7 suite and self-audit).

### Polyglot rules — ✅ mostly closed

`tests/test_polyglot.py` (127 tests) now covers at least one rule per language
including Zig, Scala, Lua, Haskell, Elixir, Dart, SQL, CSS, plus the shared
rules (`poly-todo`, `poly-unbounded-loop`, `poly-debug-leftover`) and every
rule added 2026-07-10. Not every language×rule combination has a fixture —
add one whenever a rule misfires in the field.

### Known CLI bug (xfail in test suite)

`test_cli.py:148` — `--path gate` is misinterpreted as gate mode instead of a flag value. Workaround: use `--path ./gate` instead. Tagged `xfail(strict=True)` — test expects it to fail.

---

## Stale artifacts — ✅ fixed 2026-07-10

| File | Issue | Fix |
|---|---|---|
| `README.md:1` | "still under construction" stray line | removed |
| `CHANGELOG.md` | Only had 0.1.0 entries | 0.4.0 cumulative entry added |
| `README.md` | Broken anchor `#known-issues-needs-fix` | now `#needs-fix-inventory` |

## Rule candidates from external checklists

Source: [awesome-skills/code-review-skill](https://github.com/awesome-skills/code-review-skill)
> ~95 rule candidates across 20+ languages/frameworks. Status updated 2026-07-10:
> ✓ = implemented (rule id given), *deferred* = needs AST/type/absence analysis a
> regex can't do reliably.

### React/JS (highest value)

| Rule | What | Bug class | Status |
|---|---|---|---|
| Hooks in conditional | `if { useState() }` → crashes | Runtime crash | ✓ poly-js-hook-conditional |
| `useEffect` missing cleanup | No return function → leaks subs/timers | Memory leak | deferred — pairing subscribe with `return` needs AST block tracking |
| Component inside component | Nested `function Foo()` → re-mounts every render | Perf bug | deferred — needs AST nesting analysis |
| `parseInt(x)` without radix | `parseInt('09')` → 0 in old engines | Silent wrong value | ✓ poly-js-parseint-no-radix |
| `==` instead of `===` | Type coercion → `'' == 0` is true | Logic bug | ✓ poly-js-loose-eq (`== null` idiom exempt) |
| Array index as key | `key={i}` → React reuses wrong DOM on sort | UI corruption | ✓ partial — js-ast-map-missing-key flags missing keys |
| Missing `key` prop | React can't track list items | UI corruption | ✓ js-ast-map-missing-key |

### FastAPI/Python

| Rule | What | Bug class | Status |
|---|---|---|---|
| `Depends` yield without `async with` | Session leaks if route raises | Connection leak | deferred — needs scope analysis |
| `response_model` missing on route | Exposes DB fields to client | Data leak | deferred — absence check, FP-prone |
| Route does real work (not Depends) | Inline DB/validation in route → untestable | Architecture | deferred — judgment call, not statically checkable |
| `response_model` uses Pydantic v1 syntax | `class Config` for v2 model → silent failure | Version drift | deferred — needs installed-version context |

### TypeScript

| Rule | What | Bug class | Status |
|---|---|---|---|
| `any` type usage | Bypasses type checker → runtime surprise | Type safety | ✓ poly-ts-any (incl. `as any`) |
| Missing `await` on async call | Returns Promise, not value | Logic bug | deferred — needs type info (tsc/eslint rule) |
| `this` in non-arrow callback | Context lost → `this.foo` is undefined | Runtime crash | deferred — needs scope analysis |

### Rust

| Rule | What | Bug class | Status |
|---|---|---|---|
| `unwrap()` without context | Panics with no message → un-debuggable | Crash | ✓ rs-ast-unwrap, poly-rust-unwrap |
| `clone()` in hot loop | Allocates on every iteration | Perf bug | ✓ poly-rust-clone-in-loop (INFO) |
| Missing `#[must_use]` on Result return | Caller ignores error → silent failure | Logic bug | deferred — needs type info (clippy covers) |

### Go

| Rule | What | Bug class | Status |
|---|---|---|---|
| `defer` in loop | Resources accumulate | Resource leak | ✓ go-ast-defer-loop, poly-defer-in-loop |
| `interface{}` instead of `any` | Pre-1.18 idiom | Type safety | ✓ poly-go-empty-interface (INFO) |

### Java

| Rule | What | Bug class | Status |
|---|---|---|---|
| `Date`/`Calendar`/`SimpleDateFormat` | Deprecated, thread-unsafe | Correctness | ✓ poly-java-legacy-date |
| `Optional` as field or parameter | Only for return values | Anti-pattern | ✓ poly-java-optional-field (fields; params deferred) |
| `@Autowired` field injection | Use constructor injection | Architecture | ✓ poly-java-field-injection |

### C#

| Rule | What | Bug class | Status |
|---|---|---|---|
| `Task.Wait()` / `.Result` / `async void` | Deadlocks, unobserved exceptions | Runtime crash | ✓ poly-cs-blocking-async, poly-cs-async-void |
| Missing `CancellationToken` | Can't cancel long ops | Resource leak | deferred — needs signature semantics |
| Sync/async mixed access | Deadlocks in ASP.NET | Runtime crash | ✓ partial — poly-cs-blocking-async catches the sync-over-async half |

### C/C++

| Rule | What | Bug class | Status |
|---|---|---|---|
| Raw `new`/`delete` in business logic | Use smart pointers | Memory leak | ✓ poly-cpp-raw-new (smart-pointer lines exempt) |
| No `const` on pointer params | Mutates caller data silently | Correctness | deferred — needs type analysis (clang-tidy covers) |
| Unchecked allocation size | `malloc(n * size)` overflow | Security | ✓ poly-c-alloc-overflow (INFO) |

### Kotlin

| Rule | What | Bug class | Status |
|---|---|---|---|
| `GlobalScope` usage | Leaked coroutines | Resource leak | ✓ poly-kotlin-globalscope |
| `Job()` constructor | Breaks parent-child relationship | Correctness | ✓ poly-kotlin-job-in-builder |
| `CPU` work on `Dispatchers.Main` | UI freeze | Perf bug | deferred — "CPU work" isn't statically recognizable |

### Swift

| Rule | What | Bug class | Status |
|---|---|---|---|
| `!` / `try!` force unwrap | Crashes on nil/error | Runtime crash | ✓ sw-ast-force-try, sw-ast-force-cast, poly-swift-force |
| Closure without `[weak self]` | Retain cycle → memory leak | Memory leak | deferred — needs retain-cycle analysis; FP storm as regex |
| `Task {}` fire-and-forget | Leaked, never cancelled | Resource leak | deferred — idiomatic in SwiftUI actions, FP-prone |

### PHP

| Rule | What | Bug class | Status |
|---|---|---|---|
| Missing `strict_types=1` | Type coercion bugs | Correctness | deferred — per-file absence check; polyglot rules are match-based |
| `password_hash()` not used | Plaintext or weak hashing | Security | deferred — absence check |
| SQL without parameterized queries | SQL injection | Security | ✓ poly-php-sql-interp (HIGH) |

### Vue / Svelte

| Rule | What | Bug class | Status |
|---|---|---|---|
| Destructuring `reactive` object | Loses reactivity → stale UI | UI corruption | ✓ poly-js-reactive-destructure |
| `computed` with side effects | Mutation during render → infinite loop | Runtime crash | deferred — needs AST purity analysis |
| `$effect` without cleanup | Subscription/timer leak | Memory leak | deferred — absence check |

### General (all languages)

| Rule | What | Bug class | Status |
|---|---|---|---|
| Integer overflow in allocation | `malloc(n * size)` with no overflow check | Security | ✓ poly-c-alloc-overflow |
| `==` on floats | `0.1 + 0.2 == 0.3` → False | Logic bug | ✓ C9 (Python) |
| Dead code after `return`/`throw` | Unreachable statements confuse readers | Maintainability | deferred — needs per-language CFG |

### Python (general)

| Rule | What | Bug class | Status |
|---|---|---|---|
| Mutable default arg `def f(x=[])` | Shared list across all calls | Shared state | ✓ B1 (existing) |
| `except:` bare except clause | Swallows SystemExit, KeyboardInterrupt | Swallowed errors | ✓ C1 (existing) |
| Unsafe `yaml.load()` without SafeLoader | Arbitrary code execution | RCE | ✓ SEC4 (existing) |
| `assert` used for validation logic | Stripped with `python -O` flag | Logic bug | ✓ B5 (new; isinstance / is-not-None narrowing exempt) |
| `logging.basicConfig()` called >1x | Only first call takes effect | Silent failure | ✓ R10 (new; cross-file count) |

### Django

| Rule | What | Bug class | Status |
|---|---|---|---|
| `DEBUG = True` in production settings | Exposes stack traces, secrets | Data leak | ✓ SEC7 (new; dev/local/test settings exempt) |
| `SECRET_KEY` hardcoded (not from env) | Key exposed in source control | Security | ✓ SEC3 (existing secret-assignment detection) |
| Raw SQL via `cursor.execute()` with f-string | User input interpolated | SQL injection | ✓ SEC6 (new; also %/.format/concat) |

### FastAPI extras

| Rule | What | Bug class | Status |
|---|---|---|---|
| Route without `Depends(get_current_user)` | Missing auth middleware | Auth bypass | deferred — auth spellings vary per project; absence check |
| `BackgroundTasks` for CPU-bound work | Blocks event loop thread | Perf bug | deferred — "CPU-bound" not statically recognizable |
| `async def` route with sync blocking call | No `run_in_executor` | Blocks event loop | deferred — needs call-graph blocking classification (R3 covers unbounded waits) |

### Go extras

| Rule | What | Bug class | Status |
|---|---|---|---|
| Unchecked `err` return — ignored | Error swallowed silently | Silent failure | ✓ go-ast-discarded-err, poly-empty-errcheck |
| `sync.Mutex` passed by value | Copy = separate lock, no sync | Logic bug | ✓ poly-go-mutex-value |
| `time.After` inside `select` loop | Timer never GC'd until fires | Memory leak | ✓ poly-go-timeafter-select-loop |
| `go func()` without context/defer cancel | Goroutine never cleaned up | Goroutine leak | ✓ partial — go-ast-goroutine-norecover; cancellation tracking deferred |

### Rust extras

| Rule | What | Bug class | Status |
|---|---|---|---|
| `expect()` with empty message | Panic with no context | Crash | ✓ poly-rust-expect-empty |
| `Arc<Mutex<T>>` where `&mut` suffices | Unnecessary heap allocation | Perf | deferred — needs borrow analysis |
| `async fn` with blocking I/O | `std::fs::read`, `thread::sleep` | Blocks executor | ✓ poly-rust-async-blocking |
| `unsafe` block without `// SAFETY:` comment | No invariants documented | Safety | ✓ covered — poly-rust-unsafe-block / rs-ast-unsafe flag every unsafe block |

### Java extras

| Rule | What | Bug class | Status |
|---|---|---|---|
| `System.out.println` in production | Use logger framework instead | Log noise | ✓ poly-debug-leftover (existing) |
| `catch (Exception e) {}` empty block | Exception silently swallowed | Swallowed errors | ✓ poly-empty-catch, java-ast-empty-catch (existing) |
| `==` on String objects | Reference equality, not value | Logic bug | ✓ poly-java-string-eq (literal comparisons) |

### C# extras

| Rule | What | Bug class | Status |
|---|---|---|---|
| `throw ex` instead of bare `throw` | Loses original stack trace | Debug hell | ✓ poly-cs-throw-ex |
| `async void` in non-event-handler | Unobserved exceptions crash process | Unobserved exc | ✓ poly-cs-async-void (can't exempt event handlers statically) |
| `ConfigureAwait(false)` missing in lib | Deadlock in sync-over-async | Deadlock | deferred — library-vs-app context judgment |

### C++ extras

| Rule | What | Bug class | Status |
|---|---|---|---|
| Missing virtual destructor on base class | Undefined behavior on delete | Memory leak | deferred — cpp AST pack candidate (needs class hierarchy) |
| `new` without smart pointer wrapper | No RAII, manual delete needed | Memory leak | ✓ poly-cpp-raw-new |
| Uninitialized member in constructor | Indeterminate value read | Undefined behavior | deferred — needs semantic analysis (clang-tidy covers) |

### C (standalone)

| Rule | What | Bug class | Status |
|---|---|---|---|
| `sprintf`/`strcpy`/`gets` unbounded | Use `snprintf`/`strncpy`/`fgets` | Buffer overflow | ✓ poly-unsafe-c, cpp-ast-unsafe-string (existing) |
| `malloc` return not NULL-checked | Dereference null pointer | Crash | ✓ poly-c-malloc-unchecked |
| `free()` without NULL-after | Double-free possible | Memory corrupt | deferred — style-level, FP-heavy |
| Return pointer to local variable | Dangling pointer | Undefined behavior | deferred — needs scope analysis (compilers warn) |

### Kotlin / Swift / PHP extras

| Rule | What | Bug class | Status |
|---|---|---|---|
| Kotlin: `!!` force unwrap on nullable | Equivalent to NPE | Runtime crash | ✓ poly-kotlin-notnull, kt-ast-notnull (existing) |
| Kotlin: `lateinit` before init check | Access before assignment | Runtime crash | ✓ kt-ast-lateinit (existing) |
| Swift: IUO `var x: T!` outside @IBOutlet | Hidden force-unwrap | Runtime crash | ✓ poly-swift-iuo |
| Swift: `try!` without do-catch | Crash on any error | Runtime crash | ✓ poly-swift-force, sw-ast-force-try (existing) |
| Swift: `unowned` where `weak` needed | Crash on nil access | Runtime crash | ✓ poly-swift-unowned (INFO) |
| PHP: `extract()` on `$_GET`/`$_POST` | Variable injection | Security | ✓ php-ast-extract (existing) |
| PHP: `eval()` on user-supplied string | Arbitrary code execution | Security | ✓ poly-dynamic-exec, php-ast-dynamic-exec (existing) |
| PHP: `==` instead of `===` | Loose comparison (`'0' == 0`) | Logic bug | ✓ poly-php-loose-eq |

### Angular

| Rule | What | Bug class | Status |
|---|---|---|---|
| `subscribe()` without `takeUntilDestroyed()` | Subscription never unsubscribes | Memory leak | deferred — multiline pipe chains defeat regex; FP storm on non-Angular RxJS |
| `effect()` with state mutation | Should use `computed()` | Logic bug | deferred — needs AST purity analysis |
| `setTimeout`/`setInterval` without clear | Fires after destroy | Memory leak | deferred — absence check across lifecycle methods |
| `@Input()` object mutated in-place | OnPush won't detect change | UI stale | deferred — needs data-flow analysis |

### NestJS

| Rule | What | Bug class | Status |
|---|---|---|---|
| Controller method missing `@UseGuards` | No auth on endpoint | Auth bypass | deferred — absence check, guard spellings vary |
| Circular dependency between modules | Detected from import graph | Architecture | ✓ covered — graph module detects cycles (Tarjan SCC) |

### React / TypeScript extras

| Rule | What | Bug class | Status |
|---|---|---|---|
| React: `useMemo` with static empty deps | Wrapping a constant — no benefit | Over-optimization | deferred — "constant" needs expression analysis |
| React: `useEffect` subscription no cleanup | No return function to unsubscribe | Memory leak | deferred — same as useEffect-cleanup above |
| TS: `as` unchecked type assertion | No runtime validation | Type safety | ✓ partial — poly-ts-any flags `as any`; general `as T` too noisy |
| TS: `enum` with numeric values | Enum values are numbers, not strings | Surprise | ✓ poly-ts-enum-numeric (INFO) |

### Zig

| Rule | What | Bug class | Status |
|---|---|---|---|
| `catch unreachable` on fallible op | Crashes with no message | Crash | ✓ poly-panic (existing; matches `catch unreachable`) |
| Missing `errdefer` after allocation | No cleanup on error path | Memory leak | deferred — absence check per error path |
| Allocator not passed as parameter | Hardcoded allocator | Testability | deferred — needs project-convention context |

### CSS/LESS/SASS

| Rule | What | Bug class | Status |
|---|---|---|---|
| `!important` usage | Specificity war indicator | Maintainability | ✓ poly-css-important (INFO; new css language spec) |
| `z-index` > 100 | Stacking context confusion | Maintainability | ✓ poly-css-high-zindex (INFO) |

### Universal / cross-language

| Rule | What | Bug class | Status |
|---|---|---|---|
| Empty catch block (any language) | Exception silently swallowed | Logic bug | ✓ poly-empty-catch + per-language AST variants (existing) |
| Hardcoded secrets (API keys, tokens) | Credentials in source control | Security | ✓ poly-secret, SEC3 (existing) |
| `TODO`/`FIXME`/`HACK` in production | Unfinished/known-broken code | Tech debt | ✓ poly-todo (existing) |
| `console.log` / `print` in production | Undisciplined logging | Log noise | ✓ poly-debug-leftover per language (existing) |

---

## Stats

| Dimension | Count |
|---|---|
| Polyglot regex rules | 76 PhD + 32 runtime |
| Polyglot AST rules | 72 across 9 languages |
| Python PhD rules | 55 |
| Linter integrations | 23 (incl. opt-in MegaLinter) across 17 languages |
| Graph languages | 10 (Python, JS/TS, Rust, Go, Java, C#, C/C++, Kotlin, Swift, PHP) |
| Total tests | 720 |

---

## Coverage by area

How many of the 21 languages cover each area, and which are missing:

| Area | Langs covered | Missing |
|---|---|---|
| Syntax | 20/21 | css |
| PhD | 19/21 | html, css (css is runtime-only by design) |
| Runtime | 20/21 | html |
| Wiring | 16/21 | cpp, haskell, sql, html, css |
| AST rules | 12/21 | ruby, dart, scala, elixir, zig, lua, haskell, sql, html, css |
| Linter | 20/21 | sql |
| Graph | 11/21 | ruby, dart, scala, elixir, zig, lua, haskell, sql, html, css |

**Python is the gold standard** — full coverage in every area with 55 deep PhD rules, deep wiring, and deep runtime. The gap (wiring, AST rules, graph) is languages that need cross-file structural analysis, not just regex/single-file — ~350 LOC per language to close.

