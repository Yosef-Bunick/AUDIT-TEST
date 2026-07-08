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
| 10 | Runtime audit expansion | — | memory, GPU, disk watchdogs |
| 11 | Decorator registration detection | — | FastAPI `@router` patterns |

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

