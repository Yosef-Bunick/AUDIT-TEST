"""zig fmt integration — Zig formatter (format drift only)."""

from audit_code.models import Severity

from ._tool_runner import _run_tool


def run(target_root, timeout=300):
    return _run_tool(
        "zig",
        ["zig", "fmt", "--check", "."],
        "zig-fmt",
        target_root,
        timeout,
        severity=Severity.MEDIUM,
        violation_codes={1},
    )
