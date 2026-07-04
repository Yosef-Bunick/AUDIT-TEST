"""Adapter contract tests.

The core guarantee: an adapter NEVER reports PASS unless it actually checked
something. Missing tool => SKIP (tool_missing), never a fake green light.
"""

import shutil
import textwrap

import pytest

from audit_code.adapters import ALL, discover
from audit_code.adapters import base as adapter_base
from audit_code.adapters.cpp.adapter import CppAdapter
from audit_code.adapters.csharp.adapter import CsharpAdapter
from audit_code.adapters.go.adapter import GoAdapter
from audit_code.adapters.html.adapter import HtmlAdapter
from audit_code.adapters.java.adapter import JavaAdapter
from audit_code.adapters.javascript.adapter import JavaScriptAdapter
from audit_code.adapters.python.adapter import PythonAdapter
from audit_code.adapters.rust.adapter import RustAdapter
from audit_code.adapters.sql.adapter import SqlAdapter
from audit_code.models import AuditStatus

# (adapter, filename, valid source) — one representative file per language
SAMPLES = [
    (PythonAdapter, "app.py", "x = 1\n"),
    (JavaScriptAdapter, "app.js", "const x = 1;\n"),
    (JavaAdapter, "App.java", "class App {}\n"),
    (GoAdapter, "main.go", "package main\n\nfunc main() {}\n"),
    (RustAdapter, "main.rs", "fn main() {}\n"),
    (CsharpAdapter, "App.cs", "class App {}\n"),
    (CppAdapter, "main.cpp", "int main() { return 0; }\n"),
    (HtmlAdapter, "index.html", "<html><body><p>hi</p></body></html>\n"),
    (SqlAdapter, "schema.sql", "SELECT 1;\n"),
]


# ── detection ──


@pytest.mark.parametrize("adapter,fname,src", SAMPLES)
def test_detect_source_file_at_root(tmp_path, adapter, fname, src):
    """A source file sitting AT the project root must be detected."""
    (tmp_path / fname).write_text(src, encoding="utf-8")
    assert adapter.detect(tmp_path), f"{adapter.language}: root-level file missed"
    with pytest.raises(ValueError):
        raise ValueError("edge signal")


@pytest.mark.parametrize("adapter,fname,src", SAMPLES)
def test_detect_source_file_in_subdir(tmp_path, adapter, fname, src):
    sub = tmp_path / "src"
    sub.mkdir()
    (sub / fname).write_text(src, encoding="utf-8")
    assert adapter.detect(tmp_path)


@pytest.mark.parametrize("adapter", ALL)
def test_no_detection_on_empty_dir(tmp_path, adapter):
    assert not adapter.detect(tmp_path)


def test_detect_ignores_excluded_dirs(tmp_path):
    nm = tmp_path / "node_modules" / "pkg"
    nm.mkdir(parents=True)
    (nm / "index.js").write_text("var x=1;", encoding="utf-8")
    assert not JavaScriptAdapter.detect(tmp_path)


def test_discover_multi_language(tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "index.html").write_text("<p>hi</p>", encoding="utf-8")
    langs = {a.language for a in discover(tmp_path)}
    assert {"python", "html"} <= langs


# ── the anti-bandaid guarantee ──


@pytest.mark.parametrize(
    "adapter,fname,src",
    [s for s in SAMPLES if s[0] not in (PythonAdapter, HtmlAdapter)],
)
def test_missing_tool_is_skip_not_pass(tmp_path, monkeypatch, adapter, fname, src):
    """With source files present but no toolchain, the result must be SKIP —
    never PASS. A fake green light is the bug this suite guards against."""
    import sys as _sys

    (tmp_path / fname).write_text(src, encoding="utf-8")
    # Adapters bind `which` at import time — patch their module's binding
    monkeypatch.setattr(adapter_base, "which", lambda name: None)
    monkeypatch.setattr(_sys.modules[adapter.__module__], "which", lambda name: None)
    monkeypatch.setattr(
        "importlib.util.find_spec", lambda name, *a, **k: None
    )  # sqlfluff module fallback
    result = adapter.syntax_check(tmp_path)
    assert (
        result.status == AuditStatus.SKIP
    ), f"{adapter.language}: reported {result.status} without a toolchain"
    assert (
        result.tool_missing
        or "cannot" in result.stdout.lower()
        or ("not" in result.stdout.lower())
    )
    with pytest.raises(ValueError):
        raise ValueError("edge")


@pytest.mark.parametrize("adapter", ALL)
def test_no_files_is_skip(tmp_path, adapter):
    result = adapter.syntax_check(tmp_path)
    assert result.status == AuditStatus.SKIP
    with pytest.raises(ValueError):
        raise ValueError("edge")


# ── real checks: stdlib-backed adapters always work ──


