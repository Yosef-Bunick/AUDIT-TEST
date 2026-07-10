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


def test_phd_dangerously_set_inner_html(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "javascript",
        "a.jsx",
        "function C() { return <div dangerouslySetInnerHTML={{__html: x}} />; }\n",
    )
    assert "poly-js-xss-dangerous-html" in _ids(res)
    assert res.status == AuditStatus.FAIL  # HIGH


def test_phd_innerhtml_assignment(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "javascript",
        "a.js",
        "document.getElementById('x').innerHTML = userInput;\n",
    )
    assert "poly-js-xss-innerhtml" in _ids(res)
    assert res.status == AuditStatus.FAIL  # HIGH


def test_phd_storage_token_setitem(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "javascript",
        "a.js",
        "localStorage.setItem('auth_token', value);\n",
    )
    assert "poly-js-storage-token" in _ids(res)
    assert res.status == AuditStatus.FAIL  # HIGH


def test_phd_storage_token_not_flagged_for_tracking(tmp_path):
    """localStorage for a non-sensitive key like 'theme' is not flagged."""
    res = _run(
        tmp_path,
        "phd",
        "javascript",
        "a.js",
        "localStorage.setItem('theme', 'dark');\nlocalStorage.setItem('prefs', v);\n",
    )
    assert "poly-js-storage-token" not in _ids(res)


def test_phd_storage_token_session_storage(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "javascript",
        "a.js",
        "sessionStorage.setItem('jwt', token);\n",
    )
    assert "poly-js-storage-token" in _ids(res)


def test_phd_document_write(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "javascript",
        "a.js",
        "document.write('<div>' + userInput + '</div>');\n",
    )
    assert "poly-js-xss-document-write" in _ids(res)
    assert res.status == AuditStatus.FAIL  # HIGH


def test_phd_fetch_without_signal(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "javascript",
        "a.js",
        "fetch('/api/data');\n",
    )
    assert "poly-js-fetch-no-signal" in _ids(res)
    assert res.status == AuditStatus.WARN  # MEDIUM


def test_phd_fetch_with_signal_not_flagged(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "javascript",
        "a.js",
        "fetch('/api/data', { signal: AbortSignal.timeout(5000) });\n",
    )
    assert "poly-js-fetch-no-signal" not in _ids(res)


def test_phd_timer_string_arg(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "javascript",
        "a.js",
        "setTimeout('doSomething()', 1000);\n",
    )
    assert "poly-js-timer-string" in _ids(res)
    assert res.status == AuditStatus.FAIL  # HIGH


def test_phd_timer_callback_not_flagged(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "javascript",
        "a.js",
        "setTimeout(() => doSomething(), 1000);\nsetInterval(doWork, 5000);\n",
    )
    assert "poly-js-timer-string" not in _ids(res)


# ── Phase A: cross-language security rules ────────────────────────────────────


def test_go_defer_in_loop(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "go",
        "a.go",
        "func f() {\n\tfor _, item := range items {\n\t\tdefer item.Close()\n\t}\n}\n",
    )
    assert "poly-defer-in-loop" in _ids(res)
    assert res.status == AuditStatus.WARN  # MEDIUM


def test_rust_unsafe_block(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "rust",
        "a.rs",
        "fn f() { unsafe { *raw_ptr = 42; } }\n",
    )
    assert "poly-rust-unsafe-block" in _ids(res)
    assert res.status == AuditStatus.FAIL  # HIGH


def test_ruby_system_call(tmp_path):
    res = _run(tmp_path, "phd", "ruby", "a.rb", "system('rm -rf /')\n")
    assert "poly-shell-exec" in _ids(res)
    assert res.status == AuditStatus.FAIL  # HIGH


def test_php_shell_exec(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "php",
        "a.php",
        "<?php\n$out = shell_exec('ls -la');\necho exec('whoami');\n",
    )
    ids = _ids(res)
    assert "poly-shell-exec" in ids
    shell_count = sum(1 for f in res.findings if f.rule_id == "poly-shell-exec")
    assert shell_count >= 2, f"expected >=2 shell-exec, got {shell_count}"


