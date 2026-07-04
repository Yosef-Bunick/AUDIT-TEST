"""JUnit XML report — CI test-result compatible output."""

from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring

from audit_code.models import AuditResult


def write(audits: list[AuditResult], path: str | Path) -> int:
    """Write audit results as JUnit XML. Returns number of findings."""
    attrib = {
        "name": "audit-code",
        "tests": str(sum(len(a.findings) for a in audits) + len(audits)),
        "failures": str(sum(1 for a in audits if a.status.value == "FAIL")),
        "errors": str(sum(1 for a in audits if a.status.value == "ERROR")),
        "skipped": str(sum(1 for a in audits if a.status.value == "SKIP")),
    }
    suite = Element("testsuite", attrib)

    for a in audits:
        for f in a.findings:
            tc = SubElement(
                suite,
                "testcase",
                {
                    "classname": a.audit_id,
                    "name": f.rule_id,
                    "file": f.file or "",
                    "line": str(f.line or 0),
                },
            )
            if f.severity.value in ("HIGH", "MEDIUM"):
                SubElement(
                    tc,
                    "failure",
                    {
                        "message": f.message,
                        "type": f.severity.value,
                    },
                )

    Path(path).write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        + tostring(suite, encoding="unicode"),
        encoding="utf-8",
    )
    return sum(len(a.findings) for a in audits)
