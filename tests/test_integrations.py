"""Tests for the integration adapters (_tool_runner, bandit, semgrep, and the
13 language-linter wrappers).

_run_tool is driven with the real Python interpreter at controlled exit codes,
so the exit-code → status mapping, the linter/formatter severity split, and the
UTF-8 decoding are all exercised for real. semgrep is mocked at the subprocess
boundary (its --config auto path needs the network); bandit runs for real
against a fixture since it is fast and local.
"""

import subprocess
import sys

from audit_code.integrations import (
    _tool_runner,
    bandit,
    checkstyle,
    clang_tidy,
    clippy,
    cppcheck,
    dotnet_format,
    eslint,
    go_vet,
    golangci_lint,
    htmlhint,
    pmd,
    prettier,
    rustfmt,
    semgrep,
    stylelint,
)
from audit_code.models import AuditStatus, Severity

PY = sys.executable


def _run(code, tmp_path, **kw):
    """Drive _run_tool with a real python process exiting deterministically."""
    return _tool_runner._run_tool(PY, [PY, "-c", code], "probe", tmp_path, **kw)


# ── _run_tool: exit-code → status mapping ──


def test_run_tool_pass_on_zero_exit(tmp_path):
    r = _run("pass", tmp_path)
    assert r.status == AuditStatus.PASS
    assert r.high == 0 and r.medium == 0


def test_run_tool_fail_high_for_linter_violation(tmp_path):
    r = _run("import sys; sys.exit(1)", tmp_path, violation_codes={1})
    assert r.status == AuditStatus.FAIL
    assert r.high == 1
    assert r.findings and r.findings[0].severity == Severity.HIGH


def test_run_tool_warn_medium_for_formatter_drift(tmp_path):
    r = _run(
        "import sys; sys.exit(1)",
        tmp_path,
        severity=Severity.MEDIUM,
        violation_codes={1},
    )
    assert r.status == AuditStatus.WARN
    assert r.medium == 1 and r.high == 0


def test_run_tool_crash_on_non_violation_exit_code(tmp_path):
    # exit 3 is not in {1}, so it is a bad invocation, not a lint finding.
    r = _run("import sys; sys.exit(3)", tmp_path, violation_codes={1})
    assert r.status == AuditStatus.CRASH
    assert r.high == 0


def test_run_tool_none_violation_codes_treats_any_nonzero_as_fail(tmp_path):
    r = _run("import sys; sys.exit(7)", tmp_path, violation_codes=None)
    assert r.status == AuditStatus.FAIL
    assert r.high == 1


def test_run_tool_skip_when_executable_missing(tmp_path):
    r = _tool_runner._run_tool(
        "definitely_not_a_real_tool_xyz",
        ["definitely_not_a_real_tool_xyz", "."],
        "probe",
        tmp_path,
    )
    assert r.status == AuditStatus.SKIP
    assert r.tool_missing is True


def test_run_tool_decodes_utf8_output_without_crashing(tmp_path):
    # Child writes raw UTF-8 bytes; a cp1252 decode in _run_tool would raise.
    r = _run("import sys; sys.stdout.buffer.write('\\u2713'.encode('utf-8'))", tmp_path)
    assert r.status == AuditStatus.PASS
    assert "✓" in r.stdout


# ── bandit: real run against a fixture ──


def test_bandit_flags_eval(tmp_path):
    # eval is B307 (MEDIUM) — a literal shell=True string is only LOW and would
    # be filtered by --severity-level medium.
    (tmp_path / "vuln.py").write_text(
        "def f(x):\n    return eval(x)\n", encoding="utf-8"
    )
    r = bandit.run(tmp_path)
    if r.tool_missing:  # environment without bandit — do not fail the suite
        return
    assert r.status in (AuditStatus.FAIL, AuditStatus.WARN)
    assert r.high + r.medium >= 1
    assert any(f.source == "bandit" and f.rule_id == "B307" for f in r.findings)


def test_bandit_skips_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(bandit.shutil, "which", lambda _n: None)
    r = bandit.run(tmp_path)
    assert r.status == AuditStatus.SKIP
    assert r.tool_missing is True


# ── semgrep: mocked at the subprocess boundary (no network) ──


def test_semgrep_parses_findings(tmp_path, monkeypatch):
    monkeypatch.setattr(semgrep.shutil, "which", lambda _n: "semgrep")
    payload = {
        "results": [
            {
                "check_id": "python.lang.security.dangerous-eval",
                "path": "app.py",
                "start": {"line": 3},
                "extra": {"severity": "ERROR", "message": "eval is dangerous"},
            }
        ]
    }

    def fake_run(*_a, **_k):
        return subprocess.CompletedProcess(
            args=[], returncode=1, stdout=__import__("json").dumps(payload), stderr=""
        )

    monkeypatch.setattr(semgrep.subprocess, "run", fake_run)
    r = semgrep.run(tmp_path)
    assert r.status == AuditStatus.FAIL
    assert r.high == 1
    assert r.findings[0].source == "semgrep"
    assert r.findings[0].line == 3


def test_semgrep_skips_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(semgrep.shutil, "which", lambda _n: None)
    r = semgrep.run(tmp_path)
    assert r.status == AuditStatus.SKIP
    assert r.tool_missing is True


# ── language wrappers: SKIP cleanly when their toolchain is absent ──


def test_language_wrappers_skip_when_tool_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(_tool_runner.shutil, "which", lambda _n: None)
    wrappers = [
        eslint,
        prettier,
        checkstyle,
        pmd,
        go_vet,
        golangci_lint,
        clippy,
        rustfmt,
        dotnet_format,
        cppcheck,
        htmlhint,
        stylelint,
    ]
    for mod in wrappers:
        r = mod.run(tmp_path)
        assert r.status == AuditStatus.SKIP, f"{mod.__name__} should SKIP"
        assert r.tool_missing is True


def test_clang_tidy_skips_without_compile_commands(tmp_path):
    # No compile_commands.json → honest SKIP, not a crash.
    (tmp_path / "a.cpp").write_text("int main(){return 0;}\n", encoding="utf-8")
    r = clang_tidy.run(tmp_path)
    assert r.status == AuditStatus.SKIP
    assert "compile_commands" in r.stdout
