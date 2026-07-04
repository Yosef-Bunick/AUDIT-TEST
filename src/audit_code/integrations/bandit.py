"""Bandit integration — Python security scanner."""

import json
import shutil
import subprocess
from pathlib import Path

from audit_code.models import AuditResult, AuditStatus, Finding, Severity


def run(target_root: Path, timeout: int = 300) -> AuditResult:
    """Run bandit against the target project."""
    exe = shutil.which("bandit")
    if not exe:
        return AuditResult(
            audit_id="bandit",
            status=AuditStatus.SKIP,
            tool_missing=True,
            stdout="SKIP: bandit not installed (pip install bandit)",
        )

    try:
        proc = subprocess.run(
            [
                exe,
                "-r",
                ".",
                "-f",
                "json",
                "-q",
                "--severity-level",
                "medium",
                "--exclude",
                ".venv,venv,node_modules,.git,__pycache__,dist,build,./tests",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(target_root),
        )
    except subprocess.TimeoutExpired:
        return AuditResult(
            audit_id="bandit",
            status=AuditStatus.CRASH,
            stderr=f"timed out after {timeout}s",
        )

    out = (proc.stdout or "").strip()
    if proc.returncode not in (0, 1):
        return AuditResult(
            audit_id="bandit",
            status=AuditStatus.CRASH,
            stderr=proc.stderr or f"exit {proc.returncode}",
        )

    findings: list[Finding] = []
    high = medium = info = 0

    if out:
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            return AuditResult(
                audit_id="bandit",
                status=AuditStatus.ERROR,
                stderr="could not parse bandit JSON output",
            )

        for r in data.get("results", []):
            sev_map = {
                "HIGH": Severity.HIGH,
                "MEDIUM": Severity.MEDIUM,
                "LOW": Severity.INFO,
            }
            sev = sev_map.get(r.get("issue_severity", ""), Severity.INFO)
            if sev == Severity.HIGH:
                high += 1
            elif sev == Severity.MEDIUM:
                medium += 1
            else:
                info += 1

            findings.append(
                Finding(
                    rule_id=r.get("test_id", "bandit"),
                    severity=sev,
                    message=r.get("issue_text", ""),
                    file=r.get("filename", ""),
                    line=r.get("line_number"),
                    source="bandit",
                )
            )

    status = (
        AuditStatus.FAIL
        if high
        else (AuditStatus.WARN if (medium or info) else AuditStatus.PASS)
    )

    lines = [
        f"{len(findings)} finding(s) — {high} HIGH, {medium} MEDIUM, {info} INFO",
        "",
    ]
    for f in sorted(
        findings,
        key=lambda x: (
            (
                0
                if x.severity == Severity.HIGH
                else 1 if x.severity == Severity.MEDIUM else 2
            ),
            x.file or "",
        ),
    ):
        lines.append(
            f"  [{f.severity.value:6}] {f.rule_id:10} {f.file}:{f.line or '-'}  {f.message[:100]}"
        )
    return AuditResult(
        audit_id="bandit",
        status=status,
        findings=findings,
        high=high,
        medium=medium,
        info=info,
        stdout="\n".join(lines[:200]),  # cap at 200 lines to avoid flooding
    )