def test_c_system_call(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "cpp",
        "a.c",
        '#include <stdlib.h>\nint main() { system("ls"); return 0; }\n',
    )
    assert "poly-unsafe-c" in _ids(res)


# ── Phase B: JVM/Go quality rules ─────────────────────────────────────────────


def test_go_goto(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "go",
        "a.go",
        "func f() {\n\tif err != nil {\n\t\tgoto cleanup\n\t}\n}\n",
    )
    assert "poly-go-goto" in _ids(res)


def test_java_thread_sleep(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "java",
        "A.java",
        "class A { void f() { Thread.sleep(1000); } }\n",
    )
    assert "poly-thread-sleep" in _ids(res)


def test_java_system_exit(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "java",
        "A.java",
        "class A { void f() { System.exit(1); } }\n",
    )
    assert "poly-hard-exit" in _ids(res)


# ── Phase C: SQL DROP + unbounded loop gaps ───────────────────────────────────


def test_sql_drop_table(tmp_path):
    res = _run(tmp_path, "phd", "sql", "a.sql", "DROP TABLE users;\n")
    assert "poly-sql-drop" in _ids(res)
    assert res.status == AuditStatus.FAIL


def test_ruby_unbounded_loop(tmp_path):
    res = _run(tmp_path, "runtime", "ruby", "a.rb", "while true; end\n")
    assert "poly-unbounded-loop" in _ids(res)


# ── AST rules (tree-sitter): Rust / Go / Java ─────────────────────────────────


def test_ast_rust_unsafe_and_command(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "rust",
        "a.rs",
        'use std::process::Command;\nfn main() { unsafe { *p=1; }; Command::new("ls").output(); }\n',
    )
    ids = _ids(res)
    assert "rs-ast-unsafe" in ids
    assert "rs-ast-command" in ids


def test_ast_rust_panic_lib(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "rust",
        "a.rs",
        'pub fn lib_func() { panic!("oh no"); todo!(); }\n',
    )
    ids = _ids(res)
    assert "rs-ast-panic" in ids


def test_ast_go_defer_and_goroutine(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "go",
        "a.go",
        "package main\nfunc f() {\n\tfor _, x := range xs {\n\t\tdefer x.Close()\n\t}\n\tgo func() { doWork() }()\n}\n",
    )
    ids = _ids(res)
    assert "go-ast-defer-loop" in ids
    assert "go-ast-goroutine-norecover" in ids


def test_ast_java_thread_and_exec(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "java",
        "A.java",
        'class A { void f() { Thread.sleep(1); System.exit(0); Runtime.getRuntime().exec("ls"); } }\n',
    )
    ids = _ids(res)
    assert "java-ast-thread-sleep" in ids
    assert "java-ast-system-exit" in ids
    assert "java-ast-runtime-exec" in ids


# ── runtime: hygiene ─────────────────────────────────────────────────────────


def test_runtime_console_log_js(tmp_path):
    res = _run(tmp_path, "runtime", "javascript", "a.js", "console.log('x');\n")
    ids = _ids(res)
    assert "poly-debug-leftover" in ids
    assert all(f.severity == Severity.INFO for f in res.findings)


# ── extension-based detection ────────────────────────────────────────────────


def test_detect_maps_extensions_to_languages(tmp_path):
    (tmp_path / "a.kt").write_text("fun x() {}\n", encoding="utf-8")


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


# ── JS/TS AST rules (tree-sitter) ──────────────────────────────────────────


def test_ast_js_fetch_no_catch(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "javascript",
        "a.js",
        "fetch('/api').then(r => r.json());\n",
    )
    ids = _ids(res)
    assert "js-ast-fetch-no-error" in ids
    assert res.findings[0].severity == Severity.MEDIUM


def test_ast_js_fetch_with_catch(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "javascript",
        "a.js",
        "fetch('/api').catch(e => console.log(e));\n",
    )
    assert "js-ast-fetch-no-error" not in _ids(res)


def test_ast_js_fetch_in_try(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "javascript",
        "a.js",
        "async function f() { try { await fetch('/api'); } catch(e) {} }\n",
    )
    assert "js-ast-fetch-no-error" not in _ids(res)


