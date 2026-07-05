"""rustfmt integration — Rust formatter (format drift only).

Driven via `cargo fmt` so it walks the whole crate; bare `rustfmt --check`
operates on individual files, not a directory tree.
"""

from audit_code.models import Severity

from ._tool_runner import _run_tool


def run(target_root, timeout=300):
    # cargo fmt --check exits 1 when files would be reformatted. Cosmetic → MEDIUM.
    return _run_tool(
        "cargo",
        ["cargo", "fmt", "--all", "--check"],
        "rustfmt",
        target_root,
        timeout,
        severity=Severity.MEDIUM,
        violation_codes={1},
    )
