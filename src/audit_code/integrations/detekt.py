"""detekt integration — Kotlin static analyser."""

from ._tool_runner import _run_tool


def run(target_root, timeout=300):
    return _run_tool(
        "detekt",
        ["detekt", "--input", ".", "--report", "txt"],
        "detekt",
        target_root,
        timeout,
        violation_codes=None,
    )
