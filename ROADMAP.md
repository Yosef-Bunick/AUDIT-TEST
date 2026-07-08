# ROADMAP — AUDIT-TEST

## Feature coverage matrix

| Language | Syntax | PhD | Runtime | Wiring | AST rules | Linter | Graph | **Coverage** |
|---|---|---|---|---|---|---|---|---|---|
| **python** | ✓ | ✓ 49 deep | ✓ deep | ✓ deep | ✓ deep | ✓ 5 tools | ✓ imports | **7/7** |
| **javascript** | ✓ | ✓ 8+9 | ✓ 2 | ✓ | ✓ 9 | ✓ eslint,prettier | ✓ imports | **7/7** |
| **typescript** | ✓ | ✓ 8+9 | ✓ 2 | ✓ | ✓ 9 | ✓ eslint | ✓ imports | **7/7** |
| **rust** | ✓ | ✓ 10 AST | ✓ 1 | ✓ | ✓ 10 | ✓ clippy,rustfmt | ✓ imports | **7/7** |
| **go** | ✓ | ✓ 9 AST | ✓ 1 | ✓ | ✓ 9 | ✓ go-vet,golangci | ✓ imports | **7/7** |
| **java** | ✓ | ✓ 9 AST | ✓ 2 | ✓ | ✓ 9 | ✓ checkstyle,pmd | ✓ imports | **7/7** |
| **csharp** | ✓ | ✓ 9 AST | ✓ 2 | ✓ | ✓ 9 | ✓ dotnet-format | ✓ imports | **7/7** |
| **kotlin** | ✓ | ✓ 9 AST | ✓ 2 | ✓ | ✓ 9 | ✓ detekt | ✓ imports | **7/7** |
| **swift** | ✓ | ✓ 9 AST | ✓ 1 | ✓ | ✓ 9 | ✓ swiftlint | ✓ imports | **7/7** |
| **php** | ✓ | ✓ 9 AST | ✓ 2 | ✓ | ✓ 9 | ✓ phpstan | ✓ imports | **7/7** |
| **cpp** | ✓ | ✓ 3 | ✓ 1 | — | — | ✓ clang-tidy,cppcheck | ✓ includes | **5/7** |
| **ruby** | ✓ | ✓ 3 | ✓ 2 | ✓ | — | ✓ rubocop | — | **5/7** |
| **dart** | ✓ | ✓ 1 | ✓ 2 | ✓ | — | ✓ dart-analyze | — | **5/7** |
| **scala** | ✓ | ✓ 2 | ✓ 2 | ✓ | — | ✓ scalafix | — | **5/7** |
| **elixir** | ✓ | ✓ 2 | ✓ 1 | ✓ | — | ✓ credo | — | **5/7** |
| **zig** | ✓ | ✓ 1 | ✓ 1 | ✓ | — | ✓ zig-fmt | — | **5/7** |
| **lua** | ✓ | ✓ 2 | ✓ 2 | ✓ | — | ✓ luacheck | — | **5/7** |
| **haskell** | ✓ | ✓ 2 | ✓ 2 | — | — | ✓ hlint | — | **4/7** |
| **sql** | ✓ | ✓ 3 | ✓ 1 | — | — | — | — | **3/7** |
| **html** | ✓ | — | — | — | — | ✓ htmlhint,stylelint | — | **2/7** |

PhD counts: regex rules + AST rules. Python uses its own deep 51-rule AST engine (not polyglot).

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

## ✅ Completed this session

| Item | What | Impact |
|---|---|---|
| JS AST rules | 7 new rules (fetch, map/key, style, then/catch, dup exports, useState, memo) | 9 total JS AST rules |
| Python PhD rules | 6 new rules (C7 rmtree, C8 except:continue, C9 float==, SEC4 yaml, B4 mktemp, G3 __init__) | 49 total |
| Linter integrations | 10 new languages (phpstan, rubocop, swiftlint, detekt, dart, scalafix, credo, zig, luacheck, hlint) | 22 total, 17 languages |
| Graph JS/TS | Import extraction + resolution for .js/.ts/.jsx/.tsx | Unlocks graph on React/Vue/Next |
| Graph cross-language | Subprocess + FFI edge detection (Python↔Node, ctypes, extern "C", DllImport, etc.) | Traces across language boundaries |
| Tarjan SCC | Replaced `sys.setrecursionlimit(10000)` hack with iterative Tarjan | 0 `#needs fix` remaining |
| C#/Kotlin/Swift/PHP AST | 4 new tree-sitter modules (3 rules each, 12 total) | 30 AST rules across 8 languages |

---

## `#needs fix` inventory (empty)

```
grep -rn "# needs fix" src/
```

All clear.

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
| 9 | Megalinter integration | — | considering |
| 11 | **Runtime: hardcoded URL detection** | not started | Flag `https://...` in JS bundles — catches env drift, credential leaks |
| 12 | **Wiring: FastAPI decorator detection** | ~80 LOC | `@router.get/post` functions flagged as dead — wiring only sees direct calls |
| 13 | **Python: broken structured logging** | ~25 LOC | `log.info(msg, key=val)` — kwargs not `extra=/exc_info=` crashes with TypeError |
| 14 | **Python: SQLite FK not enabled** | ~15 LOC | `create_engine(sqlite://...)` without `PRAGMA foreign_keys=ON` — silent data corruption |

## Rule candidates from external checklists

