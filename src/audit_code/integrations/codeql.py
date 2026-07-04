"""Codeql integration — stub."""

import shutil
from pathlib import Path

from audit_code.models import AuditResult, AuditStatus


def run(target_root: Path, timeout: int = 300) -> AuditResult:
    """Run codeql against the target project."""
    _ = timeout  # noqa: F841 (used when implemented)
    if not shutil.which("codeql"):
        return AuditResult(
            audit_id="codeql",
            status=AuditStatus.SKIP,
            findings=[],
            completed=True,
            tool_missing=True,
            stdout="SKIP: codeql not installed",
        )
    return AuditResult(
        audit_id="codeql",
        status=AuditStatus.SKIP,
        findings=[],
        completed=True,
        stdout="SKIP: codeql integration not yet implemented",
    )
