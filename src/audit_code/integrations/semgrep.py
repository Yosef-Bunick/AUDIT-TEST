"""Semgrep integration — stub."""

import shutil
from pathlib import Path

from audit_code.models import AuditResult, AuditStatus


def run(target_root: Path, timeout: int = 300) -> AuditResult:
    """Run semgrep against the target project."""
    _ = timeout  # noqa: F841 (used when implemented)
    if not shutil.which("semgrep"):
        return AuditResult(
            audit_id="semgrep",
            status=AuditStatus.SKIP,
            findings=[],
            completed=True,
            tool_missing=True,
            stdout="SKIP: semgrep not installed",
        )
    return AuditResult(
        audit_id="semgrep",
        status=AuditStatus.SKIP,
        findings=[],
        completed=True,
        stdout="SKIP: semgrep integration not yet implemented",
    )
