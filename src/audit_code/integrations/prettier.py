"""Prettier integration — JS/TS/CSS/HTML formatter (format drift only)."""

from audit_code.models import Severity

from ._tool_runner import _run_tool


def run(target_root, timeout=300):
    # exit 1 = files need formatting; 2 = error. Drift is cosmetic → MEDIUM.
    return _run_tool(
        "prettier",
        ["prettier", "--check", "."],
        "prettier",
        target_root,
        timeout,
        severity=Severity.MEDIUM,
        violation_codes={1},
    )