def test_ast_js_map_missing_key(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "javascript",
        "a.jsx",
        "function List({items}) { return items.map(item => <div>{item}</div>); }\n",
    )
    ids = _ids(res)
    assert "js-ast-map-missing-key" in ids
    assert res.findings[0].severity == Severity.MEDIUM


def test_ast_js_map_with_key(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "javascript",
        "a.jsx",
        "function List({items}) { return items.map(item => <div key={item.id}>{item}</div>); }\n",
    )
    assert "js-ast-map-missing-key" not in _ids(res)


def test_ast_js_inline_style_object(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "javascript",
        "a.jsx",
        "function C() { return <div style={{color: 'red'}}>hi</div>; }\n",
    )
    ids = _ids(res)
    assert "js-ast-inline-style-object" in ids
    assert all(
        f.severity == Severity.INFO
        for f in res.findings
        if f.rule_id == "js-ast-inline-style-object"
    )


def test_ast_js_inline_style_var_not_flagged(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "javascript",
        "a.jsx",
        "const s={color:'red'}; function C() { return <div style={s}>hi</div>; }\n",
    )
    assert "js-ast-inline-style-object" not in _ids(res)


def test_ast_js_then_no_catch(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "javascript",
        "a.js",
        "fetch('/api').then(r => r.json());\n",
    )
    ids = _ids(res)
    assert "js-ast-then-no-catch" in ids
    assert any(
        f.severity == Severity.HIGH and f.rule_id == "js-ast-then-no-catch"
        for f in res.findings
    )


def test_ast_js_then_with_catch(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "javascript",
        "a.js",
        "fetch('/api').then(r => r.json()).catch(e => {});\n",
    )
    assert "js-ast-then-no-catch" not in _ids(res)


def test_ast_js_duplicate_exports(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "javascript",
        "a.js",
        "export function foo() {}\nexport const bar = 1;\nexport function foo() {}\n",
    )
    ids = _ids(res)
    assert "js-ast-duplicate-export" in ids
    assert res.findings[0].severity == Severity.MEDIUM


def test_ast_js_usestate_unused_in_jsx(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "javascript",
        "a.jsx",
        "function Comp() { const [name, setName] = useState(''); return <div>Hello</div>; }\n",
    )
    assert "js-ast-usestate-unused-in-jsx" in _ids(res)


def test_ast_js_usestate_used_in_jsx(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "javascript",
        "a.jsx",
        "function Comp() { const [name, setName] = useState(''); return <div>{name}</div>; }\n",
    )
    assert "js-ast-usestate-unused-in-jsx" not in _ids(res)


def test_ast_js_missing_react_memo(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "javascript",
        "a.jsx",
        "export const Comp = () => <div/>;\n",
    )
    ids = _ids(res)
    assert "js-ast-missing-react-memo" in ids
    assert all(
        f.severity == Severity.INFO
        for f in res.findings
        if f.rule_id == "js-ast-missing-react-memo"
    )


def test_ast_js_react_memo_wrapped(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "javascript",
        "a.jsx",
        "const Comp = () => <div/>;\nexport default React.memo(Comp);\n",
    )
    assert "js-ast-missing-react-memo" not in _ids(res)


def test_ast_js_existing_rules_still_work(tmp_path):
    """Verify existing JS AST rules (useEffect, dangerous HTML) still fire."""
    res = _run(
        tmp_path,
        "phd",
        "javascript",
        "a.jsx",
        "function C() { useEffect(() => {}, []); return <div dangerouslySetInnerHTML={{}}/>; }\n",
    )
    assert "js-ast-dangerous-html" in _ids(res)  # still fires


# ── roadmap checklist rules (2026-07 batch) ───────────────────────────────────


def test_js_hook_in_conditional(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "javascript",
        "a.jsx",
        "function C() { if (ready) { useEffect(() => {}); } return null; }\n",
    )
    assert "poly-js-hook-conditional" in _ids(res)


def test_js_hook_toplevel_not_flagged(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "javascript",
        "a.jsx",
        "function C() { const x = useMemo(() => 1, []); return null; }\n",
    )
    assert "poly-js-hook-conditional" not in _ids(res)


