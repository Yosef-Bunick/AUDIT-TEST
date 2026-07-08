"""HLint integration — Haskell linter."""

from ._tool_runner import _run_tool


def run(target_root, timeout=300):
    return _run_tool(
        "hlint",
        ["hlint", "."],
        "hlint",
        target_root,
        timeout,
        violation_codes={1},
    )
