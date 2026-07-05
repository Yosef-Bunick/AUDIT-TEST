"""Checkstyle integration — Java style checker.

Note: Checkstyle usually ships as `checkstyle.jar` (run via `java -jar`); a
bare `checkstyle` launcher on PATH is provided only by some package managers.
When absent, this SKIPs honestly. Its exit code is the number of violations,
so any non-zero code counts as a finding.
"""

from ._tool_runner import _run_tool


def run(target_root, timeout=300):
    return _run_tool(
        "checkstyle",
        ["checkstyle", "-c", "/google_checks.xml", "."],
        "checkstyle",
        target_root,
        timeout,
        violation_codes=None,  # exit code == violation count
    )