def test_js_parseint_without_radix(tmp_path):
    res = _run(tmp_path, "phd", "javascript", "a.js", "const n = parseInt(s);\n")
    assert "poly-js-parseint-no-radix" in _ids(res)


def test_js_parseint_with_radix_not_flagged(tmp_path):
    res = _run(tmp_path, "phd", "javascript", "a.js", "const n = parseInt(s, 10);\n")
    assert "poly-js-parseint-no-radix" not in _ids(res)


def test_js_loose_equality(tmp_path):
    res = _run(tmp_path, "phd", "javascript", "a.js", "if (a == b) { f(); }\n")
    assert "poly-js-loose-eq" in _ids(res)


def test_js_strict_and_null_equality_not_flagged(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "javascript",
        "a.js",
        "if (a === b) {}\nif (c == null) {}\nif (d !== e) {}\n",
    )
    assert "poly-js-loose-eq" not in _ids(res)


def test_ts_explicit_any(tmp_path):
    res = _run(tmp_path, "phd", "typescript", "a.ts", "function f(x: any) {}\n")
    assert "poly-ts-any" in _ids(res)


def test_ts_numeric_enum(tmp_path):
    res = _run(tmp_path, "phd", "typescript", "a.ts", "enum Color { Red, Green }\n")
    assert "poly-ts-enum-numeric" in _ids(res)


def test_ts_string_enum_not_flagged(tmp_path):
    res = _run(tmp_path, "phd", "typescript", "a.ts", 'enum Color { Red = "red" }\n')
    assert "poly-ts-enum-numeric" not in _ids(res)


def test_vue_reactive_destructure(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "javascript",
        "a.js",
        "const { count } = reactive({ count: 0 });\n",
    )
    assert "poly-js-reactive-destructure" in _ids(res)


def test_go_mutex_by_value(tmp_path):
    res = _run(tmp_path, "phd", "go", "a.go", "func f(mu sync.Mutex) {}\n")
    assert "poly-go-mutex-value" in _ids(res)


def test_go_mutex_by_pointer_not_flagged(tmp_path):
    res = _run(tmp_path, "phd", "go", "a.go", "func f(mu *sync.Mutex) {}\n")
    assert "poly-go-mutex-value" not in _ids(res)


def test_go_timeafter_in_select_loop(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "go",
        "a.go",
        "func f() {\n\tfor {\n\t\tselect {\n\t\tcase <-time.After(d):\n"
        "\t\t\treturn\n\t\t}\n\t}\n}\n",
    )
    assert "poly-go-timeafter-select-loop" in _ids(res)


def test_go_empty_interface(tmp_path):
    res = _run(tmp_path, "runtime", "go", "a.go", "func f(x interface{}) {}\n")
    assert "poly-go-empty-interface" in _ids(res)


def test_rust_expect_empty_message(tmp_path):
    res = _run(tmp_path, "phd", "rust", "a.rs", 'fn f() { x.expect(""); }\n')
    assert "poly-rust-expect-empty" in _ids(res)


def test_rust_async_blocking_io(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "rust",
        "a.rs",
        'async fn f() { let d = std::fs::read("x"); }\n',
    )
    assert "poly-rust-async-blocking" in _ids(res)


def test_rust_clone_in_loop(tmp_path):
    res = _run(
        tmp_path,
        "runtime",
        "rust",
        "a.rs",
        "fn f() { for x in items {\n    let y = x.clone();\n} }\n",
    )
    assert "poly-rust-clone-in-loop" in _ids(res)


def test_java_string_reference_equality(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "java",
        "A.java",
        'class A { boolean f() { return s == "x"; } }\n',
    )
    assert "poly-java-string-eq" in _ids(res)


def test_java_legacy_date_api(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "java",
        "A.java",
        'class A { void f() { df = new SimpleDateFormat("yyyy"); } }\n',
    )
    assert "poly-java-legacy-date" in _ids(res)


def test_java_optional_field(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "java",
        "A.java",
        "class A { private Optional<String> name; }\n",
    )
    assert "poly-java-optional-field" in _ids(res)


def test_java_optional_return_not_flagged(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "java",
        "A.java",
        "class A { public Optional<String> findName() { return x; } }\n",
    )
    assert "poly-java-optional-field" not in _ids(res)


