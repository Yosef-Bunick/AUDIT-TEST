"""gate.py — the per-CHANGE verdict.

Judges ONLY the working-tree diff vs HEAD through four gates:
  G0  SYNTAX              changed files must parse
  G1  STATIC REGRESSION   no new HIGH findings vs HEAD
  G2  SUITE GREEN         full test suite passes
  G3  EXECUTION PROOF     every changed def + line executes under tests
  G4  MUTATION KILL       injected bugs in changed lines must be caught
"""

import subprocess
import sys
from pathlib import Path

from audit_code.audit_shared import utf8_subprocess_env

_SCRIPT = Path(__file__).resolve().parent / "audit_gate.py"


def run_gate(
    target_root: Path,
    fast: bool = False,
    no_static: bool = False,
    kill_pct: int = 60,
    severity: str | None = "HIGH",
    verbose: bool = False,
) -> int:
    """Run the change gate. Returns exit code (0=pass, 1=fail, 2=nothing to judge)."""
    cmd = [
        sys.executable,
        str(_SCRIPT),
        "--path",
        str(target_root),
        "--kill",
        str(kill_pct),
    ]
    if fast:
        cmd.append("--fast")
    if no_static:
        cmd.append("--no-static")
    if severity == "MEDIUM":
        cmd.append("--medium")
    elif severity is None:
        cmd.append("--info")
    if verbose:
        cmd.append("--verbose")

    proc = subprocess.run(
        cmd,
        capture_output=False,  # stream to terminal so user sees gate progress
        cwd=str(target_root),
        timeout=3600,
        env=utf8_subprocess_env(),
    )
    return proc.returncode
