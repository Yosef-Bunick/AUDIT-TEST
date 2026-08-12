"""runtime.py — operational failure modes.

Answers "will it hang, crash on another machine, or run with the wrong brain?"
Now calls audit_runtime.main() directly instead of subprocess.
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


def run(target_root: Path, strict: bool = True) -> AuditResult:
    """Run the runtime audit against a target project."""
    import audit_code.audit_runtime as audit_runtime

    audit_runtime.ROOT = target_root.resolve()

    # explicit argv + thread-local capture: background wrapper threads (deps,
    # linters) must not see our flags or steal/leak our stdout mid-run
    try:
        with thread_capture_stdout() as buf:
            audit_runtime.main(["audit_runtime", "--path", str(target_root)])
    except Exception:
        import traceback

        return AuditResult(
            audit_id="runtime",
            status=AuditStatus.CRASH,
            stderr=f"audit_runtime.main() raised:\n{traceback.format_exc()}",
        )

    out = buf.getvalue()
    high = med = info = 0
    m = SUMMARY_RE.search(out)
    if m:
        high, med, info = int(m.group(1)), int(m.group(2)), int(m.group(3))

    if "SUMMARY" not in out:
        return AuditResult(
            audit_id="runtime",
            status=AuditStatus.CRASH,
            stdout=out,
            stderr=(
                f"runtime output has no SUMMARY line ({len(out)} bytes "
                f"captured; tail: {out.strip()[-120:]!r})"
            ),
        )

    status = (
        AuditStatus.FAIL
        if high
        else (AuditStatus.WARN if (med or info) else AuditStatus.PASS)
    )

    return AuditResult(
        audit_id="runtime",
        status=status,
        findings=findings_from_tuples(audit_runtime.LAST_FINDINGS, "runtime"),
        high=high,
        medium=med,
        info=info,
        stdout=out,
        completed=True,
    )
