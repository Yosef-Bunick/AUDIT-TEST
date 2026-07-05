"""HTMLHint integration — HTML linter."""

from audit_code.models import Severity

from ._tool_runner import _run_tool


def run(target_root, timeout=300):
    # Exits 1 when lint errors are found. Markup lint → MEDIUM.
    return _run_tool(
        "htmlhint",
        ["htmlhint", "."],
        "htmlhint",
        target_root,
        timeout,
        severity=Severity.MEDIUM,
        violation_codes=None,
    )