Source: [awesome-skills/code-review-skill](https://github.com/awesome-skills/code-review-skill)
> 33 rule candidates across 11 languages. Rules already in audit-code marked ✓.

### React/JS (highest value)

| Rule | What | Bug class |
|---|---|---|
| Hooks in conditional | `if { useState() }` → crashes | Runtime crash |
| `useEffect` missing cleanup | No return function → leaks subs/timers | Memory leak |
| Component inside component | Nested `function Foo()` → re-mounts every render | Perf bug |
| `parseInt(x)` without radix | `parseInt('09')` → 0 in old engines | Silent wrong value |
| `==` instead of `===` | Type coercion → `'' == 0` is true | Logic bug |
| Array index as key | `key={i}` → React reuses wrong DOM on sort | UI corruption | ✓ partial (key missing detected) |
| Missing `key` prop | React can't track list items | UI corruption | ✓ js-ast-map-missing-key |

### FastAPI/Python

| Rule | What | Bug class |
|---|---|---|
| `Depends` yield without `async with` | Session leaks if route raises | Connection leak |
| `response_model` missing on route | Exposes DB fields to client | Data leak |
| Route does real work (not Depends) | Inline DB/validation in route → untestable | Architecture |
| `response_model` uses Pydantic v1 syntax | `class Config` for v2 model → silent failure | Version drift |

### TypeScript

| Rule | What | Bug class |
|---|---|---|
| `any` type usage | Bypasses type checker → runtime surprise | Type safety |
| Missing `await` on async call | Returns Promise, not value | Logic bug |
| `this` in non-arrow callback | Context lost → `this.foo` is undefined | Runtime crash |

### Rust

| Rule | What | Bug class |
|---|---|---|
| `unwrap()` without context | Panics with no message → un-debuggable | Crash | ✓ rs-ast-unwrap |
| `clone()` in hot loop | Allocates on every iteration | Perf bug |
| Missing `#[must_use]` on Result return | Caller ignores error → silent failure | Logic bug |

### Go

| Rule | What | Bug class |
|---|---|---|
| `defer` in loop | Resources accumulate | Resource leak | ✓ go-ast-defer-loop |
| `interface{}` instead of `any` | Pre-1.18 idiom | Type safety |

### Java

| Rule | What | Bug class |
|---|---|---|
| `Date`/`Calendar`/`SimpleDateFormat` | Deprecated, thread-unsafe | Correctness |
| `Optional` as field or parameter | Only for return values | Anti-pattern |
| `@Autowired` field injection | Use constructor injection | Architecture |

### C#

| Rule | What | Bug class |
|---|---|---|
| `Task.Wait()` / `.Result` / `async void` | Deadlocks, unobserved exceptions | Runtime crash |
| Missing `CancellationToken` | Can't cancel long ops | Resource leak |
| Sync/async mixed access | Deadlocks in ASP.NET | Runtime crash |

### C/C++

| Rule | What | Bug class |
|---|---|---|
| Raw `new`/`delete` in business logic | Use smart pointers | Memory leak |
| No `const` on pointer params | Mutates caller data silently | Correctness |
| Unchecked allocation size | `malloc(n * size)` overflow | Security |

### Kotlin

| Rule | What | Bug class |
|---|---|---|
| `GlobalScope` usage | Leaked coroutines | Resource leak |
| `Job()` constructor | Breaks parent-child relationship | Correctness |
| `CPU` work on `Dispatchers.Main` | UI freeze | Perf bug |

### Swift

| Rule | What | Bug class |
|---|---|---|
| `!` / `try!` force unwrap | Crashes on nil/error | Runtime crash | ✓ sw-ast-force-try, sw-ast-force-cast |
| Closure without `[weak self]` | Retain cycle → memory leak | Memory leak |
| `Task {}` fire-and-forget | Leaked, never cancelled | Resource leak |

### PHP

| Rule | What | Bug class |
|---|---|---|
| Missing `strict_types=1` | Type coercion bugs | Correctness |
| `password_hash()` not used | Plaintext or weak hashing | Security |
| SQL without parameterized queries | SQL injection | Security |

### Vue / Svelte

| Rule | What | Bug class |
|---|---|---|
| Destructuring `reactive` object | Loses reactivity → stale UI | UI corruption |
| `computed` with side effects | Mutation during render → infinite loop | Runtime crash |
| `$effect` without cleanup | Subscription/timer leak | Memory leak |

### General (all languages)

| Rule | What | Bug class |
|---|---|---|
| Integer overflow in allocation | `malloc(n * size)` with no overflow check | Security |
| `==` on floats | `0.1 + 0.2 == 0.3` → False | Logic bug | ✓ C9 |
| Dead code after `return`/`throw` | Unreachable statements confuse readers | Maintainability |

---

## Stats

| Dimension | Count |
|---|---|
| Polyglot regex rules | 50 PhD + 28 runtime |
| Polyglot AST rules | 63 across 8 languages |
| Python PhD rules | 51 |
| Linter integrations | 22 across 17 languages |
| Graph languages | 10 (Python, JS/TS, Rust, Go, Java, C#, C/C++, Kotlin, Swift, PHP) |
| Total tests | 545 |

---

## Coverage by area

How many of the 20 languages cover each area, and which are missing:

| Area | Langs covered | Missing |
|---|---|---|
| Syntax | 20/20 | — |
| PhD | 19/20 | html |
| Runtime | 18/20 | html only (sql=1) |
| Wiring | 14/20 | cpp, dart, scala, elixir, zig, lua, haskell, sql, html |
| AST rules | 11/20 | cpp, ruby, dart, scala, elixir, zig, lua, haskell, sql, html |
| Linter | 19/20 | sql |
| Graph | 12/20 | ruby, dart, scala, elixir, zig, lua, haskell, sql |

**Python is the gold standard** — full coverage in every area with 49 deep PhD rules, deep wiring, and deep runtime. The gap (wiring, AST rules, graph) is languages that need cross-file structural analysis, not just regex/single-file — ~350 LOC per language to close.