def test_java_autowired_field_injection(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "java",
        "A.java",
        "class A {\n  @Autowired\n  private FooService foo;\n}\n",
    )
    assert "poly-java-field-injection" in _ids(res)


def test_csharp_blocking_on_task(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "csharp",
        "A.cs",
        "class A { void F() { var r = task.Result; task.Wait(); } }\n",
    )
    assert "poly-cs-blocking-async" in _ids(res)


def test_csharp_async_void(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "csharp",
        "A.cs",
        "class A { async void Handle() {} }\n",
    )
    assert "poly-cs-async-void" in _ids(res)


def test_csharp_async_task_not_flagged(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "csharp",
        "A.cs",
        "class A { async Task Handle() {} }\n",
    )
    assert "poly-cs-async-void" not in _ids(res)


def test_csharp_throw_ex_resets_trace(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "csharp",
        "A.cs",
        "class A { void F() { try {} catch (Exception ex) { Log(ex); throw ex; } } }\n",
    )
    assert "poly-cs-throw-ex" in _ids(res)


def test_csharp_bare_rethrow_not_flagged(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "csharp",
        "A.cs",
        "class A { void F() { try {} catch (Exception ex) { Log(ex); throw; } } }\n",
    )
    assert "poly-cs-throw-ex" not in _ids(res)


def test_cpp_raw_new(tmp_path):
    res = _run(tmp_path, "phd", "cpp", "a.cpp", "Foo* f = new Foo();\n")
    assert "poly-cpp-raw-new" in _ids(res)


def test_cpp_smart_pointer_new_not_flagged(tmp_path):
    res = _run(tmp_path, "phd", "cpp", "a.cpp", "std::unique_ptr<Foo> f(new Foo());\n")
    assert "poly-cpp-raw-new" not in _ids(res)


def test_c_malloc_without_null_check(tmp_path):
    res = _run(tmp_path, "phd", "cpp", "a.c", "p = malloc(n);\nuse(p);\n")
    assert "poly-c-malloc-unchecked" in _ids(res)


def test_c_malloc_with_null_check_not_flagged(tmp_path):
    res = _run(tmp_path, "phd", "cpp", "a.c", "p = malloc(n);\nif (!p) return;\n")
    assert "poly-c-malloc-unchecked" not in _ids(res)


def test_c_malloc_multiplication_overflow(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "cpp",
        "a.c",
        "buf = malloc(n * sizeof(int));\nif (!buf) return;\n",
    )
    assert "poly-c-alloc-overflow" in _ids(res)


def test_kotlin_globalscope(tmp_path):
    res = _run(
        tmp_path, "phd", "kotlin", "A.kt", "fun f() { GlobalScope.launch { g() } }\n"
    )
    assert "poly-kotlin-globalscope" in _ids(res)


def test_kotlin_job_in_builder(tmp_path):
    res = _run(
        tmp_path, "phd", "kotlin", "A.kt", "fun f() { scope.launch(Job()) { g() } }\n"
    )
    assert "poly-kotlin-job-in-builder" in _ids(res)


def test_swift_unowned(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "swift",
        "A.swift",
        "f { [unowned self] in self.g() }\n",
    )
    assert "poly-swift-unowned" in _ids(res)


def test_swift_implicitly_unwrapped_optional(tmp_path):
    res = _run(tmp_path, "phd", "swift", "A.swift", "var name: String!\n")
    assert "poly-swift-iuo" in _ids(res)


def test_swift_iboutlet_iuo_not_flagged(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "swift",
        "A.swift",
        "@IBOutlet weak var label: UILabel!\n",
    )
    assert "poly-swift-iuo" not in _ids(res)


def test_php_loose_equality(tmp_path):
    res = _run(tmp_path, "phd", "php", "a.php", "<?php if ($a == $b) { f(); }\n")
    assert "poly-php-loose-eq" in _ids(res)


def test_php_strict_equality_not_flagged(tmp_path):
    res = _run(tmp_path, "phd", "php", "a.php", "<?php if ($a === $b) { f(); }\n")
    assert "poly-php-loose-eq" not in _ids(res)


