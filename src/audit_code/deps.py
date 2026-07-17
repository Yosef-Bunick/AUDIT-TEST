"""deps.py — dependency scanner. Now calls audit_deps.main() directly."""

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

from audit_code.models import (
    AuditResult,
    AuditStatus,
)


def run(target_root: Path, print_only: bool = False) -> AuditResult:
    """Scan dependencies and update .requirements."""
    import audit_code.audit_deps as audit_deps

    audit_deps.ROOT = target_root.resolve()

    saved_argv = sys.argv[:]
    buf = io.StringIO()
    try:
        sys.argv = [
            "audit_deps",
            "--path",
            str(target_root),
        ]
        if print_only:
            sys.argv.append("--print")

        with redirect_stdout(buf):
            audit_deps.main()
    except Exception as exc:
        return AuditResult(
            audit_id="deps",
            status=AuditStatus.CRASH,
            stderr=f"audit_deps.main() raised: {exc}",
        )
    finally:
        sys.argv = saved_argv

    out = buf.getvalue()
    return AuditResult(
        audit_id="deps",
        status=AuditStatus.PASS if "ERROR" not in out else AuditStatus.ERROR,
        stdout=out,
    )
