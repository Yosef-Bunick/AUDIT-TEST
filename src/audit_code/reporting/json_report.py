"""JSON report — machine-readable audit output."""

import json
from pathlib import Path

from audit_code.models import AuditResult


def write(audits: list[AuditResult], path: str | Path) -> int:
    """Write audit results to a JSON file. Returns number of findings."""
    data = {
        "version": "0.1.0",
        "audits": [
            {
                "id": a.audit_id,
                "status": a.status.value,
                "high": a.high,
                "medium": a.medium,
                "info": a.info,
                "completed": a.completed,
                "duration_seconds": a.duration_seconds,
                "stdout": a.stdout,
                "findings": [
                    {
                        "rule_id": f.rule_id,
                        "severity": f.severity.value,
                        "message": f.message,
                        "file": f.file,
                        "line": f.line,
                        "language": f.language,
                        "source": f.source,
                        "fingerprint": f.fingerprint,
                        "suggestion": f.suggestion,
                    }
                    for f in a.findings
                ],
            }
            for a in audits
        ],
    }
    Path(path).write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    return sum(len(a.findings) for a in audits)
