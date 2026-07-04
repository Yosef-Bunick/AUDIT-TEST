"""Config Checks — Agent Engine profile."""

from audit_code.models import AuditResult, AuditStatus


def check(target_root) -> AuditResult:
    """Verify config files: limits.json, model_rules.json, etc."""
    return AuditResult(
        audit_id="profile-ae-config",
        status=AuditStatus.SKIP,
        findings=[],
        completed=True,
        stdout="SKIP: not yet implemented",
    )
