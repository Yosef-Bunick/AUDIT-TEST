"""MegaLinter integration — multi-language meta-linter.

Opt-in via ``--megalinter`` (never auto-dispatched: it is Docker-based and
slow, so it only runs when named explicitly). Uses whichever CLI is on PATH:
the official npm wrapper ``mega-linter-runner``, or a bare ``megalinter``
binary. Missing tool returns an honest SKIP via ``_run_tool``.
"""

import shutil
from pathlib import Path

from audit_code.models import AuditResult

from ._tool_runner import _run_tool


def run(target_root: Path, timeout: int = 600) -> AuditResult:
    """Run MegaLinter against the target project."""
    exe = "mega-linter-runner" if shutil.which("mega-linter-runner") else "megalinter"
    return _run_tool(
        exe,
        [exe],
        "megalinter",
        target_root,
        timeout,
        violation_codes=None,
    )
