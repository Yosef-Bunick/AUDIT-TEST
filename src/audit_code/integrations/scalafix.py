"""Scalafix integration — Scala linter/refactoring tool."""

from ._tool_runner import _run_tool


def run(target_root, timeout=300):
    return _run_tool(
        "scalafix",
        ["scalafix", "--check"],
        "scalafix",
        target_root,
        timeout,
        violation_codes={1},
    )
