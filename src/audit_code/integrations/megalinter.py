"""Megalinter integration — stub."""

import shutil
from pathlib import Path

from audit_code.models import AuditResult, AuditStatus


def run(target_root: Path, timeout: int = 300) -> AuditResult:
    """Run megalinter against the target project."""
    _ = timeout  # noqa: F841 (used when implemented)
    if not shutil.which("megalinter"):
        return AuditResult(
            audit_id="megalinter",
            status=AuditStatus.SKIP,
            findings=[],
            completed=True,
            tool_missing=True,
            stdout="SKIP: megalinter not installed",
        )
    return AuditResult(
        audit_id="megalinter",
        status=AuditStatus.SKIP,
        findings=[],
        completed=True,
        stdout="SKIP: megalinter integration not yet implemented",
    )
