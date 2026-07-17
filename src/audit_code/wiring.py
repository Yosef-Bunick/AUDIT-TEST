"""wiring.py — deep wiring audit (dead symbols, test-only, config drift).

Answers "is it connected?" via AST analysis.
Now calls audit_wiring.main() directly in run() — eliminates Python
startup overhead. collect_dead_symbols() still subprocesses for the
profiler (lower priority path).
"""

import io
import json
import re
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

from audit_code.audit_shared import utf8_subprocess_env
from audit_code.models import (
    AuditResult,
    AuditStatus,
)

_SCRIPT = Path(__file__).resolve().parent / "audit_wiring.py"

HIGH_RE = re.compile(r"HIGH-confidence findings:\s*(\d+)")
SUMMARY_RE = re.compile(r"SUMMARY\s+HIGH:\s*(\d+)\s+MEDIUM:\s*(\d+)\s+INFO:\s*(\d+)")


def run(target_root: Path, strict: bool = True) -> AuditResult:
    """Run the wiring audit against a target project.

    Calls audit_wiring.main() directly instead of subprocess.
    """
    from audit_code import audit_wiring  # late import — heavy

    # Reset ROOT — module-level state persists across calls in-process
    audit_wiring.ROOT = target_root.resolve()

    saved_argv = sys.argv[:]
    buf = io.StringIO()
    try:
        sys.argv = [
            "audit_wiring",
            "--path",
            str(target_root),
        ]

        with redirect_stdout(buf):
            audit_wiring.main()
    except Exception as exc:
        return AuditResult(
            audit_id="wiring",
            status=AuditStatus.CRASH,
            stderr=f"audit_wiring.main() raised: {exc}",
        )
    finally:
        sys.argv = saved_argv

    out = buf.getvalue()
    high = med = info = 0
    m = SUMMARY_RE.search(out)
    if m:
        high, med, info = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = HIGH_RE.search(out)
        if m:
            high = int(m.group(1))

    crashed = (
        high == 0
        and med == 0
        and info == 0
        and "SUMMARY" not in out
        and "HIGH-confidence" not in out
    )

    if crashed:
        return AuditResult(
            audit_id="wiring",
            status=AuditStatus.CRASH,
            stdout=out,
        )

    status = (
        AuditStatus.FAIL
        if high
        else (AuditStatus.WARN if (med or info) else AuditStatus.PASS)
    )

    return AuditResult(
        audit_id="wiring",
        status=status,
        high=high,
        medium=med,
        info=info,
        stdout=out,
        completed=True,
    )


def collect_dead_symbols(target_root: Path) -> list[dict]:
    """Return the wiring audit's dead symbols as ``[{name, file, line, kind}]``.

    Runs the wiring script with ``--dead-json`` pointed at a temp file and reads
    the structured result back — the same subprocess contract as :func:`run`, so
    the target's own environment/encoding is honoured.
    """
    target_root = Path(target_root)
    with tempfile.NamedTemporaryFile(
        "r", suffix=".json", delete=False, encoding="utf-8"
    ) as tmp:
        tmp_path = Path(tmp.name)
    try:
        subprocess.run(
            [
                sys.executable,
                str(_SCRIPT),
                "--path",
                str(target_root),
                "--dead-json",
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            cwd=str(target_root),
            env=utf8_subprocess_env(),
        )
        data = json.loads(tmp_path.read_text(encoding="utf-8"))
        # audit_wiring writes {"dead": [...], "test_only": [...]} —
        # consumers want just the dead list
        if isinstance(data, dict) and "dead" in data:
            return data["dead"]
        return data
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
