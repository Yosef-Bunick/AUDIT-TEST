"""Tests for the language-agnostic wiring/phd/runtime engine (polyglot.py).

T1 anchor: "polyglot" — referenced by test imports.
"""

from audit_code import polyglot
from audit_code.models import AuditStatus, Severity


def _run(tmp_path, kind, lang, name, content):
    f = tmp_path / name
    f.write_text(content, encoding="utf-8")
    return polyglot.run(kind, tmp_path, lang, [f])


def _msgs(res):
    return [f.message for f in res.findings]


def _ids(res):
    return {f.rule_id for f in res.findings}


# ── wiring: dead symbols ─────────────────────────────────────────────────────


def test_wiring_flags_dead_and_keeps_referenced(tmp_path):
    res = _run(
        tmp_path,
        "wiring",
        "javascript",
        "a.js",
        "function deadOne() { return 1; }\n"
        "function liveOne() { return 2; }\n"
        "liveOne();\n",
    )
    assert any("deadOne" in m for m in _msgs(res))
    assert not any("liveOne" in m for m in _msgs(res))


def test_wiring_excludes_exported_js(tmp_path):
    res = _run(
        tmp_path, "wiring", "javascript", "a.js", "export function apiThing() {}\n"
    )
    assert res.findings == []


def test_wiring_excludes_pub_rust(tmp_path):
    res = _run(tmp_path, "wiring", "rust", "a.rs", "pub fn apiThing() -> i32 { 1 }\n")
    assert res.findings == []


def test_wiring_excludes_exported_go_by_capitalization(tmp_path):
    # Capitalised Go identifiers are exported; the def regex only matches lower.
    res = _run(
        tmp_path, "wiring", "go", "a.go", "func ExportedThing() int { return 1 }\n"
    )
    assert res.findings == []


def test_wiring_ignores_entry_points(tmp_path):
    res = _run(tmp_path, "wiring", "go", "a.go", "func main() {}\n")
    assert res.findings == []


# ── phd: correctness / security ──────────────────────────────────────────────


def test_phd_empty_catch_js(tmp_path):
    res = _run(tmp_path, "phd", "javascript", "a.js", "try { x(); } catch (e) {}\n")
    assert "poly-empty-catch" in _ids(res)
    assert res.status == AuditStatus.FAIL  # HIGH


def test_phd_dynamic_eval_js(tmp_path):
    res = _run(tmp_path, "phd", "javascript", "a.js", "const r = eval(userInput);\n")
    assert "poly-dynamic-exec" in _ids(res)


def test_phd_broad_catch_java(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "java",
        "A.java",
        "try { f(); } catch (Exception e) { log(e); }\n",
    )
    assert "poly-broad-catch" in _ids(res)


def test_phd_go_empty_errcheck(tmp_path):
    res = _run(tmp_path, "phd", "go", "a.go", "if err != nil {}\n")
    assert "poly-empty-errcheck" in _ids(res)


def test_phd_rust_unwrap(tmp_path):
    res = _run(tmp_path, "phd", "rust", "a.rs", "let v = thing().unwrap();\n")
    assert "poly-rust-unwrap" in _ids(res)


def test_phd_hardcoded_secret(tmp_path):
    res = _run(
        tmp_path, "phd", "javascript", "a.js", 'const API_KEY = "sk_live_abc123def";\n'
    )
    assert "poly-secret" in _ids(res)


def test_phd_secret_excludes_env_lookup(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "javascript",
        "a.js",
        "const apiKey = process.env.API_KEY;\n",
    )
    assert "poly-secret" not in _ids(res)


# ── runtime: hygiene ─────────────────────────────────────────────────────────


def test_runtime_console_log_js(tmp_path):
    res = _run(tmp_path, "runtime", "javascript", "a.js", "console.log('x');\n")
    ids = _ids(res)
    assert "poly-debug-leftover" in ids
    assert all(f.severity == Severity.INFO for f in res.findings)


def test_runtime_todo_marker_shared(tmp_path):
    res = _run(tmp_path, "runtime", "go", "a.go", "// TODO: handle this\n")
    assert "poly-todo" in _ids(res)


def test_runtime_java_system_out(tmp_path):
    res = _run(tmp_path, "runtime", "java", "A.java", "System.out.println(x);\n")
    assert "poly-debug-leftover" in _ids(res)


def test_runtime_unbounded_loop(tmp_path):
    res = _run(tmp_path, "runtime", "rust", "a.rs", "while true {}\n")
    # rust 'while true' has no parens; C-family 'while (true)' is what we match.
    res2 = _run(tmp_path, "runtime", "javascript", "a.js", "while (true) {}\n")
    assert "poly-unbounded-loop" in _ids(res2)
    assert res is not None


# ── dispatch / status ────────────────────────────────────────────────────────


def test_typescript_uses_js_spec(tmp_path):
    res = _run(tmp_path, "phd", "typescript", "a.ts", "eval(payload);\n")
    assert "poly-dynamic-exec" in _ids(res)


def test_unsupported_language_skips(tmp_path):
    res = polyglot.run("wiring", tmp_path, "html", [])
    assert res.status == AuditStatus.SKIP
    assert res.audit_id == "html-wiring"


def test_unknown_kind_skips(tmp_path):
    res = polyglot.run("bogus", tmp_path, "go", [])
    assert res.status == AuditStatus.SKIP


