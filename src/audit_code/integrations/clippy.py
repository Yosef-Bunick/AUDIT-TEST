"""cargo clippy integration — Rust linter."""

from ._tool_runner import _run_tool


def run(target_root, timeout=300):
    # -D warnings promotes lints to errors; cargo then exits non-zero. A
    # compile error also exits non-zero, so any non-zero code is a finding.
    return _run_tool(
        "cargo",
        ["cargo", "clippy", "--", "-D", "warnings"],
        "clippy",
        target_root,
        timeout,
        violation_codes=None,
    )
