"""Tests for the thin in-package wrappers that shell out to the standalone
audit scripts (phd/wiring/runtime/suite) and for runner's standalone-tool path.

These verify the subprocess is spawned, its SUMMARY parsed, and a real
AuditResult returned — i.e. the wrapper completes instead of crashing.
"""

from audit_code import phd, runner, runtime, suite, wiring
from audit_code.models import AuditStatus

_NON_CRASH = (AuditStatus.PASS, AuditStatus.WARN, AuditStatus.FAIL)


def _clean_project(tmp_path):
    # `used` is referenced, so wiring will not flag it as dead.
    (tmp_path / "app.py").write_text(
        'def used():\n    """Doc."""\n    return 1\n\n\nprint(used())\n',
        encoding="utf-8",
    )
    return tmp_path


def test_phd_wrapper_runs_and_parses(tmp_path):
    r = phd.run(_clean_project(tmp_path))
    assert r.status in _NON_CRASH
    assert r.audit_id == "phd"


def test_wiring_wrapper_runs_and_parses(tmp_path):
    r = wiring.run(_clean_project(tmp_path))
    assert r.status in _NON_CRASH
    assert r.audit_id == "wiring"


def test_runtime_wrapper_runs_and_parses(tmp_path):
    r = runtime.run(_clean_project(tmp_path))
    assert r.status in _NON_CRASH
    assert r.audit_id == "runtime"


def test_phd_wrapper_exports_structured_findings(tmp_path):
    (tmp_path / "app.py").write_text(
        "def f():\n"
        "    try:\n"
        "        risky()\n"
        "    except Exception:\n"
        "        pass\n\n\n"
        "f()\n",
        encoding="utf-8",
    )
    r = phd.run(tmp_path)
    assert r.high >= 1
    c2 = [f for f in r.findings if f.rule_id == "C2"]
    assert c2, "swallowed-exception finding should be structured, not stdout-only"
    assert c2[0].file == "app.py" and c2[0].line == 4
    assert len(r.findings) == r.high + r.medium + r.info


def test_wiring_wrapper_exports_structured_findings(tmp_path):
    (tmp_path / "app.py").write_text(
        'def dead_helper():\n    """Doc."""\n    return 1\n', encoding="utf-8"
    )
    r = wiring.run(tmp_path)
    w1 = [f for f in r.findings if f.rule_id == "W1"]
    assert w1, "dead symbol should be a structured finding"
    assert w1[0].file == "app.py" and w1[0].line == 1
    assert len([f for f in r.findings if f.severity.value == "HIGH"]) == r.high


def test_suite_wrapper_runs_pytest(tmp_path):
    _clean_project(tmp_path)
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_ok.py").write_text(
        "def test_passes():\n    assert 1 + 1 == 2\n", encoding="utf-8"
    )
    r = suite.run(tmp_path)
    assert r.status == AuditStatus.PASS
    assert "1 passed" in r.stdout or "passed" in r.stdout


def test_run_standalone_tool_lint(tmp_path):
    (tmp_path / "clean.py").write_text("x = 1\n", encoding="utf-8")
    r = runner._run_standalone_tool(tmp_path, "lint", fix=False)
    # ruff present → PASS/FAIL; absent → SKIP. Never a crash.
    assert r.status in (AuditStatus.PASS, AuditStatus.FAIL, AuditStatus.SKIP)
    assert r.audit_id == "lint"