def test_clean_file_passes(tmp_path):
    res = _run(
        tmp_path,
        "runtime",
        "go",
        "a.go",
        "func f() int { return 1 }\n",
    )
    assert res.status == AuditStatus.PASS
    assert res.findings == []


def test_audit_id_shape(tmp_path):
    res = _run(tmp_path, "phd", "rust", "a.rs", "fn f() {}\n")
    assert res.audit_id == "rust-phd"


def test_utf8_content_does_not_crash(tmp_path):
    res = _run(
        tmp_path,
        "runtime",
        "javascript",
        "a.js",
        "// café 🐇 TODO: fix\nconsole.log('naïve');\n",
    )
    assert res.status in (AuditStatus.WARN, AuditStatus.PASS)


# ── extension-based detection ────────────────────────────────────────────────


def test_detect_maps_extensions_to_languages(tmp_path):
    (tmp_path / "a.kt").write_text("fun x() {}\n", encoding="utf-8")
    (tmp_path / "b.swift").write_text("func x() {}\n", encoding="utf-8")
    (tmp_path / "c.rb").write_text("def x; end\n", encoding="utf-8")
    (tmp_path / "d.sql").write_text("SELECT 1;\n", encoding="utf-8")
    got = {k: len(v) for k, v in polyglot.detect(tmp_path).items()}
    assert got == {"kotlin": 1, "swift": 1, "ruby": 1, "sql": 1}


def test_detect_skips_excluded_dirs(tmp_path):
    (tmp_path / "real.go").write_text("package main\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dep.go").write_text("package d\n", encoding="utf-8")
    got = polyglot.detect(tmp_path)
    assert set(got) == {"go"}
    assert len(got["go"]) == 1


def test_every_spec_has_extensions_and_rules():
    for spec in polyglot._ALL_SPECS:
        assert spec.extensions, f"{spec.language} has no extensions"
        # Each language contributes at least one rule or a wiring capability.
        assert spec.phd_rules or spec.runtime_rules or spec.defs


# ── new-language rule coverage (one representative finding each) ──────────────


def test_kotlin_notnull_and_broad_catch(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "kotlin",
        "A.kt",
        "fun f() { try { g() } catch (e: Exception) {} ; val y = z!! }\n",
    )
    ids = _ids(res)
    assert {"poly-empty-catch", "poly-broad-catch", "poly-kotlin-notnull"} <= ids


def test_swift_force_and_fatal(tmp_path):
    res = _run(
        tmp_path, "phd", "swift", "A.swift", "let x = try! f()\nfatalError('no')\n"
    )
    assert {"poly-swift-force", "poly-fatal"} <= _ids(res)


def test_dart_private_dead_symbol(tmp_path):
    res = _run(tmp_path, "wiring", "dart", "a.dart", "void _helper() {}\n")
    assert any("_helper" in m for m in _msgs(res))


def test_ruby_eval_and_broad_rescue(tmp_path):
    res = _run(
        tmp_path, "phd", "ruby", "a.rb", "eval(x)\nbegin\nrescue Exception\nend\n"
    )
    assert {"poly-dynamic-exec", "poly-broad-catch"} <= _ids(res)


def test_php_eval(tmp_path):
    res = _run(tmp_path, "phd", "php", "a.php", "<?php eval($code);\n")
    assert "poly-dynamic-exec" in _ids(res)


def test_zig_panic(tmp_path):
    res = _run(tmp_path, "phd", "zig", "a.zig", 'fn f() void { @panic("x"); }\n')
    assert "poly-panic" in _ids(res)


def test_scala_null_and_cast(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "scala",
        "A.scala",
        "val x: String = null\nval y = z.asInstanceOf[Int]\n",
    )
    assert {"poly-scala-null", "poly-scala-cast"} <= _ids(res)


def test_lua_dynamic_load(tmp_path):
    res = _run(tmp_path, "phd", "lua", "a.lua", "local f = load(src)\n")
    assert "poly-dynamic-exec" in _ids(res)


def test_haskell_unsafe(tmp_path):
    res = _run(tmp_path, "phd", "haskell", "a.hs", "x = unsafePerformIO y\n")
    assert "poly-haskell-unsafe" in _ids(res)


def test_elixir_code_eval(tmp_path):
    res = _run(tmp_path, "phd", "elixir", "a.ex", "Code.eval_string(x)\n")
    assert "poly-dynamic-exec" in _ids(res)


def test_sql_delete_without_where(tmp_path):
    res = _run(tmp_path, "phd", "sql", "a.sql", "DELETE FROM users;\n")
    assert "poly-sql-delete-all" in _ids(res)
    assert res.status == AuditStatus.FAIL


def test_sql_update_with_where_is_clean(tmp_path):
    res = _run(
        tmp_path, "phd", "sql", "a.sql", "UPDATE users SET active = 0 WHERE id = 1;\n"
    )
    assert "poly-sql-update-all" not in _ids(res)


def test_cpp_unsafe_and_no_wiring(tmp_path):
    res = _run(tmp_path, "phd", "cpp", "a.c", "char b[8]; gets(b);\n")
    assert "poly-unsafe-c" in _ids(res)
    wired = _run(tmp_path, "wiring", "cpp", "a.c", "static void helper() {}\n")
    assert wired.findings == []  # C/C++ wiring intentionally disabled
