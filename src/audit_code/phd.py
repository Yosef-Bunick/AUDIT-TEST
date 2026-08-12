"""phd.py — PhD-standard static review.

Answers "does it meet the bar?" — exception discipline, security patterns,
state management, complexity, test smells, and documentation coverage.

Now calls audit_phd.main() directly instead of subprocess — eliminates
Python startup overhead (~15% of audit runtime).
"""

import re
from pathlib import Path

from audit_code.audit_shared import thread_capture_stdout
from audit_code.models import (
    AuditResult,
    AuditStatus,
    findings_from_tuples,
)

SUMMARY_RE = re.compile(r"SUMMARY\s+HIGH:\s*(\d+)\s+MEDIUM:\s*(\d+)\s+INFO:\s*(\d+)")


def run(target_root: Path, severity: str | None = "HIGH") -> AuditResult:
    """Run the PhD audit against a target project.

    severity: "HIGH" (only HIGH), "MEDIUM" (HIGH+MEDIUM), None (all)
    """
    from audit_code import audit_phd  # late import — audit_phd is heavy

    # Reset ROOT — module-level state persists across calls in-process
    audit_phd.ROOT = target_root.resolve()

    # explicit argv + thread-local capture: background wrapper threads (deps,
    # linters) must not see our flags or steal/leak our stdout mid-run
    argv = ["audit_phd", "--path", str(target_root)]
    if severity:
        argv.append(f"--min-severity={severity}")
    try:
        with thread_capture_stdout() as buf:
            audit_phd.main(argv)
    except Exception:
        import traceback

        return AuditResult(
            audit_id="phd",
            status=AuditStatus.CRASH,
            stderr=f"audit_phd.main() raised:\n{traceback.format_exc()}",
        )

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
            stderr=(
                f"phd output has no SUMMARY line ({len(out)} bytes "
                f"captured; tail: {out.strip()[-120:]!r})"
            ),
        )

    status = (
        AuditStatus.FAIL
        if high
        else (AuditStatus.WARN if (med or info) else AuditStatus.PASS)
    )

    return AuditResult(
        audit_id="phd",
        status=status,
        findings=findings_from_tuples(audit_phd.LAST_FINDINGS, "phd", severity),
        high=high,
        medium=med,
        info=info,
        stdout=out,
        completed=True,
    )
