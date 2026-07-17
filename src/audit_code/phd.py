"""phd.py — PhD-standard static review.

Answers "does it meet the bar?" — exception discipline, security patterns,
state management, complexity, test smells, and documentation coverage.

Now calls audit_phd.main() directly instead of subprocess — eliminates
Python startup overhead (~15% of audit runtime).
"""

import io
import re
import sys
from contextlib import redirect_stdout
from pathlib import Path

from audit_code.models import (
    AuditResult,
    AuditStatus,
)

SUMMARY_RE = re.compile(r"SUMMARY\s+HIGH:\s*(\d+)\s+MEDIUM:\s*(\d+)\s+INFO:\s*(\d+)")


def run(target_root: Path, severity: str | None = "HIGH") -> AuditResult:
    """Run the PhD audit against a target project.

    severity: "HIGH" (only HIGH), "MEDIUM" (HIGH+MEDIUM), None (all)
    """
    from audit_code import audit_phd  # late import — audit_phd is heavy

    # Reset ROOT — module-level state persists across calls in-process
    audit_phd.ROOT = target_root.resolve()

    saved_argv = sys.argv[:]
    buf = io.StringIO()
    try:
        sys.argv = [
            "audit_phd",
            "--path",
            str(target_root),
        ]
        if severity:
            sys.argv.append(f"--min-severity={severity}")

        with redirect_stdout(buf):
            audit_phd.main()
    except Exception as exc:
        return AuditResult(
            audit_id="phd",
            status=AuditStatus.CRASH,
            stderr=f"audit_phd.main() raised: {exc}",
        )
    finally:
        sys.argv = saved_argv

    out = buf.getvalue()
    high = med = info = 0
    m = SUMMARY_RE.search(out)
    if m:
        high, med, info = int(m.group(1)), int(m.group(2)), int(m.group(3))

    if "SUMMARY" not in out:
        return AuditResult(
            audit_id="phd",
            status=AuditStatus.CRASH,
            stdout=out,
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
        completed=True,
    )
