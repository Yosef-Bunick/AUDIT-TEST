"""deps.py — dependency scanner. Now calls audit_deps.main() directly."""

from pathlib import Path

from audit_code.audit_shared import thread_capture_stdout
from audit_code.models import (
    AuditResult,
    AuditStatus,
)


def run(target_root: Path, req: bool = False) -> AuditResult:
    """Scan dependencies. Prints report always; writes .requirements only with req=True."""
    import audit_code.audit_deps as audit_deps

    audit_deps.ROOT = target_root.resolve()

    # explicit argv + thread-local capture: deps runs in a background thread
    # concurrently with the wiring/phd/runtime wrappers — mutating the global
    # sys.argv/sys.stdout here corrupted THEIR runs intermittently
    argv = ["audit_deps", "--path", str(target_root)]
    if not req:
        argv.append("--print")
    try:
        with thread_capture_stdout() as buf:
            audit_deps.main(argv)
    except Exception:
        import traceback

        return AuditResult(
            audit_id="deps",
            status=AuditStatus.CRASH,
            stderr=f"audit_deps.main() raised:\n{traceback.format_exc()}",
        )

    out = buf.getvalue()
    return AuditResult(
        audit_id="deps",
        status=AuditStatus.PASS if "ERROR" not in out else AuditStatus.ERROR,
        stdout=out,
    )
