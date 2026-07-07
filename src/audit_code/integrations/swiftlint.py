"""SwiftLint integration — Swift linter."""

from ._tool_runner import _run_tool


def run(target_root, timeout=300):
    return _run_tool(
        "swiftlint",
        ["swiftlint", "lint", "--quiet"],
        "swiftlint",
        target_root,
        timeout,
        violation_codes=None,
    )
