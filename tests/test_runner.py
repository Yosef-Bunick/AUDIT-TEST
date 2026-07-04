"""Runner + wiring integration tests — the manual verifications from the
adapter overhaul, encoded so they keep passing.

Covers: end-to-end run_suite on broken vs clean multi-language projects,
the [audit].languages config filter, the python-audits SKIP row, native
test-suite execution rows, JSON report contents, the cp1252 console fix,
and the wiring framework-callback heuristic.
"""

import ast
import io
import json
import sys
from pathlib import Path

import pytest

from audit_code import cli, quality, runner
from audit_code.adapters.python.adapter import PythonAdapter
from audit_code.models import AuditStatus
from audit_code.reporting import json_report

REPO_ROOT = Path(__file__).resolve().parent.parent


def _no_external_tools(monkeypatch):
    """Make the quality audit skip every external tool (black/ruff/mypy/
    pip-audit) so e2e tests stay fast and offline; Q0 (ast syntax) still runs."""
    monkeypatch.setattr(quality, "_tool", lambda name, root: None)


# ── end-to-end: run_suite on real (tmp) projects ──


def test_run_suite_broken_multilang_fails(tmp_path, monkeypatch, capsys):
    """Broken Python + broken HTML: syntax rows must FAIL/WARN and the
    failure must propagate (this is the fail-closed contract)."""
    _no_external_tools(monkeypatch)
    (tmp_path / "broken.py").write_text("def f(:\n", encoding="utf-8")
    (tmp_path / "broken.html").write_text(
        "<html><body><div>hi</span></body></html>", encoding="utf-8"
    )

    results = runner.run_suite(tmp_path, mode="min")
    by_id = {r.audit_id: r for r in results}

    assert by_id["python-syntax"].status == AuditStatus.FAIL
    assert by_id["python-syntax"].findings[0].file == "broken.py"
    assert by_id["html-syntax"].status == AuditStatus.WARN
    assert by_id["quality"].is_failure  # Q0 catches broken.py without tools
    assert any(r.is_failure for r in results)


def test_run_suite_clean_multilang_passes(tmp_path, monkeypatch, capsys):
    _no_external_tools(monkeypatch)
    (tmp_path / "clean.py").write_text("X = 1\n", encoding="utf-8")
    (tmp_path / "index.html").write_text(
        "<html><body><p>hi</p></body></html>", encoding="utf-8"
    )

    results = runner.run_suite(tmp_path, mode="min")
    by_id = {r.audit_id: r for r in results}

    assert by_id["python-syntax"].status == AuditStatus.PASS
    assert by_id["html-syntax"].status == AuditStatus.PASS
    assert not any(r.is_failure for r in results)


def test_run_suite_no_python_emits_skip_row(tmp_path, capsys):
    """A Python-less project must report one honest SKIP row for the five
    Python audits instead of vacuous passes."""
    (tmp_path / "index.html").write_text("<p>hi</p>", encoding="utf-8")

    results = runner.run_suite(tmp_path, mode="min")
    by_id = {r.audit_id: r for r in results}

    assert by_id["html-syntax"].status == AuditStatus.PASS
    assert by_id["python-audits"].status == AuditStatus.SKIP
    assert "no Python detected" in by_id["python-audits"].stdout
    assert not any(r.is_failure for r in results)


def test_language_filter_from_config(tmp_path, capsys):
    """[audit] languages restricts detection; a filtered-out Python project
    must not run the Python audit stack."""
    (tmp_path / "app.py").write_text("X = 1\n", encoding="utf-8")
    (tmp_path / "index.html").write_text("<p>hi</p>", encoding="utf-8")

    results = runner.run_suite(
        tmp_path, mode="min", config={"audit": {"languages": ["html"]}}
    )
    ids = {r.audit_id for r in results}

    assert "html-syntax" in ids
    assert "python-syntax" not in ids
    assert "quality" not in ids and "wiring" not in ids
    assert "python-audits" in ids  # honest SKIP row, not silence


# ── native test-suite execution rows ──


