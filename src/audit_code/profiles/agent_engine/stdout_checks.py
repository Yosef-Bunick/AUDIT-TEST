"""Stdout Checks — Agent Engine profile."""

from audit_code.models import AuditResult, AuditStatus


def check(target_root) -> AuditResult:
    """Verify __EVENT__ / __RESULT__ stdout markers."""
    return AuditResult(
        audit_id="profile-ae-stdout",
        status=AuditStatus.SKIP,
        findings=[],
        completed=True,
        stdout="SKIP: not yet implemented",
    )
