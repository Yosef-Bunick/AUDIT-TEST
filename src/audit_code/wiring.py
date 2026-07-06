"""wiring.py — deep wiring audit (dead symbols, test-only, config drift).

Answers "is it connected?" via AST analysis:
  CHECK 1 - DEAD SYMBOLS (zero references anywhere)
  CHECK 2 - TEST-ONLY SYMBOLS (referenced only from tests/)
  CHECK 3 - DEAD CONFIG KEYS (quoted-exact matching)
  CHECK 4 - CONFIG-KEY FLOW (definers vs consumers)
  CHECK 7 - SHADOWED CONFIG (key in multiple files)
  CHECK 8 - TRANSITIVELY DEAD CONFIG
  CHECK 9 - STDOUT PROTOCOL (__EVENT__/__RESULT__ markers)
"""

import json
import re
import subprocess
import sys
import tempfile
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
    """Run the wiring audit against a target project."""
    try:
        proc = subprocess.run(
            [sys.executable, str(_SCRIPT), "--path", str(target_root)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            cwd=str(target_root),
            env=utf8_subprocess_env(),
        )
    except subprocess.TimeoutExpired:
        return AuditResult(
            audit_id="wiring",
            status=AuditStatus.CRASH,
            stderr="timed out after 300s",
        )

    out = proc.stdout or ""
    err = proc.stderr or ""

    high = med = info = 0
    m = SUMMARY_RE.search(out)
    if m:
        high, med, info = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = HIGH_RE.search(out)
        if m:
            high = int(m.group(1))

    crashed = proc.returncode not in (0, 1) or (
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
            stderr=err,
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
        stderr=err,
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
        return data.get("dead", [])
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return []
    finally:
        tmp_path.unlink(missing_ok=True)
