"""ESLint integration — JavaScript/TypeScript linter."""

from ._tool_runner import _run_tool


def run(target_root, timeout=300):
    # exit 1 = lint problems; 2 = fatal config error (→ CRASH).
    return _run_tool(
        "eslint",
        ["eslint", ".", "--format", "compact"],
        "eslint",
        target_root,
        timeout,
        violation_codes={1},
    )
