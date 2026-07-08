"""RuboCop integration — Ruby linter/formatter."""

from ._tool_runner import _run_tool


def run(target_root, timeout=300):
    return _run_tool(
        "rubocop",
        ["rubocop", "--format", "progress", "."],
        "rubocop",
        target_root,
        timeout,
        violation_codes={1},
    )
