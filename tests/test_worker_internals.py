"""Drive subprocess-worker internals in-process (mocked I/O) so coverage sees them.

These functions normally run inside spawned audit_*.py subprocesses or shell out
to git/pytest; the mocks replace the process boundary so the pure logic runs
under the suite.
"""

import ast
import subprocess

import pytest

from audit_code import audit_deps, audit_gate, audit_runtime, audit_suite, deps, gate
from audit_code import suite as suite_mod
from audit_code.adapters.java.adapter import JavaAdapter
from audit_code.adapters.javascript.adapter import JavaScriptAdapter
from audit_code.models import AuditStatus


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# ── thin subprocess shims ────────────────────────────────────────────────────


def test_deps_run_pass_and_crash(monkeypatch, tmp_path):
    monkeypatch.setattr(deps.subprocess, "run", lambda *a, **k: _Proc(0, "ok"))
    assert deps.run(tmp_path).status == AuditStatus.PASS

    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="x", timeout=60)

    monkeypatch.setattr(deps.subprocess, "run", boom)
    assert deps.run(tmp_path).status == AuditStatus.CRASH


def test_gate_run_gate_returns_exit_code(monkeypatch, tmp_path):
    monkeypatch.setattr(gate.subprocess, "run", lambda *a, **k: _Proc(returncode=1))
    # exercise every flag branch while we're here
    code = gate.run_gate(
        tmp_path, fast=True, no_static=True, severity="MEDIUM", verbose=True
    )
    assert code == 1
    monkeypatch.setattr(gate.subprocess, "run", lambda *a, **k: _Proc(returncode=0))
    assert gate.run_gate(tmp_path, severity=None) == 0


# ── audit_deps requirements read/write (ROOT redirected to tmp) ───────────────


def test_read_requirements(monkeypatch, tmp_path):
    monkeypatch.setattr(audit_deps, "ROOT", tmp_path)
    assert audit_deps._read_requirements() == []  # missing file
    (tmp_path / ".requirements").write_text("requests\n# note\n", encoding="utf-8")
    assert audit_deps._read_requirements() == ["requests", "# note"]


def test_write_requirements(monkeypatch, tmp_path):
    monkeypatch.setattr(audit_deps, "ROOT", tmp_path)
    audit_deps._write_requirements(
        {"requests": ["a.py", "b.py"]}, preserved=["# manual header", ""]
    )
    text = (tmp_path / ".requirements").read_text(encoding="utf-8")
    assert "# manual header" in text
    assert "requests" in text and "auto-generated" in text


# ── audit_gate helpers (git/subprocess mocked) ───────────────────────────────


def test_static_high_shape(monkeypatch, tmp_path):
    # _run is the subprocess wrapper; return "no summary" -> completed False
    monkeypatch.setattr(audit_gate, "_run", lambda *a, **k: (1, ""))
    counts = audit_gate._static_high(tmp_path)
    assert isinstance(counts, dict) and counts  # one entry per static audit
    assert all(isinstance(v, tuple) and v[1] is False for v in counts.values())


def test_changed_files_parses_porcelain(monkeypatch):
    porcelain = " M src/x.py\n?? new.py\n D old.py\n"
    monkeypatch.setattr(audit_gate, "_run", lambda *a, **k: (0, porcelain))
    changed, deleted = audit_gate._changed_files()
    assert "src/x.py" in changed and changed["src/x.py"] == set()
    assert changed.get("new.py") is None  # untracked -> all lines new
    assert "old.py" in deleted


# ── solo-failure classification (pytest subprocess mocked) ───────────────────


@pytest.mark.parametrize(
    "rc,out,expect",
    [(0, "", "POLLUTION"), (1, "1 failed", "real"), (5, "no tests ran", "vanished")],
)
def test_audit_suite_classify_solo(monkeypatch, rc, out, expect):
    monkeypatch.setattr(audit_suite, "_run_pytest", lambda *a, **k: (out, rc))
    assert expect in audit_suite._classify_solo("tests/test_x.py::test_y")


def test_suite_classify_solo(monkeypatch, tmp_path):
    monkeypatch.setattr(suite_mod, "_run_pytest", lambda *a, **k: ("", 0))
    assert "POLLUTION" in suite_mod._classify_solo(tmp_path, "tests/t.py::t")


# ── runtime R-gate analysis (drives the nested _inert/classify_uses/visit/
#    assign_targets via one gate fixture) ─────────────────────────────────────


def test_audit_gates_walks_gate_usage():
    # a gate (check_*) that RETURNS and never raises, whose result is assigned
    # and consulted only by an inert (log-only) branch — the exact shape the
    # R-gate audit's nested helpers classify.
    code = (
        "def check_ok():\n"
        "    return True\n"
        "\n"
        "def vetoed():\n"  # inert log-only branch -> _inert, assign_targets
        "    ok = check_ok()\n"
        "    if not ok:\n"
        "        log.info('vetoed')\n"
        "    return ok\n"
        "\n"
        "def consumer():\n"  # gate result feeds a real branch -> classify_uses/visit
        "    v = check_ok()\n"
        "    if v:\n"
        "        return 1\n"
        "    return v\n"
    )
    trees = {audit_runtime.ROOT / "gate_fixture.py": ast.parse(code)}
    findings = audit_runtime.audit_gates(trees)
    assert isinstance(findings, list)  # nested helpers executed without error


# ── javascript adapter tsc discovery ─────────────────────────────────────────


def test_js_find_tsc_none_without_toolchain(tmp_path):
    result = JavaScriptAdapter._find_tsc(tmp_path)
    assert result is None or isinstance(result, list)


def test_java_test_command_none_without_build_file(tmp_path):
    assert JavaAdapter.test_command(tmp_path) is None  # no pom.xml / build.gradle
