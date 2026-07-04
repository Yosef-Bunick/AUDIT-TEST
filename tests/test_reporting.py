"""Report-writer tests — SARIF and JUnit output shape.

The JSON writer is covered in test_runner.py; these pin the other two
formats CI consumes: SARIF severity mapping + locations for GitHub code
scanning, and JUnit XML that a CI test-report parser accepts.
"""

import json
from xml.etree.ElementTree import fromstring

from audit_code.models import AuditResult, AuditStatus, Finding, Severity
from audit_code.reporting import junit, sarif


def _sample_results() -> list:
    return [
        AuditResult(
            audit_id="python-syntax",
            status=AuditStatus.FAIL,
            findings=[
                Finding(
                    rule_id="python-syntax",
                    severity=Severity.HIGH,
                    message="invalid syntax",
                    file="src\\pkg\\bad.py",
                    line=3,
                    language="python",
                ),
                Finding(
                    rule_id="python-syntax",
                    severity=Severity.MEDIUM,
                    message="style drift",
                    file="src/pkg/meh.py",
                ),
                Finding(
                    rule_id="python-syntax",
                    severity=Severity.INFO,
                    message="note only",
                ),
            ],
        ),
        AuditResult(audit_id="wiring", status=AuditStatus.PASS),
        AuditResult(audit_id="go-syntax", status=AuditStatus.SKIP),
    ]


# ── SARIF ──


def test_sarif_severity_mapping_and_locations(tmp_path):
    out = tmp_path / "r.sarif"
    count = sarif.write(_sample_results(), out)
    assert count == 3

    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["version"] == "2.1.0"
    results = data["runs"][0]["results"]
    levels = [r["level"] for r in results]
    assert levels == ["error", "warning", "note"]  # HIGH/MEDIUM/INFO

    first = results[0]
    loc = first["locations"][0]["physicalLocation"]
    assert loc["artifactLocation"]["uri"] == "src/pkg/bad.py"  # backslashes fixed
    assert loc["region"]["startLine"] == 3

    # finding without a file → no bogus location entry
    assert results[2]["locations"] == []


def test_sarif_empty_findings_is_valid(tmp_path):
    out = tmp_path / "empty.sarif"
    count = sarif.write([AuditResult(audit_id="x", status=AuditStatus.PASS)], out)
    assert count == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["runs"][0]["results"] == []


# ── JUnit ──


def test_junit_xml_parses_with_correct_counts(tmp_path):
    out = tmp_path / "r.xml"
    count = junit.write(_sample_results(), out)
    assert count == 3

    # S314: safe — parsing XML this test just wrote itself
    root = fromstring(out.read_text(encoding="utf-8"))  # noqa: S314
    assert root.tag == "testsuite"
    assert root.get("failures") == "1"  # one FAIL audit
    assert root.get("skipped") == "1"  # one SKIP audit
    assert root.get("errors") == "0"

    cases = root.findall("testcase")
    assert len(cases) == 3
    # HIGH and MEDIUM findings carry a <failure> child; INFO does not
    with_failure = [c for c in cases if c.find("failure") is not None]
    assert len(with_failure) == 2
    assert cases[0].get("file") == "src\\pkg\\bad.py"
    assert cases[0].get("line") == "3"


def test_junit_empty_results_is_valid_xml(tmp_path):
    out = tmp_path / "empty.xml"
    junit.write([], out)
    root = fromstring(out.read_text(encoding="utf-8"))  # noqa: S314 (own output)
    assert root.get("tests") == "0"
