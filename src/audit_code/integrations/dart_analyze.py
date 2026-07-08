"""Dart Analyzer integration — Dart static analyser."""

from ._tool_runner import _run_tool


def run(target_root, timeout=300):
    return _run_tool(
        "dart",
        ["dart", "analyze", "."],
        "dart-analyze",
        target_root,
        timeout,
        violation_codes=None,
    )