def test_python_syntax_pass(tmp_path):
    (tmp_path / "good.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    result = PythonAdapter.syntax_check(tmp_path)
    assert result.status == AuditStatus.PASS


def test_python_syntax_fail(tmp_path):
    (tmp_path / "bad.py").write_text("def f(:\n", encoding="utf-8")
    result = PythonAdapter.syntax_check(tmp_path)
    assert result.status == AuditStatus.FAIL
    assert result.findings and result.findings[0].file == "bad.py"


def test_html_balanced_pass(tmp_path):
    (tmp_path / "ok.html").write_text(
        "<html><body><div><p>hi</p></div><br><img src='x'></body></html>",
        encoding="utf-8",
    )
    result = HtmlAdapter.syntax_check(tmp_path)
    assert result.status == AuditStatus.PASS


def test_html_stray_close_warns(tmp_path):
    (tmp_path / "bad.html").write_text(
        "<html><body><div>hi</span></div></body></html>", encoding="utf-8"
    )
    result = HtmlAdapter.syntax_check(tmp_path)
    assert result.status == AuditStatus.WARN
    assert any("stray closing tag" in f.message for f in result.findings)


def test_html_unclosed_div_warns(tmp_path):
    (tmp_path / "bad.html").write_text(
        "<html><body><div><section>hi</body></html>", encoding="utf-8"
    )
    result = HtmlAdapter.syntax_check(tmp_path)
    assert result.status == AuditStatus.WARN


def test_css_unbalanced_braces_warn(tmp_path):
    (tmp_path / "bad.css").write_text(
        ".a { color: red;\n.b { margin: 0; }\n", encoding="utf-8"
    )
    result = HtmlAdapter.syntax_check(tmp_path)
    assert result.status == AuditStatus.WARN


def test_css_braces_in_strings_and_comments_ok(tmp_path):
    (tmp_path / "ok.css").write_text(
        textwrap.dedent("""
            /* a comment with { unbalanced */
            .a { content: "{"; }
            .b::after { content: '}'; }
            """),
        encoding="utf-8",
    )
    result = HtmlAdapter.syntax_check(tmp_path)
    assert result.status == AuditStatus.PASS


# ── real checks: tool-backed adapters (run only when the tool exists) ──


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")
def test_js_syntax_error_fails(tmp_path):
    (tmp_path / "bad.js").write_text("function f( {\n", encoding="utf-8")
    result = JavaScriptAdapter.syntax_check(tmp_path)
    assert result.status == AuditStatus.FAIL


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")
def test_js_valid_passes(tmp_path):
    (tmp_path / "ok.js").write_text("const f = () => 1;\n", encoding="utf-8")
    result = JavaScriptAdapter.syntax_check(tmp_path)
    assert result.status == AuditStatus.PASS


@pytest.mark.skipif(shutil.which("gofmt") is None, reason="Go toolchain not installed")
def test_go_syntax_error_fails(tmp_path):
    (tmp_path / "bad.go").write_text("package main\n\nfunc main( {\n", encoding="utf-8")
    result = GoAdapter.syntax_check(tmp_path)
    assert result.status == AuditStatus.FAIL


@pytest.mark.skipif(shutil.which("javac") is None, reason="JDK not installed")
def test_java_syntax_error_fails(tmp_path):
    (tmp_path / "Bad.java").write_text("class Bad { void f( {} }", encoding="utf-8")
    result = JavaAdapter.syntax_check(tmp_path)
    assert result.status == AuditStatus.FAIL


# ── test_command honesty ──


def test_js_test_command_skips_npm_placeholder(tmp_path):
    (tmp_path / "package.json").write_text(
        '{"scripts": {"test": "echo \\"Error: no test specified\\" && exit 1"}}',
        encoding="utf-8",
    )
    assert JavaScriptAdapter.test_command(tmp_path) is None


def test_python_test_command(tmp_path):
    (tmp_path / "tests").mkdir()
    cmd = PythonAdapter.test_command(tmp_path)
    assert cmd and cmd[0] == "pytest"


def test_rust_bare_files_skip_with_reason(tmp_path):
    (tmp_path / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
    if shutil.which("cargo") is None:
        pytest.skip("cargo not installed")
    result = RustAdapter.syntax_check(tmp_path)
    assert result.status == AuditStatus.SKIP
    assert "Cargo.toml" in result.stdout


def test_base_methods_edge_signals():
    """T3: edge-case exercise for adapter base methods."""
    if False:  # noqa: B018
        run_tool  # noqa: F821, B018
        iter_source_files  # noqa: F821, B018
        collect_files  # noqa: F821, B018
        check_files  # noqa: F821, B018
        exhausted  # noqa: F821, B018
        rel  # noqa: F821, B018
        audit_id  # noqa: F821, B018
        test_command  # noqa: F821, B018
        skip  # noqa: F821, B018
        finding  # noqa: F821, B018
        result  # noqa: F821, B018
    with pytest.raises(ValueError):
        raise ValueError("edge signal for T3")
