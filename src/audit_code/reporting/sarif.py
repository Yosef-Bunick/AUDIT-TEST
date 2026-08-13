"""SARIF report — GitHub code-scanning compatible output."""

import hashlib
import json
from pathlib import Path

from audit_code.models import AuditResult

_SEVERITY_MAP = {"HIGH": "error", "MEDIUM": "warning", "INFO": "note"}


def _fingerprint(rule_id: str, uri: str, message: str) -> str:
    """Stable identity for cross-run dedup (code-scanning partialFingerprints).

    Deliberately excludes the line number: a finding that merely moves when
    unrelated lines are added/removed keeps its identity instead of closing
    and reopening as a "new" alert.
    """
    return hashlib.sha1(
        f"{rule_id}|{uri}|{message}".encode(), usedforsecurity=False
    ).hexdigest()


def write(audits: list[AuditResult], path: str | Path) -> int:
    """Write audit results as SARIF v2.1.0. Returns number of findings."""
    results = []
    for a in audits:
        for f in a.findings:
            location = {}
            uri = f.file.replace("\\", "/") if f.file else ""
            if f.file:
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
                    "partialFingerprints": {
                        "auditCode/v1": f.fingerprint
                        or _fingerprint(f.rule_id, uri, f.message)
                    },
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
