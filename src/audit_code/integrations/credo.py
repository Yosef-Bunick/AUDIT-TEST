"""Credo integration — Elixir linter (via mix)."""

from ._tool_runner import _run_tool


def run(target_root, timeout=300):
    return _run_tool(
        "mix",
        ["mix", "credo", "--format", "oneline"],
        "credo",
        target_root,
        timeout,
        violation_codes=None,
    )
