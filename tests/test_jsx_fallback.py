# ruff: noqa: S101
import sys
import tempfile

sys.path.insert(0, "/mnt/c/AI/audit/src")
from pathlib import Path

from audit_code.adapters.base import TimeBudget
from audit_code.adapters.javascript.adapter import JavaScriptAdapter


def test_jsx_catches_broken():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "broken.jsx").write_text("function C() { return <div>; }\n")
        findings = []
        checked = JavaScriptAdapter._check_jsx_syntax(
            root, [root / "broken.jsx"], findings, TimeBudget(30)
        )
        assert checked == 1
        assert any("parse error" in f.message.lower() for f in findings)


def test_jsx_passes_clean():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "good.jsx").write_text("function C() { return <div>ok</div>; }\n")
        findings = []
        checked = JavaScriptAdapter._check_jsx_syntax(
            root, [root / "good.jsx"], findings, TimeBudget(30)
        )
        assert checked == 1
        assert not findings
