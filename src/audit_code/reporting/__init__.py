"""Reporting layer — console, JSON, SARIF, JUnit output formats."""

from audit_code.models import AuditResult
from audit_code.reporting import json_report, junit, sarif


def write(
    audits: list[AuditResult],
    json_path: str = "",
    sarif_path: str = "",
    junit_path: str = "",
) -> None:
    """Write audit results to requested formats."""
    if json_path:
        json_report.write(audits, json_path)
    if sarif_path:
        sarif.write(audits, sarif_path)
    if junit_path:
        junit.write(audits, junit_path)
