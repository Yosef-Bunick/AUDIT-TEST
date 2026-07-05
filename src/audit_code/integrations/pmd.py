"""PMD integration — Java static analysis."""

from ._tool_runner import _run_tool


def run(target_root, timeout=300):
    # PMD exit codes: 0 clean, 4 = violations found, 1/2 = error (→ CRASH).
    return _run_tool(
        "pmd",
        [
            "pmd",
            "check",
            "-d",
            ".",
            "-R",
            "rulesets/java/quickstart.xml",
            "-f",
            "text",
        ],
        "pmd",
        target_root,
        timeout,
        violation_codes={4},
    )