def test_run_test_suite_pass_and_fail(tmp_path):
    ok = runner._run_test_suite(
        tmp_path, "go", [sys.executable, "-c", "raise SystemExit(0)"]
    )
    assert ok.audit_id == "go-tests" and ok.status == AuditStatus.PASS

    bad = runner._run_test_suite(
        tmp_path, "go", [sys.executable, "-c", "raise SystemExit(3)"]
    )
    assert bad.status == AuditStatus.FAIL and bad.is_failure


def test_run_test_suite_missing_runner_is_skip(tmp_path):
    result = runner._run_test_suite(
        tmp_path, "java", ["no-such-test-runner-xyz", "test"]
    )
    assert result.status == AuditStatus.SKIP
    assert result.tool_missing


# ── JSON report carries adapter findings ──


def test_json_report_contains_adapter_findings(tmp_path):
    (tmp_path / "bad.py").write_text("def f(:\n", encoding="utf-8")
    result = PythonAdapter.syntax_check(tmp_path)
    out = tmp_path / "report.json"

    json_report.write([result], out)
    data = json.loads(out.read_text(encoding="utf-8"))

    audit = data["audits"][0]
    assert audit["id"] == "python-syntax"
    assert audit["status"] == "FAIL"
    finding = audit["findings"][0]
    assert finding["file"] == "bad.py"
    assert finding["line"] == 1
    assert finding["severity"] == "HIGH"
    assert finding["language"] == "python"


# ── Windows cp1252 console fix ──


def test_force_utf8_output_makes_status_glyphs_printable(monkeypatch):
    """A cp1252 stream (Windows console default) crashes on ✓/✗ —
    _force_utf8_output() must reconfigure it so the report can print."""
    buf = io.BytesIO()
    cp1252 = io.TextIOWrapper(buf, encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", cp1252)
    monkeypatch.setattr(
        sys, "stderr", io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    )

    with pytest.raises(UnicodeEncodeError):
        cp1252.write("✓ ✗ △ ☠")

    cli._force_utf8_output()
    print("✓ ✗ △ ☠")
    sys.stdout.flush()
    assert "✓".encode("utf-8") in buf.getvalue()


# ── profile wiring ──


def test_profiles_load_known_and_unknown():
    from audit_code.profiles import load

    assert callable(load("agent-engine"))
    assert load("nope") is None


def test_run_suite_with_known_profile_adds_row(tmp_path, capsys):
    (tmp_path / "index.html").write_text("<p>hi</p>", encoding="utf-8")
    results = runner.run_suite(tmp_path, mode="min", profile="agent-engine")
    by_id = {r.audit_id: r for r in results}
    assert "profile-agent-engine" in by_id
    assert not by_id["profile-agent-engine"].is_failure


def test_run_suite_with_unknown_profile_fails_closed(tmp_path, capsys):
    """A typo'd --profile must surface as an ERROR row, not vanish."""
    (tmp_path / "index.html").write_text("<p>hi</p>", encoding="utf-8")
    results = runner.run_suite(tmp_path, mode="min", profile="bogus")
    by_id = {r.audit_id: r for r in results}
    assert by_id["profile-bogus"].status == AuditStatus.ERROR
    assert by_id["profile-bogus"].is_failure
    assert any(r.is_failure for r in results)


# ── wiring: framework callbacks are not dead symbols ──


def test_framework_wired_methods_heuristic():
    """Public methods of a class with an externally-defined base are
    framework-dispatched (HTMLParser.handle_*) — wired, not dead. Private
    methods and methods of locally-based classes stay eligible."""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from audit_code import audit_wiring

    src = (
        "from html.parser import HTMLParser\n"
        "class T(HTMLParser):\n"
        "    def handle_starttag(self, tag, attrs): pass\n"
        "    def _private_helper(self): pass\n"
        "class LocalBase:\n"
        "    pass\n"
        "class Child(LocalBase):\n"
        "    def visible(self): pass\n"
    )
    trees = {Path("x.py"): ast.parse(src)}
    wired = audit_wiring.framework_wired_methods(trees)

    assert "handle_starttag" in wired
    assert "_private_helper" not in wired  # frameworks dispatch public names
    assert "visible" not in wired  # LocalBase is defined in-repo
