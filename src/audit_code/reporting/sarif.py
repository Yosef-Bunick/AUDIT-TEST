"""SARIF report — GitHub code-scanning compatible output."""

import json
from pathlib import Path

from audit_code.models import AuditResult

_SEVERITY_MAP = {"HIGH": "error", "MEDIUM": "warning", "INFO": "note"}


def write(audits: list[AuditResult], path: str | Path) -> int:
    """Write audit results as SARIF v2.1.0. Returns number of findings."""
    results = []
    for a in audits:
        for f in a.findings:
            location = {}
            if f.file:
                uri = f.file.replace("\\", "/")
                location["physicalLocation"] = {
                    "artifactLocation": {"uri": uri},
                }
                if f.line:
                    location["physicalLocation"]["region"] = {"startLine": f.line}  # type: ignore[dict-item]
            results.append(
                {
                    "ruleId": f.rule_id,
                    "level": _SEVERITY_MAP.get(f.severity.value, "warning"),
                    "message": {"text": f.message},
                    "locations": [location] if location else [],
                }
            )

    sarif = {
        "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "audit-code",
                        "version": "0.1.0",
                        "informationUri": "https://github.com/Yosef-Bunick/AUDIT_TESTING_TESTS-CODE",
                    }
                },
                "results": results,
            }
        ],
    }
    Path(path).write_text(json.dumps(sarif, indent=2), encoding="utf-8")
    return len(results)
