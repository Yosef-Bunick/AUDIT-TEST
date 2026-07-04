"""Secret Scan integration — stub."""

import shutil
from pathlib import Path

from audit_code.models import AuditResult, AuditStatus


def run(target_root: Path, timeout: int = 300) -> AuditResult:
    """Run secret_scan against the target project."""
    _ = timeout  # noqa: F841 (used when implemented)
    if not shutil.which("secret_scan"):
        return AuditResult(
            audit_id="secret_scan",
            status=AuditStatus.SKIP,
            findings=[],
            completed=True,
            tool_missing=True,
            stdout="SKIP: secret_scan not installed",
        )
    return AuditResult(
        audit_id="secret_scan",
        status=AuditStatus.SKIP,
        findings=[],
        completed=True,
        stdout="SKIP: secret_scan integration not yet implemented",
    )
