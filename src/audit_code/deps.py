"""deps.py — dependency scanner.

Auto-generates .requirements from the codebase:
  - Adds new third-party packages found in imports
  - Removes entries whose imports no longer exist
  - Preserves manual entries (Python version, system deps, comments)
"""

import subprocess
import sys
from pathlib import Path

from audit_code.audit_shared import utf8_subprocess_env
from audit_code.models import (
    AuditResult,
    AuditStatus,
)

_SCRIPT = Path(__file__).resolve().parent / "audit_deps.py"


def run(target_root: Path, print_only: bool = False) -> AuditResult:
    """Scan dependencies and update .requirements."""
    cmd = [sys.executable, str(_SCRIPT), "--path", str(target_root)]
    if print_only:
        cmd.append("--print")

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            cwd=str(target_root),
            env=utf8_subprocess_env(),
        )
    except subprocess.TimeoutExpired:
        return AuditResult(
            audit_id="deps",
            status=AuditStatus.CRASH,
            stderr="timed out after 60s",
        )

    return AuditResult(
        audit_id="deps",
        status=AuditStatus.PASS if proc.returncode == 0 else AuditStatus.ERROR,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
    )
