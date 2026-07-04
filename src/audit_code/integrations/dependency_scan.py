"""Dependency Scan integration — stub."""

import shutil
from pathlib import Path

from audit_code.models import AuditResult, AuditStatus


def run(target_root: Path, timeout: int = 300) -> AuditResult:
    """Run dependency_scan against the target project."""
    _ = timeout  # noqa: F841 (used when implemented)
    if not shutil.which("dependency_scan"):
        return AuditResult(
            audit_id="dependency_scan",
            status=AuditStatus.SKIP,
            findings=[],
            completed=True,
            tool_missing=True,
            stdout="SKIP: dependency_scan not installed",
        )
    return AuditResult(
        audit_id="dependency_scan",
        status=AuditStatus.SKIP,
        findings=[],
        completed=True,
        stdout="SKIP: dependency_scan integration not yet implemented",
    )
