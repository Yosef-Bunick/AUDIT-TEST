"""PHPStan integration — PHP static analyser."""

from ._tool_runner import _run_tool


def run(target_root, timeout=300):
    return _run_tool(
        "phpstan",
        ["phpstan", "analyse", "--no-progress", "--error-format=raw", "."],
        "phpstan",
        target_root,
        timeout,
        violation_codes={1},
    )
