"""runtime.py — operational failure modes.

Answers "will it hang, crash on another machine, or run with the wrong brain?"
Now calls audit_runtime.main() directly instead of subprocess.
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


def run(target_root: Path, strict: bool = True) -> AuditResult:
    """Run the runtime audit against a target project."""
    import audit_code.audit_runtime as audit_runtime
    audit_runtime.ROOT = target_root.resolve()

    saved_argv = sys.argv[:]
    buf = io.StringIO()
    try:
        sys.argv = [
            "audit_runtime",
            "--path",
            str(target_root),
        ]

        with redirect_stdout(buf):
            audit_runtime.main()
    except Exception as exc:
        return AuditResult(
            audit_id="runtime",
            status=AuditStatus.CRASH,
            stderr=f"audit_runtime.main() raised: {exc}",
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
            audit_id="runtime",
            status=AuditStatus.CRASH,
            stdout=out,
        )

    status = (
        AuditStatus.FAIL
        if high
        else (AuditStatus.WARN if (med or info) else AuditStatus.PASS)
    )

    return AuditResult(
        audit_id="runtime",
        status=status,
        high=high,
        medium=med,
        info=info,
        stdout=out,
        completed=True,
    )
