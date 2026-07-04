"""phd.py — PhD-standard static review.

Answers "does it meet the bar?" — exception discipline, security patterns,
state management, complexity, test smells, and documentation coverage.
"""

import re
import subprocess
import sys
from pathlib import Path

from audit_code.models import (
    AuditResult,
    AuditStatus,
)

_SCRIPT = Path(__file__).resolve().parent.parent.parent / "audit_phd.py"

SUMMARY_RE = re.compile(r"SUMMARY\s+HIGH:\s*(\d+)\s+MEDIUM:\s*(\d+)\s+INFO:\s*(\d+)")


def run(target_root: Path, strict: bool = True) -> AuditResult:
    """Run the PhD audit against a target project."""
    try:
        proc = subprocess.run(
            [sys.executable, str(_SCRIPT), "--path", str(target_root)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            cwd=str(target_root),
        )
    except subprocess.TimeoutExpired:
        return AuditResult(
            audit_id="phd",
            status=AuditStatus.CRASH,
            stderr="timed out after 300s",
        )

    out = proc.stdout or ""
    err = proc.stderr or ""

    high = med = info = 0
    m = SUMMARY_RE.search(out)
    if m:
        high, med, info = int(m.group(1)), int(m.group(2)), int(m.group(3))

    crashed = proc.returncode not in (0, 1) or "SUMMARY" not in out

    if crashed:
        return AuditResult(
            audit_id="phd",
            status=AuditStatus.CRASH,
            stdout=out,
            stderr=err,
        )

    status = (
        AuditStatus.FAIL
        if high
        else (AuditStatus.WARN if (med or info) else AuditStatus.PASS)
    )

    return AuditResult(
        audit_id="phd",
        status=status,
        high=high,
        medium=med,
        info=info,
        stdout=out,
        stderr=err,
    )