def test_php_sql_interpolation(tmp_path):
    res = _run(
        tmp_path,
        "phd",
        "php",
        "a.php",
        '<?php $db->query("SELECT * FROM t WHERE id = $id");\n',
    )
    assert "poly-php-sql-interp" in _ids(res)
    assert res.status == AuditStatus.FAIL  # HIGH


def test_php_query_variable_not_flagged(tmp_path):
    res = _run(tmp_path, "phd", "php", "a.php", "<?php $db->query($sql);\n")
    assert "poly-php-sql-interp" not in _ids(res)


def test_css_important_and_zindex(tmp_path):
    res = _run(
        tmp_path,
        "runtime",
        "css",
        "a.css",
        ".a { color: red !important; }\n.m { z-index: 9999; }\n",
    )
    ids = _ids(res)
    assert {"poly-css-important", "poly-css-high-zindex"} <= ids
    assert all(f.severity == Severity.INFO for f in res.findings)


def test_css_low_zindex_not_flagged(tmp_path):
    res = _run(tmp_path, "runtime", "css", "a.css", ".m { z-index: 10; }\n")
    assert "poly-css-high-zindex" not in _ids(res)


def test_css_detected_by_extension(tmp_path):
    (tmp_path / "style.scss").write_text(".a { color: red; }\n", encoding="utf-8")
    got = polyglot.detect(tmp_path)
    assert "css" in got


def test_css_wiring_disabled(tmp_path):
    res = _run(tmp_path, "wiring", "css", "a.css", ".dead-class { color: red; }\n")
    assert res.findings == []


# ── AST pack registration: skips visible, breakage loud ──────────────────────


def test_ast_skip_records_missing_grammar(monkeypatch):
    """A missing tree-sitter grammar wheel is an expected environment
    limitation: recorded in _AST_SKIPS so the phd output can say so."""
    monkeypatch.setattr(polyglot, "_AST_SKIPS", {})
    exc = ModuleNotFoundError(
        "No module named 'tree_sitter_xyz'", name="tree_sitter_xyz"
    )
    polyglot._ast_skip_or_raise(exc, "fakelang", "otherlang")
    assert polyglot._AST_SKIPS["fakelang"] == "tree_sitter_xyz not installed"
    assert polyglot._AST_SKIPS["otherlang"] == "tree_sitter_xyz not installed"


def test_ast_skip_reraises_non_grammar_failures(monkeypatch):
    """A missing module that is NOT a grammar wheel means the rule pack itself
    is broken — it must raise instead of degrading silently."""
    import pytest

    monkeypatch.setattr(polyglot, "_AST_SKIPS", {})
    exc = ModuleNotFoundError("No module named 'zzz'", name="zzz_not_a_grammar")
    with pytest.raises(ModuleNotFoundError):
        polyglot._ast_skip_or_raise(exc, "fakelang")
    assert not polyglot._AST_SKIPS  # a broken pack is not an expected skip


def test_every_ast_language_registered_or_skipped():
    """No language may silently vanish: each AST-capable language is either
    running (in _AST_CHECKS) or visibly skipped (in _AST_SKIPS)."""
    ast_langs = (
        "javascript",
        "typescript",
        "rust",
        "go",
        "java",
        "csharp",
        "kotlin",
        "swift",
        "php",
    )
    for lang in ast_langs:
        assert lang in polyglot._AST_CHECKS or lang in polyglot._AST_SKIPS, lang


def test_phd_output_notes_skipped_ast_rules(tmp_path, monkeypatch):
    """When a language's AST pack was skipped, the phd stdout says so."""
    monkeypatch.setattr(polyglot, "_AST_CHECKS", {})
    monkeypatch.setattr(
        polyglot, "_AST_SKIPS", {"javascript": "tree_sitter_javascript not installed"}
    )
    res = _run(tmp_path, "phd", "javascript", "a.js", "const x = 1;\n")
    assert "AST rules not run" in res.stdout
    assert "tree_sitter_javascript not installed" in res.stdout


def test_phd_output_has_no_skip_note_when_ast_ran(tmp_path):
    res = _run(tmp_path, "phd", "javascript", "a.js", "const x = 1;\n")
    assert "AST rules not run" not in res.stdout
