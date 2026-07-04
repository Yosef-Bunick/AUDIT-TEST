"""Tool Registry Checks — Agent Engine profile."""

from audit_code.models import AuditResult, AuditStatus


def check(target_root) -> AuditResult:
    """Verify tool definitions vs dispatch parity."""
    return AuditResult(
        audit_id="profile-ae-tool",
        status=AuditStatus.SKIP,
        findings=[],
        completed=True,
        stdout="SKIP: not yet implemented",
    )
