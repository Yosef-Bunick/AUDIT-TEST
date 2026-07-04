"""Prompt Checks — Agent Engine profile."""

from audit_code.models import AuditResult, AuditStatus


def check(target_root) -> AuditResult:
    """Verify prompt files: {task} placeholders, JSON contracts."""
    return AuditResult(
        audit_id="profile-ae-prompt",
        status=AuditStatus.SKIP,
        findings=[],
        completed=True,
        stdout="SKIP: not yet implemented",
    )
