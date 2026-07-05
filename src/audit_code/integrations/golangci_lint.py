"""golangci-lint integration — Go meta-linter."""

from ._tool_runner import _run_tool


def run(target_root, timeout=300):
    # Default issues-exit-code is 1; config/internal failures use 3/7 (→ CRASH).
    return _run_tool(
        "golangci-lint",
        ["golangci-lint", "run", "./..."],
        "golangci-lint",
        target_root,
        timeout,
        violation_codes={1},
    )
