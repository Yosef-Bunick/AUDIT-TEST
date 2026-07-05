"""Stylelint integration — CSS/SCSS linter.

Stylelint expands globs itself (via globby), so the patterns are passed
unquoted — a shell-style "…" would arrive as a literal quote character under
subprocess (no shell) and match nothing. --allow-empty-input keeps a repo with
no CSS from erroring.
"""

from audit_code.models import Severity

from ._tool_runner import _run_tool


def run(target_root, timeout=300):
    # exit 2 = lint problems; 78/80 = config/glob error (→ CRASH). Style → MEDIUM.
    return _run_tool(
        "stylelint",
        ["stylelint", "**/*.css", "**/*.scss", "--allow-empty-input"],
        "stylelint",
        target_root,
        timeout,
        severity=Severity.MEDIUM,
        violation_codes={2},
    )
